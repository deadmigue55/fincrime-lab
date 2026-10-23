"""Genuine (non-fraud) activity of the synthetic bank, as the *latent* event stream.

Each event is something a customer intended to do. Fraud is added later (``attacks``), and a
policy layer decides which events are approved. Events are generated in chunks of customers so
memory stays flat at any scale, and each chunk uses its own random stream.

Timestamps are naive local Mexico City time. Double entry: a transfer between two customers of
the bank appears twice, as ``spei_out`` for the sender and ``spei_in`` for the receiver.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.customers import rng_for

CHUNK_CUSTOMERS = 25_000
MIRROR_OFFSET = 1 << 50  # event_id of the receiving side of an internal transfer
SEQ_BITS = 20  # event_id = customer_id << 20 | sequence (up to ~1M events per customer)

EVENT_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Int64,
    "ts": pl.Datetime("us"),
    "account_id": pl.Int64,
    "event_type": pl.String,
    "channel": pl.String,
    "direction": pl.String,
    "amount": pl.Float64,
    "counterparty_kind": pl.String,
    "counterparty_account": pl.Int64,
    "counterparty_bank": pl.String,
    "mcc_group": pl.String,
    "device_id": pl.Int64,
    "is_fraud": pl.Boolean,
    "fraud_type": pl.String,
    "episode_id": pl.Int64,
}

# type -> (channel, direction, counterparty kind, device used?)
TYPE_INFO: dict[str, tuple[str, str, str, bool]] = {
    "spei_out": ("app", "out", "person", True),
    "spei_in": ("app", "in", "person", False),
    "card_pos": ("card_pos", "out", "merchant", False),
    "card_online": ("card_online", "out", "merchant", True),
    "cash_in": ("store", "in", "store", False),
    "atm": ("atm", "out", "atm", False),
    "codi": ("codi", "out", "merchant", True),
    "bill_pay": ("app", "out", "biller", True),
    "payroll": ("spei", "in", "employer", False),
}

# Share of events by hour of day (assumption, documented in docs/assumptions.md).
_HOURS = np.arange(24)


def _bump(center: float, width: float) -> np.ndarray:
    return np.exp(-0.5 * ((_HOURS - center) / width) ** 2)


def _pmf(*bumps: tuple[float, float, float], floor: float = 0.01) -> np.ndarray:
    raw = sum(w * _bump(c, s) for w, c, s in bumps) + floor
    return raw / raw.sum()


HOUR_PMF: dict[str, np.ndarray] = {
    "spei_out": _pmf((1.0, 13, 4.5), (0.5, 20, 2.5)),
    "spei_in": _pmf((1.0, 13, 5.0), (0.4, 20, 2.5)),
    "card_pos": _pmf((1.0, 14, 4.0), (0.6, 19, 2.0)),
    "card_online": _pmf((0.8, 14, 4.0), (1.0, 21, 3.0), floor=0.03),
    "cash_in": _pmf((1.0, 13, 3.5)),
    "atm": _pmf((1.0, 13, 4.0), (0.5, 19, 2.0)),
    "codi": _pmf((1.0, 14, 4.0), (0.6, 19, 2.0)),
    "bill_pay": _pmf((1.0, 11, 4.0), (0.5, 20, 2.5)),
    "payroll": _pmf((1.0, 3, 2.0), floor=0.002),
}


@dataclass(frozen=True)
class Calendar:
    """Day-level facts shared by all chunks."""

    start: date
    days: int
    weights: np.ndarray  # relative weight of each day for non-payroll activity
    cdf: np.ndarray
    cdf_prev: np.ndarray

    @classmethod
    def build(cls, a: Assumptions) -> Calendar:
        days = (a.end_date - a.start_date).days + 1
        w = np.empty(days)
        for i in range(days):
            d = a.start_date + timedelta(days=i)
            boost = d.day in (1, 2, 15, 16, 17) or d.day >= monthrange(d.year, d.month)[1] - 1
            w[i] = a.weekday_weights[d.weekday()] * (a.payday_boost if boost else 1.0)
        cdf = np.cumsum(w) / w.sum()
        return cls(a.start_date, days, w, cdf, np.concatenate([[0.0], cdf[:-1]]))


def pay_day_indices(a: Assumptions, cal: Calendar) -> tuple[np.ndarray, np.ndarray]:
    """Day indices of last-day-of-month pay days, and of the 15th (biweekly extra)."""
    last, mid = [], []
    for i in range(cal.days):
        d = a.start_date + timedelta(days=i)
        if d.day == monthrange(d.year, d.month)[1]:
            last.append(i)
        elif d.day == 15:
            mid.append(i)
    return np.array(last), np.array(mid)


def _day_index(frame: pl.DataFrame, cal: Calendar) -> np.ndarray:
    """Days from the calendar start to each customer's onboarding date."""
    days = frame.select((pl.col("onboarded_on") - pl.lit(cal.start)).dt.total_days())
    return days.to_series().to_numpy()


def _ts(cal: Calendar, day: np.ndarray, hour: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    seconds = rng.integers(0, 3600, size=len(day))
    base = np.datetime64(cal.start, "s") + (day * 86_400).astype("timedelta64[s]")
    return base + (hour * 3600 + seconds).astype("timedelta64[s]")


def _external_bank_probs(n_banks: int) -> np.ndarray:
    w = 1.0 / np.arange(1, n_banks + 1)  # Zipf: a few big banks, a long tail
    return w / w.sum()


def _chunk_events(
    a: Assumptions,
    cal: Calendar,
    cust: pl.DataFrame,
    n_onboarded_by_day: np.ndarray,
    chunk_idx: int,
) -> pl.DataFrame:
    """All genuine events for one chunk of customers (including mirrored receiving sides)."""
    rng = rng_for(a.seed, f"events-{chunk_idx}")
    cid = cust["customer_id"].to_numpy()
    onb = _day_index(cust, cal)
    income = cust["monthly_income"].to_numpy()
    activity = cust["activity"].to_numpy()
    n_dev = cust["n_devices"].to_numpy()
    parts: list[pl.DataFrame] = []
    banks = np.array([f"B{i:02d}" for i in range(1, a.n_external_banks + 1)])
    bank_p = _external_bank_probs(a.n_external_banks)

    def finish(etype: str, idx: np.ndarray, ts: np.ndarray, amount: np.ndarray) -> pl.DataFrame:
        channel, direction, kind, uses_device = TYPE_INFO[etype]
        m = len(idx)
        account = cid[idx]
        device = np.full(m, -1, dtype=np.int64)
        if uses_device:
            second = (n_dev[idx] == 2) & (rng.random(m) < a.second_device_use)
            device = account * 10 + np.where(second, 2, 1)
        mcc = None
        if etype == "card_pos":
            mix = a.mcc_mix_pos
        elif etype == "card_online":
            mix = a.mcc_mix_online
        else:
            mix = None
        if mix is not None:
            names = np.array(list(mix))
            p = np.array(list(mix.values()))
            mcc = names[rng.choice(len(names), size=m, p=p / p.sum())]
        if etype == "atm":
            amount = np.maximum(100.0, np.round(amount / 100.0) * 100.0)
        frame = pl.DataFrame(
            {
                "ts": ts.astype("datetime64[us]"),
                "account_id": account,
                "event_type": np.full(m, etype),
                "channel": np.full(m, channel),
                "direction": np.full(m, direction),
                "amount": np.round(np.maximum(amount, 10.0), 2),
                "counterparty_kind": np.full(m, kind),
                "counterparty_account": np.full(m, -1, dtype=np.int64),
                "counterparty_bank": np.full(m, ""),
                "mcc_group": mcc if mcc is not None else np.full(m, ""),
                "device_id": device,
            }
        )
        return frame

    # --- regular activity -------------------------------------------------------------------
    for etype, per_month in a.events_per_month.items():
        lam = per_month * 12 / 365.25 * (cal.days - onb) * activity
        counts = rng.poisson(lam)
        idx = np.repeat(np.arange(len(cid)), counts)
        if len(idx) == 0:
            continue
        lo = cal.cdf_prev[onb[idx]]
        u = lo + rng.random(len(idx)) * (1.0 - lo)
        day = np.minimum(np.searchsorted(cal.cdf, u, side="right"), cal.days - 1)
        hour = rng.choice(24, size=len(idx), p=HOUR_PMF[etype])
        ts = _ts(cal, day, hour, rng)
        median = income[idx] * a.amount_median_frac[etype]
        amount = median * np.exp(a.amount_sigma[etype] * rng.normal(size=len(idx)))
        frame = finish(etype, idx, ts, amount)
        frame = frame.with_columns(day=pl.Series(day.astype(np.int64)))
        parts.append(frame)

    # --- payroll ----------------------------------------------------------------------------
    last, mid = pay_day_indices(a, cal)
    salaried = cust["salaried"].to_numpy()
    biweekly = cust["biweekly"].to_numpy()
    scale = np.exp(rng.normal(0.0, 0.05, len(cid)))  # employer-specific pay level
    for days_idx, who in ((last, salaried), (mid, biweekly)):
        for d in days_idx:
            idx = np.nonzero(who & (onb + 3 <= d))[0]
            if len(idx) == 0:
                continue
            share = np.where(biweekly[idx], 0.5, 1.0)  # half each quincena
            amount = income[idx] * scale[idx] * share
            hour = rng.choice(24, size=len(idx), p=HOUR_PMF["payroll"])
            ts = _ts(cal, np.full(len(idx), d), hour, rng)
            frame = finish("payroll", idx, ts, amount).with_columns(
                day=pl.lit(int(d), dtype=pl.Int64)
            )
            parts.append(frame)

    events = pl.concat(parts, how="vertical")

    # --- counterparties ---------------------------------------------------------------------
    n = events.height
    etype = events["event_type"].to_numpy()
    account = events["account_id"].to_numpy()
    day = events["day"].to_numpy()
    cp_account = np.full(n, -1, dtype=np.int64)
    cp_bank = np.full(n, "", dtype=object)

    out = etype == "spei_out"
    internal = out & (rng.random(n) < a.internal_transfer_share)
    count = n_onboarded_by_day[day[internal]]
    pick = 1 + (rng.random(internal.sum()) * count).astype(np.int64)
    pick = np.where(pick == account[internal], pick % np.maximum(count, 1) + 1, pick)
    cp_account[internal] = pick
    external = (out & ~internal) | (etype == "spei_in") | (etype == "payroll")
    cp_bank[external] = banks[rng.choice(len(banks), size=external.sum(), p=bank_p)]

    kind = events["counterparty_kind"].to_numpy().astype(object)
    kind[internal] = "customer"
    event_id = (account << SEQ_BITS) | _rank_within(account)

    events = events.with_columns(
        pl.Series("event_id", event_id),
        pl.Series("counterparty_account", cp_account),
        pl.Series("counterparty_bank", cp_bank.astype(str)),
        pl.Series("counterparty_kind", kind.astype(str)),
    ).drop("day")

    # --- mirrored receiving side of internal transfers --------------------------------------
    sent = events.filter(pl.col("counterparty_account") > 0).filter(
        pl.col("event_type") == "spei_out"
    )
    mirror = sent.with_columns(
        event_id=pl.col("event_id") + MIRROR_OFFSET,
        account_id=pl.col("counterparty_account"),
        counterparty_account=pl.col("account_id"),
        event_type=pl.lit("spei_in"),
        direction=pl.lit("in"),
        device_id=pl.lit(-1, dtype=pl.Int64),
    )
    events = pl.concat([events, mirror], how="vertical")

    # --- finalize: nulls, latent columns, schema --------------------------------------------
    events = events.with_columns(
        pl.when(pl.col("counterparty_account") < 0)
        .then(None)
        .otherwise(pl.col("counterparty_account"))
        .alias("counterparty_account"),
        pl.when(pl.col("counterparty_bank") == "")
        .then(None)
        .otherwise(pl.col("counterparty_bank"))
        .alias("counterparty_bank"),
        pl.when(pl.col("mcc_group") == "")
        .then(None)
        .otherwise(pl.col("mcc_group"))
        .alias("mcc_group"),
        pl.when(pl.col("device_id") < 0)
        .then(None)
        .otherwise(pl.col("device_id"))
        .alias("device_id"),
        is_fraud=pl.lit(False),
        fraud_type=pl.lit(None, dtype=pl.String),
        episode_id=pl.lit(None, dtype=pl.Int64),
    )
    return events.select(list(EVENT_SCHEMA)).cast(EVENT_SCHEMA)


def _rank_within(group: np.ndarray) -> np.ndarray:
    """0, 1, 2, ... within each group value, in the order rows appear."""
    order = np.argsort(group, kind="stable")
    sorted_group = group[order]
    start = np.concatenate([[True], sorted_group[1:] != sorted_group[:-1]])
    idx = np.arange(len(group))
    first_pos = np.maximum.accumulate(np.where(start, idx, 0))
    rank_sorted = idx - first_pos
    rank = np.empty(len(group), dtype=np.int64)
    rank[order] = rank_sorted
    return rank


def iter_genuine_events(
    a: Assumptions, customers: pl.DataFrame, profiles: pl.DataFrame
) -> Iterator[pl.DataFrame]:
    """Yield the genuine events chunk by chunk (chunks are not globally time-ordered)."""
    cal = Calendar.build(a)
    joined = customers.join(profiles, on="customer_id")
    onb_days = _day_index(customers, cal)
    # number of customers already onboarded on each calendar day (ids grow with join date)
    n_onboarded_by_day = np.searchsorted(onb_days, np.arange(cal.days), side="right")
    for i, start in enumerate(range(0, joined.height, CHUNK_CUSTOMERS)):
        yield _chunk_events(a, cal, joined.slice(start, CHUNK_CUSTOMERS), n_onboarded_by_day, i)
