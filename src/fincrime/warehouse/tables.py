"""Which tables exist in the local warehouse, and which of them are copied to Databricks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Table:
    """One table: where it lives locally and what the copy does with it."""

    schema: str
    name: str
    copy: bool = True  # False: listed in the manifest, not uploaded (see ``why``)
    rebuild: bool = False  # True: recomputed in Databricks SQL and compared, not uploaded
    exclude: tuple[str, ...] = ()  # columns that stay local
    why: str = ""

    @property
    def full(self) -> str:
        return f"{self.schema}.{self.name}"


# ``filename`` is the local path DuckDB adds when it reads the Parquet files: a machine detail, not data.
TABLES = (
    Table("lake", "attempts", exclude=("filename",)),
    Table("lake", "truth", exclude=("filename",)),
    Table("lake", "reports", exclude=("filename",)),
    Table("dim", "accounts"),
    Table("eval", "accounts"),
    Table("eval", "daily_fraud", copy=False, rebuild=True),
    Table("mart", "daily_kpis", copy=False, rebuild=True),
    Table("mart", "account_day", copy=False, rebuild=True),
    Table("mart", "device_account_day", copy=False, rebuild=True),
    Table("mart", "transfer_edge_day", copy=False, rebuild=True),
    Table("ops", "load_ledger"),
    Table("ops", "dq_results"),
    Table(
        "ops",
        "event_registry",
        copy=False,
        why="DuckDB-internal dedupe index; every row is already in lake.attempts",
    ),
)

FEATURES = Table("features", "fraud_events", exclude=("month",))


def features_relation(data_root: Path) -> str:
    """The feature store is a Parquet dataset partitioned by month, not a DuckDB table."""
    pattern = (data_root / "processed" / "features" / "fraud_events").as_posix()
    return f"read_parquet('{pattern}/*/*.parquet', hive_partitioning = true)"
