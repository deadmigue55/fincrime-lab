"""Potential outcomes: the same bank under two rule sets, so an experiment has a known answer.

The simulator draws every random thing about a customer, an attacker and an attempt *before* the
rules decide (``docs/methodology.md`` section 2), and a rule sees only the attempt's own features,
never earlier decisions. So the bank built with rule set B differs from the bank built with the
baseline A *only* in what the rules did, and a customer's outcome under A and under B are both
known. A real experiment shows one of the two per customer; here both exist, which is what lets us
check that an experiment's estimate lands on the truth.

``build_arm`` builds one world and reduces it to three small tables (``extract``). Nothing in this
module reads the real world's future: it is the *test bench*; the analysis code in ``analysis.py``
sees only what an experimenter would.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.policy import RulePolicy
from fincrime.bank.simulate import build_bank

# The experiment: customers onboarded by START are eligible, the new rules apply from START, and
# the outcome window runs to END. The 26 weeks before START are the pre-period for covariates.
START = date(2025, 1, 1)
END = date(2025, 6, 30)
PRE_START = date(2024, 7, 1)

# The rule changes under test. Each is the baseline with some thresholds moved; the first two each
# change one family of rules (so each has a clear mechanism and a clear zero on the other fraud
# type), the third changes both.
TREATMENTS: dict[str, RulePolicy] = {
    "velocity3": RulePolicy(name="velocity3", velocity_limit=3),
    "device500": RulePolicy(name="device500", new_device_amount=500.0),
    "tight_all": RulePolicy(
        name="tight_all",
        velocity_limit=3,
        new_device_amount=500.0,
        young_cp_amount=500.0,
        night_amount=1_000.0,
    ),
}


def build_arm(
    policy: RulePolicy, workdir: Path, customers: int = 200_000, seed: int = 2026
) -> dict[str, float]:
    """Build the whole bank under ``policy`` in ``workdir`` (about two minutes at 200,000)."""
    t0 = time.time()
    build_bank(Assumptions(n_customers=customers, seed=seed), policy, workdir)
    return {"seconds": time.time() - t0}


def extract(bank_dir: Path) -> dict[str, pl.DataFrame]:
    """Reduce a built bank to the tables an experiment needs.

    ``fraud``: one row per approved outgoing fraud event, with its customer and the report date of
    its episode (null = never reported). ``episodes``: one row per fraud episode with what was
    attempted, lost and blocked. ``genuine``: every outgoing genuine attempt that was *not*
    plainly approved, i.e. that caused friction (a failed extra verification or a decline), and
    the step-ups that were passed (a burden even when they succeed). ``volume``: per customer and
    period, the approved genuine outgoing count and pesos.
    """
    con = duckdb.connect()
    att = f"read_parquet('{(bank_dir / 'attempts' / '*.parquet').as_posix()}')"
    acc = f"read_parquet('{(bank_dir / 'accounts.parquet').as_posix()}')"
    rep = f"read_parquet('{(bank_dir / 'fraud_reports.parquet').as_posix()}')"
    epi = f"read_parquet('{(bank_dir / 'episodes.parquet').as_posix()}')"
    start, end = f"TIMESTAMP '{START} 00:00:00'", f"TIMESTAMP '{END} 23:59:59'"

    fraud = con.sql(
        f"""
        SELECT a.event_id, a.ts, k.customer_id, a.amount, a.fraud_type, a.episode_id,
               r.reported_ts
        FROM {att} a
        JOIN {acc} k USING (account_id)
        LEFT JOIN (SELECT episode_id, min(reported_ts) AS reported_ts FROM {rep}
                   GROUP BY episode_id) r USING (episode_id)
        WHERE a.is_fraud AND a.crime_class = 'fraud' AND a.status = 'approved'
              AND a.direction = 'out' AND a.ts >= {start}
        ORDER BY a.ts
        """
    ).pl()
    episodes = con.sql(
        f"""
        SELECT e.episode_id, e.fraud_type, k.customer_id, e.start_ts, e.n_attempts,
               e.attempted_amount, e.lost_amount, e.blocked_amount
        FROM {epi} e JOIN {acc} k ON k.account_id = e.victim_account
        WHERE e.crime_class = 'fraud' AND e.start_ts >= {start}
        """
    ).pl()
    genuine = con.sql(
        f"""
        SELECT a.ts, k.customer_id, a.status, a.decision, a.rule, a.amount
        FROM {att} a JOIN {acc} k USING (account_id)
        WHERE NOT a.is_fraud AND a.crime_class IS NULL AND a.direction = 'out'
              AND (a.status <> 'approved' OR a.decision = 'step_up')
              AND a.ts >= TIMESTAMP '{PRE_START} 00:00:00'
        """
    ).pl()
    volume = con.sql(
        f"""
        SELECT k.customer_id, (a.ts >= {start}) AS in_window,
               count(*) AS n_out, sum(a.amount) AS pesos_out
        FROM {att} a JOIN {acc} k USING (account_id)
        WHERE NOT a.is_fraud AND a.crime_class IS NULL AND a.direction = 'out'
              AND a.status = 'approved' AND a.ts >= TIMESTAMP '{PRE_START} 00:00:00'
              AND a.ts <= {end}
        GROUP BY ALL
        """
    ).pl()
    return {"fraud": fraud, "episodes": episodes, "genuine": genuine, "volume": volume}


def build_and_extract(name: str, policy: RulePolicy, out: Path, keep: bool = False) -> Path:
    """Build one arm, write its tables to ``out/<name>/``, drop the bulky bank unless ``keep``."""
    work = out / f"_bank_{name}"
    build_arm(policy, work)
    tables = extract(work)
    dest = out / name
    dest.mkdir(parents=True, exist_ok=True)
    for key, df in tables.items():
        df.write_parquet(dest / f"{key}.parquet")
    if not keep:
        import shutil

        shutil.rmtree(work)
    return dest


def extract_baseline(bank_dir: Path, out: Path) -> Path:
    """Tables of the already-built baseline bank (arm A), same layout as the treated arms."""
    dest = out / "baseline"
    dest.mkdir(parents=True, exist_ok=True)
    for key, df in extract(bank_dir).items():
        df.write_parquet(dest / f"{key}.parquet")
    return dest


def customers_table(bank_dir: Path) -> pl.DataFrame:
    """The eligible population: genuine customers onboarded before the experiment starts."""
    c = pl.read_parquet(bank_dir / "customers.parquet")
    return (
        c.filter(pl.col("fraud_role").is_null() & (pl.col("onboarded_on") < START))
        .select("customer_id", "onboarded_on", "state", "income_band", "n_devices")
        .with_columns(
            tenure_days=(pl.lit(START) - pl.col("onboarded_on")).dt.total_days().cast(pl.Float64)
        )
        .sort("customer_id")
    )
