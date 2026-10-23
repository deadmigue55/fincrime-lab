"""Run the whole Banco Sintético and write it to Parquet.

Layers on disk (``<out>/``):

* latent truth: ``customers`` (with ``fraud_role``, ``ring_id``), ``profiles``, ``rings``,
  ``episodes``, and the truth columns of ``attempts`` (``is_fraud``, ``crime_class``,
  ``fraud_type``, ``episode_id``, ``ring_id``);
* policy layer: the ``decision``, ``rule`` and ``status`` of every attempt;
* observed layer: ``attempts`` where ``status = 'approved'`` are the bank's transactions, and
  ``fraud_reports`` holds the fraud labels with the (late) date the victim reported.

Models must read only observable columns; the evaluation code reads the truth.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.attacks import ATTEMPT_SCHEMA, simulate_attacks
from fincrime.bank.customers import make_customers, rng_for
from fincrime.bank.events import MIRROR_OFFSET, iter_genuine_events
from fincrime.bank.policy import RulePolicy
from fincrime.bank.profiles import make_profiles
from fincrime.bank.rings import make_rings

# Observable attempt columns (what a real bank sees at decision time and after).
OBSERVABLE = (
    "event_id",
    "ts",
    "account_id",
    "event_type",
    "channel",
    "direction",
    "amount",
    "counterparty_kind",
    "counterparty_account",
    "counterparty_bank",
    "mcc_group",
    "device_id",
    "decision",
    "rule",
    "status",
)
TRUTH = ("is_fraud", "fraud_type", "episode_id", "crime_class", "ring_id")


@dataclass(frozen=True)
class BuildSummary:
    """Headline counts of a build, also written to ``summary.json``."""

    n_customers: int
    n_mules: int
    n_attempts: int
    n_genuine: int
    n_fraud_attempts: int
    n_laundering_events: int
    genuine_blocked: int
    fraud_attempted_amount: float
    fraud_lost_amount: float
    fraud_blocked_amount: float
    n_reports: int


def observe_genuine(
    a: Assumptions,
    events: pl.DataFrame,
    opened_on: pl.DataFrame,
    policy: RulePolicy,
    chunk_idx: int,
) -> pl.DataFrame:
    """Compute features, apply the policy and resolve step-up for one chunk of genuine events."""
    rng = rng_for(a.seed, f"stepup-{chunk_idx}")
    df = events.sort(["account_id", "ts"]).with_columns(
        hour=pl.col("ts").dt.hour().cast(pl.Int64),
        device_age_minutes=pl.when(pl.col("device_id").is_not_null())
        .then(
            (pl.col("ts") - pl.col("ts").min().over(["account_id", "device_id"])).dt.total_minutes()
        )
        .otherwise(10**9)
        .cast(pl.Int64),
        velocity_10m=(pl.col("direction") == "out")
        .cast(pl.Int64)
        .rolling_sum_by("ts", window_size="10m", closed="left")
        .over("account_id")
        .cast(pl.Int64),
    )
    cp = opened_on.rename({"account_id": "counterparty_account", "opened_on": "cp_opened"})
    df = (
        df.join(cp, on="counterparty_account", how="left")
        .with_columns(
            cp_age_days=pl.when(pl.col("direction") == "out")
            .then((pl.col("ts").dt.date() - pl.col("cp_opened")).dt.total_days())
            .otherwise(None)
            .cast(pl.Int64)
        )
        .drop("cp_opened")
    )
    df = policy.decide(df)
    passed = rng.random(df.height) < a.stepup_pass_genuine
    df = df.with_columns(
        status=pl.when(pl.col("decision") == "decline")
        .then(pl.lit("declined"))
        .when((pl.col("decision") == "step_up") & ~pl.Series(passed))
        .then(pl.lit("stepup_failed"))
        .otherwise(pl.lit("approved"))
    )
    # a transfer that never happened has no receiving side
    sent_ok = df.filter(pl.col("event_id") < MIRROR_OFFSET).select(
        (pl.col("event_id") + MIRROR_OFFSET).alias("event_id"),
        (pl.col("status") == "approved").alias("sender_ok"),
    )
    df = df.join(sent_ok, on="event_id", how="left")
    df = df.filter(pl.col("sender_ok").fill_null(True)).drop("sender_ok")
    return df.select(list(ATTEMPT_SCHEMA)).cast(ATTEMPT_SCHEMA)


def build_bank(a: Assumptions, policy: RulePolicy, out_dir: Path) -> BuildSummary:
    """Generate and write every table. Deterministic in ``a`` and ``policy``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "attempts").mkdir(exist_ok=True)
    for old in (out_dir / "attempts").glob("*.parquet"):
        old.unlink()

    customers, accounts = make_customers(a)
    profiles = make_profiles(a, customers)
    customers_all, accounts_all, rings = make_rings(a, customers, accounts)
    opened_on = accounts_all.select("account_id", "opened_on")

    n_genuine = n_genuine_blocked = 0
    for i, chunk in enumerate(iter_genuine_events(a, customers, profiles)):
        observed = observe_genuine(a, chunk, opened_on, policy, i)
        observed.write_parquet(out_dir / "attempts" / f"genuine-{i:04d}.parquet")
        n_genuine += observed.height
        n_genuine_blocked += observed.filter(pl.col("status") != "approved").height

    attacks = simulate_attacks(a, customers_all, profiles, rings, policy)
    attacks.attempts.write_parquet(out_dir / "attempts" / "attacks.parquet")
    attacks.reports.write_parquet(out_dir / "fraud_reports.parquet")
    attacks.episodes.write_parquet(out_dir / "episodes.parquet")
    customers_all.write_parquet(out_dir / "customers.parquet")
    accounts_all.write_parquet(out_dir / "accounts.parquet")
    profiles.write_parquet(out_dir / "profiles.parquet")
    rings.write_parquet(out_dir / "rings.parquet")

    at = attacks.attempts
    fraud = at.filter(pl.col("is_fraud") & (pl.col("event_type") != "spei_in"))
    fraud_ep = attacks.episodes.filter(pl.col("crime_class") == "fraud")
    summary = BuildSummary(
        n_customers=customers.height,
        n_mules=customers_all.height - customers.height,
        n_attempts=n_genuine + at.height,
        n_genuine=n_genuine,
        n_fraud_attempts=fraud.height,
        n_laundering_events=at.filter(pl.col("crime_class") == "laundering").height,
        genuine_blocked=n_genuine_blocked,
        fraud_attempted_amount=float(fraud_ep["attempted_amount"].sum()),
        fraud_lost_amount=float(fraud_ep["lost_amount"].sum()),
        fraud_blocked_amount=float(fraud_ep["blocked_amount"].sum()),
        n_reports=attacks.reports.height,
    )
    meta = {
        "summary": asdict(summary),
        "assumptions": asdict(a),
        "policy": asdict(policy),
    }
    (out_dir / "summary.json").write_text(json.dumps(meta, indent=2, default=_json_default))
    return summary


def _json_default(obj: object) -> object:
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"not serialisable: {type(obj)}")
