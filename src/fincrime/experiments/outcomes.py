"""From the tables of each simulated arm to per-customer outcome matrices.

Every analysis in this module works on three things: the eligible customers, their pre-treatment
covariates (always measured in the baseline bank, before the new rules start), and the outcomes
each customer would have under each rule set. :class:`Bench` holds them as dense arrays so that
thousands of random assignments can be scored with a few matrix products.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.experiments import worlds

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
    return Arm(
        name=path.name,
        y=_metrics(ids, fraud, genuine, T0, T_END + timedelta(seconds=1))[0],
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
