"""From the tables of each simulated arm to per-customer outcome matrices.

Every analysis in this module works on three things: the eligible customers, their pre-treatment
covariates (always measured in the baseline bank, before the new rules start), and the outcomes
each customer would have under each rule set. :class:`Bench` holds them as dense arrays so that
thousands of random assignments can be scored with a few matrix products.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.experiments import worlds
from fincrime.risk import claims as cl
from fincrime.risk import delay

METRICS = ("loss", "claims", "ato_loss", "ct_loss", "friction", "stepups")
LABELS = {
    "loss": "P1 fraud loss (pesos per customer)",
    "claims": "S1 fraud claims per customer",
    "ato_loss": "S2 takeover loss",
    "ct_loss": "S3 card-testing loss",
    "friction": "G1 customers with a failed verification or decline",
    "stepups": "G2 step-ups per customer",
}
XNAMES = [
    "log pre outgoing count",
    "log pre outgoing pesos",
    "pre failed",
    "pre step-ups",
    "tenure",
]
WEEKS = 25
T0 = datetime(worlds.START.year, worlds.START.month, worlds.START.day)
T_END = datetime(worlds.END.year, worlds.END.month, worlds.END.day, 23, 59, 59)


def week_ends(weeks: int = WEEKS) -> list[datetime]:
    """End of each whole week of the experiment: the instants at which it is read."""
    return [T0 + timedelta(days=7 * k) for k in range(1, weeks + 1)]


@dataclass
class Arm:
    """Everything one rule set did to the eligible customers."""

    name: str
    y: np.ndarray  # (n, len(METRICS)) outcomes over the whole window
    fraud: pl.DataFrame  # one row per approved fraud event, with its row index ``i``
    weekly: dict[str, np.ndarray]  # cumulative (n, WEEKS) outcome tables, by readout basis
    volume: np.ndarray  # (n, 2): approved genuine outgoing count and pesos in the window
    failed: np.ndarray  # (n,) failed verifications and declines of genuine attempts in the window


@dataclass
class Bench:
    """The eligible customers, their pre-period values and the outcomes under every arm."""

    ids: np.ndarray
    x: np.ndarray  # (n, 5) covariates for the regression-adjusted estimator
    pre: np.ndarray  # (n, len(METRICS)) the pre-period value of each metric, for CUPED
    tenure: np.ndarray
    arms: dict[str, Arm]

    @property
    def n(self) -> int:
        return len(self.ids)

    def potential(self, treatment: str, columns: list[int] | None = None) -> tuple[np.ndarray, ...]:
        """``(y_control, y_treated)`` with one row per customer, the truth of both worlds."""
        a, b = self.arms["baseline"].y, self.arms[treatment].y
        if columns is not None:
            a, b = a[:, columns], b[:, columns]
        return a, b

    def truth(self, treatment: str) -> np.ndarray:
        """True average effect on every metric: the mean over all customers of ``Y(B) - Y(A)``."""
        a, b = self.potential(treatment)
        return (b - a).mean(axis=0)


def _index(ids: np.ndarray, customer: pl.Series) -> np.ndarray:
    """Row of each customer id in ``ids`` (which is sorted), -1 for a customer not eligible."""
    c = customer.to_numpy()
    pos = np.searchsorted(ids, c)
    pos = np.clip(pos, 0, len(ids) - 1)
    return np.where(ids[pos] == c, pos, -1)


def _accumulate(n: int, row: np.ndarray, value: np.ndarray, when: np.ndarray, ends) -> np.ndarray:
    """Cumulative table: ``out[i, k]`` is the sum of ``value`` of customer i's events by look k."""
    table = np.zeros((n, len(ends)))
    ends_np = np.asarray([np.datetime64(e) for e in ends])
    look = np.searchsorted(ends_np, when, side="left")  # first look whose end is at or after
    ok = (row >= 0) & (look < len(ends))
    np.add.at(table, (row[ok], look[ok]), value[ok])
    return np.cumsum(table, axis=1)


def _metrics(
    ids: np.ndarray, fraud: pl.DataFrame, genuine: pl.DataFrame, lo: datetime, hi: datetime
) -> tuple[np.ndarray, np.ndarray]:
    """The six per-customer metrics over ``[lo, hi)``, and the count of failed verifications."""
    n = len(ids)
    f = fraud.filter((pl.col("ts") >= lo) & (pl.col("ts") < hi))
    row = _index(ids, f["customer_id"])
    ok = row >= 0
    amount = f["amount"].to_numpy()
    y = np.zeros((n, len(METRICS)))
    np.add.at(y[:, 0], row[ok], amount[ok])
    ep = _index(ids, f.group_by("episode_id").agg(pl.col("customer_id").first())["customer_id"])
    np.add.at(y[:, 1], ep[ep >= 0], 1.0)
    for col, kind in ((2, "ato"), (3, "card_testing")):
        sel = (f["fraud_type"] == kind).to_numpy() & ok
        np.add.at(y[:, col], row[sel], amount[sel])
    g = genuine.filter((pl.col("ts") >= lo) & (pl.col("ts") < hi))
    gi = _index(ids, g["customer_id"])
    failed = g["status"].is_in(["declined", "stepup_failed"]).to_numpy() & (gi >= 0)
    stepped = (g["decision"] == "step_up").to_numpy() & (gi >= 0)
    n_failed = np.zeros(n)
    np.add.at(n_failed, gi[failed], 1.0)
    y[:, 4] = n_failed > 0
    np.add.at(y[:, 5], gi[stepped], 1.0)
    return y, n_failed


def _volume(n: int, volume: pl.DataFrame, ids: np.ndarray, in_window: bool) -> np.ndarray:
    v = volume.filter(pl.col("in_window") == in_window)
    out = np.zeros((n, 2))
    i = _index(ids, v["customer_id"])
    ok = i >= 0
    out[i[ok], 0] = v["n_out"].to_numpy()[ok]
    out[i[ok], 1] = v["pesos_out"].to_numpy()[ok]
    return out


def load_arm(path: Path, ids: np.ndarray) -> Arm:
    """Read one arm's tables (``worlds.extract``) into per-customer arrays."""
    fraud = pl.read_parquet(path / "fraud.parquet").filter(pl.col("ts") >= T0)
    genuine = pl.read_parquet(path / "genuine.parquet")
    volume = pl.read_parquet(path / "volume.parquet")
    n = len(ids)
    row = _index(ids, fraud["customer_id"])
    fraud = fraud.with_columns(i=pl.Series(row))
    ends = week_ends()
    amount = fraud["amount"].to_numpy()
    when = fraud["ts"].to_numpy()
    rep = fraud["reported_ts"].to_numpy()
    seen = ~np.isnat(rep)
    weekly = {
        "loss": _accumulate(n, row, amount, when, ends),
        "loss_reported": _accumulate(n, row[seen], amount[seen], rep[seen], ends),
        # losses by loss date that will eventually be reported: what a delay-model readout targets
        "loss_ultimate": _accumulate(n, row[seen], amount[seen], when[seen], ends),
    }
    g = genuine.filter(pl.col("ts") >= T0)
    gi = _index(ids, g["customer_id"])
    ts = g["ts"].to_numpy()
    failed = g["status"].is_in(["declined", "stepup_failed"]).to_numpy()
    stepped = (g["decision"] == "step_up").to_numpy()
    weekly["friction"] = np.minimum(
        _accumulate(n, gi[failed], np.ones(failed.sum()), ts[failed], ends), 1.0
    )
    weekly["stepups"] = _accumulate(n, gi[stepped], np.ones(stepped.sum()), ts[stepped], ends)
    y, failed = _metrics(ids, fraud, genuine, T0, T_END + timedelta(seconds=1))
    return Arm(
        name=path.name,
        y=y,
        failed=failed,
        fraud=fraud,
        weekly=weekly,
        volume=_volume(n, volume, ids, True),
    )


def load(root: Path, bank_dir: Path, treatments: list[str] | None = None) -> Bench:
    """Read the baseline and every built treatment arm, plus the pre-period covariates."""
    cust = worlds.customers_table(bank_dir)
    ids = cust["customer_id"].to_numpy()
    n = len(ids)
    base = load_arm(root / "baseline", ids)
    fraud_all = pl.read_parquet(root / "baseline" / "fraud.parquet")
    genuine = pl.read_parquet(root / "baseline" / "genuine.parquet")
    volume = pl.read_parquet(root / "baseline" / "volume.parquet")
    pre, pre_failed = _metrics(ids, fraud_all, genuine, datetime(2000, 1, 1), T0)
    vol = _volume(n, volume, ids, False)
    tenure = cust["tenure_days"].to_numpy()
    pre_stepups = pre[:, 5]
    x = np.column_stack([np.log1p(vol[:, 0]), np.log1p(vol[:, 1]), pre_failed, pre_stepups, tenure])
    names = treatments or list(worlds.TREATMENTS)
    arms = {"baseline": base} | {t: load_arm(root / t, ids) for t in names}
    return Bench(ids=ids, x=x, pre=pre, tenure=tenure, arms=arms)


def completion_fits(bank_dir: Path, as_of: date) -> dict:
    """Delay model per fraud type, fitted by the step 7 code on claims reported by ``as_of``.

    History only (the bank as it stood before the experiment), so using it in a readout during the
    experiment is legitimate. It carries no report rate: the share of fraud that is never reported
    cannot be learned from reports, and a gross-up for it is a named assumption (docs/fraud-loss.md),
    not something this readout estimates.
    """
    return delay.fit_by_type(cl.load_claims(bank_dir), as_of)


AS_OF = date(
    2024, 12, 31
)  # the bank as it stood before the experiment: delay models use claims to here


def completion(fits: dict, kind: np.ndarray, age: np.ndarray) -> np.ndarray:
    """Share of reports of each fraud type that has arrived ``age`` days after the first loss.

    Floored at 1e-3 so a very new claim cannot get an unbounded weight. Shared by the single-bank
    readout here and the many-bank study in ``labels``, so both start the clock the same way.
    """
    c = np.ones(len(age))
    for t in cl.TYPES:
        s = kind == t
        if t in fits and s.any():
            c[s] = fits[t].cdf(age[s])
    return np.maximum(c, 1e-3)


def weighted_weekly(arm: Arm, fits: dict, n: int) -> np.ndarray:
    """Cumulative reported loss, each claim scaled up by the chance it would be reported by now.

    A claim whose first loss was ``a`` days before a look and that has been reported counts
    ``1 / F(a)`` times its amount, where ``F`` is the delay distribution of its fraud type. Claims
    not yet reported count zero, so on average the weighted figure is the loss of the claims that
    will eventually be reported. Weights grow quickly for the newest claims, the price of reading
    early. An episode that began before the experiment has its clock started at its first event
    after the start, which slightly overweights it; there are very few.
    """
    first = arm.fraud.group_by("episode_id").agg(pl.col("ts").min().alias("first_ts"))
    ev = arm.fraud.join(first, on="episode_id").filter(pl.col("reported_ts").is_not_null())
    row = ev["i"].to_numpy()
    amount = ev["amount"].to_numpy()
    first_ts = ev["first_ts"].to_numpy()
    reported = ev["reported_ts"].to_numpy()
    kind = ev["fraud_type"].to_numpy()
    out = np.zeros((n, WEEKS))
    for k, end in enumerate(week_ends()):
        e = np.datetime64(end)
        ok = (reported <= e) & (row >= 0)
        age = (e - first_ts[ok]) / np.timedelta64(1, "D")
        c = completion(fits, kind[ok], age)
        np.add.at(out[:, k], row[ok], amount[ok] / c)
    return out
