"""Claims, as the bank would see them, and the exposure they arise from.

A **claim** is a fraud episode that actually cost money: at least one approved outgoing loss
(docs/methodology.md section 8). Its *loss date* is its first approved loss event, its *amount* the
episode's lost pesos, its *reported date* the day the victim told the bank (never, for the share
that do not report).

The one rule of this module is the **as-of cut**: at a date D the bank knows only the claims whose
report date is on or before D. :func:`visible` is the single place that applies it, and the tests
check that deleting everything after D cannot change an estimate. The simulator keeps every report,
including those after the end of the window, so the later "ultimate" of each cohort is available
for scoring; a real bank has no such file.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl

TYPES = ("ato", "card_testing")


def build_claims(
    attempts: pl.DataFrame, episodes: pl.DataFrame, reports: pl.DataFrame
) -> pl.DataFrame:
    """One row per claim: ``episode_id, fraud_type, loss_ts, amount, reported_ts`` (null = never).

    ``attempts`` need only hold the attack events. The loss date is the first *approved outgoing*
    event of the episode, which is also where the simulator starts the report-delay clock.
    """
    loss = (
        attempts.filter(
            pl.col("is_fraud")
            & (pl.col("crime_class") == "fraud")
            & (pl.col("status") == "approved")
            & (pl.col("direction") == "out")
        )
        .group_by("episode_id")
        .agg(pl.col("ts").min().alias("loss_ts"), pl.col("amount").sum().alias("event_amount"))
    )
    rep = reports.group_by("episode_id").agg(pl.col("reported_ts").min())
    out = (
        episodes.filter(pl.col("crime_class") == "fraud")
        .select("episode_id", "fraud_type", pl.col("lost_amount").alias("amount"))
        .join(loss, on="episode_id", how="inner")
        .join(rep, on="episode_id", how="left")
        .sort("loss_ts")
    )
    # the episode table and the event table must agree on what was lost
    gap = (out["amount"] - out["event_amount"]).abs().max()
    if gap is not None and gap > 0.01:
        raise ValueError(f"episode lost_amount disagrees with the approved events by {gap:.2f}")
    return out.drop("event_amount")


def load_claims(bank_dir: Path) -> pl.DataFrame:
    """Claims of a built bank (``bank build``) read from its Parquet files."""
    attempts = pl.read_parquet(bank_dir / "attempts" / "attacks.parquet")
    return build_claims(
        attempts,
        pl.read_parquet(bank_dir / "episodes.parquet"),
        pl.read_parquet(bank_dir / "fraud_reports.parquet"),
    )


def visible(claims: pl.DataFrame, as_of: datetime | date) -> pl.DataFrame:
    """What the bank knows at the end of day ``as_of``: claims reported by then, nothing else."""
    cut = _end_of_day(as_of)
    return claims.filter(pl.col("reported_ts") <= cut)


def _end_of_day(d: datetime | date) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime(d.year, d.month, d.day, 23, 59, 59, 999999)


def month_index(ts: pl.Expr) -> pl.Expr:
    """Months since year 0, so cohorts and lags are integer arithmetic."""
    return ts.dt.year().cast(pl.Int32) * 12 + ts.dt.month().cast(pl.Int32) - 1


def exposure(
    accounts: pl.DataFrame, customers: pl.DataFrame, first_month: date, last_month: date
) -> pl.DataFrame:
    """Open genuine accounts per calendar month: the denominator of the claim frequency.

    Mule accounts are the criminals' and cannot be defrauded as customers, so they are left out.
    A month's exposure is the mean of the accounts open at its first and last day, i.e. account
    months, so a month with heavy onboarding counts the new accounts for about half of it.
    """
    genuine = customers.filter(pl.col("fraud_role").is_null()).select("customer_id")
    opened = accounts.join(genuine, on="customer_id", how="inner")["opened_on"].to_numpy()
    rows = []
    y, m = first_month.year, first_month.month
    while (y, m) <= (last_month.year, last_month.month):
        start = np.datetime64(date(y, m, 1))
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        end = np.datetime64(date(ny, nm, 1))
        n_start, n_end = int((opened < start).sum()), int((opened < end).sum())
        rows.append(
            {"month": date(y, m, 1), "accounts": (n_start + n_end) / 2.0, "opened": n_end - n_start}
        )
        y, m = ny, nm
    return pl.DataFrame(rows)
