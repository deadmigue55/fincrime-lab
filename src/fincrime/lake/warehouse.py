"""The DuckDB warehouse: schemas, views over the Parquet lake, and the marts.

Facts live in the Parquet lake (partitioned by event day, never edited in place). DuckDB holds
the small derived tables (marts), the load ledger and the data-quality results, and exposes the
lake through views in schema ``lake``. Marts are rebuilt per *event day*, so late data only
costs recomputing the days it touches.
"""

from __future__ import annotations

from datetime import date
from importlib import resources
from pathlib import Path

import duckdb

LAKE_KINDS = ("attempts", "truth", "reports")


def _q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def sql_files() -> list[tuple[str, str]]:
    """The mart SQL files in order, as ``(name, text)``."""
    folder = resources.files("fincrime.lake") / "sql"
    items = sorted((p for p in folder.iterdir() if p.name.endswith(".sql")), key=lambda p: p.name)
    return [(p.name, p.read_text(encoding="utf-8")) for p in items]


def statements(text: str) -> list[str]:
    """Split a SQL file into statements, dropping ``--`` comments."""
    lines = [line.split("--", 1)[0] for line in text.splitlines()]
    return [s.strip() for s in chr(10).join(lines).split(";") if s.strip()]


def connect(db: Path | None, lake: Path) -> duckdb.DuckDBPyConnection:
    """Open the warehouse (``None`` for in-memory), create schemas, attach the lake views."""
    if db is not None:
        db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db) if db is not None else ":memory:")
    for name, text in sql_files():
        if name == "schema.sql":
            for stmt in statements(text):
                con.execute(stmt)
    refresh_views(con, lake)
    return con


def refresh_views(con: duckdb.DuckDBPyConnection, lake: Path) -> None:
    """(Re)create ``lake.*`` views for every kind that has files."""
    con.execute("create schema if not exists lake")
    for kind in LAKE_KINDS:
        if any((lake / kind).glob("*/*.parquet")):
            con.execute(
                f"create or replace view lake.{kind} as select * from "
                f"read_parquet('{_q(lake)}/{kind}/*/*.parquet', hive_partitioning = true, filename = true)"
            )


def has_lake(con: duckdb.DuckDBPyConnection, kind: str) -> bool:
    """Is there a ``lake.<kind>`` view yet?"""
    return bool(
        con.execute(
            "select count(*) from information_schema.tables where table_schema = 'lake' and table_name = ?",
            [kind],
        ).fetchall()[0][0]
    )


def refresh_dims(con: duckdb.DuckDBPyConnection, landing: Path) -> int:
    """Full refresh of the small dimension tables from the landing zone."""
    dim = landing / "dim"
    con.execute(
        f"create or replace table dim.accounts as select * from read_parquet('{_q(dim)}/accounts.parquet')"
    )
    truth = dim / "accounts_truth.parquet"
    if truth.exists():
        con.execute(
            f"create or replace table eval.accounts as select * from read_parquet('{_q(truth)}')"
        )
    return con.execute("select count(*) from dim.accounts").fetchall()[0][0]


def rebuild_marts(con: duckdb.DuckDBPyConnection, days: list[date]) -> None:
    """Recompute every mart for the given event days (delete then insert, in one transaction)."""
    if not days:
        return
    con.execute("begin")
    try:
        con.execute("create or replace temp table touched as select unnest(?)::date as day", [days])
        for name, text in sql_files():
            if name != "schema.sql":
                for stmt in statements(text):
                    con.execute(stmt)
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
