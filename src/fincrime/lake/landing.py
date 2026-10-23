"""The landing zone: what the source systems would hand to the data platform each day.

The bank's build is split into daily *drops* partitioned by **arrival date**, the day a file
reaches the platform. A small share of events arrives late (1 to 3 days after they happened),
so a drop can contain rows for earlier days: that is what makes incremental loading and
reprocessing interesting.

The platform never sees truth in the same place as observables: ``attempts`` holds only what a
real bank sees, ``truth`` holds the latent labels (for evaluation), ``reports`` holds the fraud
reports (which arrive on the day the victim reports), and ``dim`` holds account attributes.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from fincrime.bank.simulate import OBSERVABLE, TRUTH

LATE_SHARE = 3  # percent of events that arrive after the day they happened
MAX_LATE_DAYS = 3

DIM_COLUMNS = (
    "account_id",
    "customer_id",
    "clabe",
    "opened_on",
    "state",
    "sex",
    "birth_date",
    "income_band",
    "n_devices",
    "curp",
)


def _q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def arrival_expression() -> str:
    """SQL for the arrival date of an event: usually its own day, sometimes up to 3 days later."""
    h = "(hash(event_id) % 100)"
    late = f"(1 + (hash(event_id + 7) % {MAX_LATE_DAYS}))"
    return f"(ts::date + (case when {h} < {LATE_SHARE} then {late} else 0 end)::int)"


def export_landing(bank_dir: Path, landing_dir: Path) -> dict[str, int]:
    """Split a built bank into daily drops under ``landing_dir``. Returns row counts per kind."""
    con = duckdb.connect()
    src = f"read_parquet('{_q(bank_dir)}/attempts/*.parquet')"
    obs = ", ".join(OBSERVABLE)
    truth = ", ".join(("event_id", *TRUTH))
    arrival = arrival_expression()
    counts: dict[str, int] = {}

    import shutil

    for kind in ("attempts", "truth", "reports", "dim"):
        shutil.rmtree(landing_dir / kind, ignore_errors=True)
    landing_dir.mkdir(parents=True, exist_ok=True)

    for kind, cols in (("attempts", obs), ("truth", truth)):
        out = landing_dir / kind
        con.sql(
            f"copy (select {cols}, {arrival} as arrival from {src}) to '{_q(out)}' "
            "(format parquet, compression zstd, partition_by (arrival), overwrite_or_ignore)"
        )
        counts[kind] = con.sql(
            f"select count(*) from read_parquet('{_q(out)}/*/*.parquet')"
        ).fetchall()[0][0]

    reports = landing_dir / "reports"
    con.sql(
        f"copy (select event_id, episode_id, fraud_type, reported_ts, reported_ts::date as arrival "
        f"from read_parquet('{_q(bank_dir)}/fraud_reports.parquet')) to '{_q(reports)}' "
        "(format parquet, compression zstd, partition_by (arrival), overwrite_or_ignore)"
    )
    counts["reports"] = con.sql(
        f"select count(*) from read_parquet('{_q(bank_dir)}/fraud_reports.parquet')"
    ).fetchall()[0][0]

    dim_cols = ", ".join(
        f"c.{c}" if c not in ("account_id", "clabe", "opened_on") else f"a.{c}" for c in DIM_COLUMNS
    )
    dim = landing_dir / "dim"
    dim.mkdir(parents=True, exist_ok=True)
    con.sql(
        f"copy (select {dim_cols} from read_parquet('{_q(bank_dir)}/customers.parquet') c "
        f"join read_parquet('{_q(bank_dir)}/accounts.parquet') a using (customer_id)) "
        f"to '{_q(dim)}/accounts.parquet' (format parquet, compression zstd)"
    )
    counts["dim"] = con.sql(
        f"select count(*) from read_parquet('{_q(dim)}/accounts.parquet')"
    ).fetchall()[0][0]
    # latent account facts for evaluation only, kept apart from the observable dimension
    con.sql(
        f"copy (select customer_id as account_id, fraud_role, ring_id "
        f"from read_parquet('{_q(bank_dir)}/customers.parquet')) "
        f"to '{_q(dim)}/accounts_truth.parquet' (format parquet, compression zstd)"
    )
    return counts


def drops(landing_dir: Path) -> list[str]:
    """Arrival dates present in the landing zone, oldest first."""
    found = set()
    for kind in ("attempts", "truth", "reports"):
        for p in (landing_dir / kind).glob("arrival=*"):
            found.add(p.name.split("=", 1)[1])
    return sorted(found)
