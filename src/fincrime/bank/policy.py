"""The bank's real-time decision rules (the *policy layer*).

A policy looks at an attempt using only what the bank can see at that moment and answers
``approve``, ``step_up`` (ask for extra verification) or ``decline``. The same rules run over a
whole table of genuine activity (vectorised) and over one attacker attempt at a time, because
``decide_one`` calls ``decide`` on a one-row table, so the two can never disagree.

Policies are plain frozen dataclasses: an A/B test in step 8 is just two ``RulePolicy`` values
with different thresholds. Rules only look at app and online-card events; chip-and-PIN card
payments and ATM withdrawals are out of scope for these rules.

Feature columns the policy expects (all observable by a real bank):

``event_type``, ``amount``, ``hour`` (0-23), ``device_age_minutes`` (since this device was first
seen on the account), ``velocity_10m`` (outgoing attempts on the account in the previous ten
minutes), ``cp_age_days`` (age of the receiving account when it is inside the bank, else null).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

DIGITAL_OUT = ("spei_out", "card_online", "bill_pay", "codi")
FEATURES = ("event_type", "amount", "hour", "device_age_minutes", "velocity_10m", "cp_age_days")


@dataclass(frozen=True)
class RulePolicy:
    """Threshold rules, evaluated in order; the first one that fires decides."""

    name: str = "baseline_v1"
    velocity_limit: int = 6  # decline: this many outgoing attempts in ten minutes
    new_device_minutes: int = 1_440  # a device seen for less than a day is "new"
    new_device_amount: float = 3_000.0  # step up: new device and at least this amount
    night_start: int = 0
    night_end: int = 5  # night is hours night_start..night_end inclusive
    night_amount: float = 5_000.0  # step up: night-time and at least this amount
    young_cp_days: int = 14  # a receiving account younger than this is "young"
    young_cp_amount: float = 2_000.0  # step up: transfer to a young account
    big_amount: float = 25_000.0  # step up: any digital outflow this large

    def rules(self) -> list[tuple[str, pl.Expr, str]]:
        """Ordered ``(rule name, condition, decision)`` triples."""
        digital = pl.col("event_type").is_in(DIGITAL_OUT)
        return [
            (
                "velocity",
                digital & (pl.col("velocity_10m") >= self.velocity_limit),
                "decline",
            ),
            (
                "new_device_amount",
                digital
                & (pl.col("device_age_minutes") < self.new_device_minutes)
                & (pl.col("amount") >= self.new_device_amount),
                "step_up",
            ),
            (
                "young_beneficiary",
                digital
                & (pl.col("cp_age_days") < self.young_cp_days)
                & (pl.col("amount") >= self.young_cp_amount),
                "step_up",
            ),
            (
                "night_amount",
                digital
                & pl.col("hour").is_between(self.night_start, self.night_end)
                & (pl.col("amount") >= self.night_amount),
                "step_up",
            ),
            ("big_amount", digital & (pl.col("amount") >= self.big_amount), "step_up"),
        ]

    def decide(self, df: pl.DataFrame) -> pl.DataFrame:
        """Add ``decision`` and ``rule`` columns to a table that has the feature columns."""
        decision = pl.lit("approve")
        rule = pl.lit(None, dtype=pl.String)
        for name, cond, outcome in reversed(self.rules()):
            decision = pl.when(cond).then(pl.lit(outcome)).otherwise(decision)
            rule = pl.when(cond).then(pl.lit(name)).otherwise(rule)
        return df.with_columns(decision.alias("decision"), rule.alias("rule"))

    def decide_one(self, **features: object) -> tuple[str, str | None]:
        """Decision for one attempt, given its feature values as keyword arguments."""
        row = pl.DataFrame(
            {
                "event_type": [features["event_type"]],
                "amount": [float(features["amount"])],
                "hour": [int(features["hour"])],
                "device_age_minutes": [int(features["device_age_minutes"])],
                "velocity_10m": [int(features["velocity_10m"])],
                "cp_age_days": [features.get("cp_age_days")],
            },
            schema={
                "event_type": pl.String,
                "amount": pl.Float64,
                "hour": pl.Int64,
                "device_age_minutes": pl.Int64,
                "velocity_10m": pl.Int64,
                "cp_age_days": pl.Int64,
            },
        )
        out = self.decide(row)
        return out["decision"][0], out["rule"][0]
