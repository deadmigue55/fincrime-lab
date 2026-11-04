"""Read the local DuckDB warehouse into the shapes the copy needs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb

from fincrime.config import Paths
from fincrime.warehouse import manifest
from fincrime.warehouse.tables import FEATURES, TABLES, Table, features_relation


def columns(con: duckdb.DuckDBPyConnection, relation: str, exclude: tuple[str, ...]):
    """``[(name, category)]`` for a table or Parquet relation, minus excluded columns."""
    rows = con.sql(f"describe select * from {relation}").fetchall()
    return [(r[0], manifest.category(r[1])) for r in rows if r[0] not in exclude]


def relation_of(table: Table, paths: Paths) -> str:
    return features_relation(paths.data) if table is FEATURES else table.full


def build_manifest(paths: Paths, with_features: bool = True) -> dict[str, Any]:
    """Fingerprint every local table (and the feature store). The database is opened read-only."""
    con = duckdb.connect(str(paths.database), read_only=True)

    def run(sql: str):
        return con.sql(sql).fetchall()

    tables = {}
    for table in (*TABLES, *((FEATURES,) if with_features else ())):
        rel = relation_of(table, paths)
        cols = columns(con, rel, table.exclude)
        tables[table.full] = manifest.fingerprint(run, rel, cols) | {
            "copy": table.copy,
            "rebuild": table.rebuild,
            "excluded": list(table.exclude),
        }
    con.close()
    return {"created": datetime.now(UTC).isoformat(timespec="seconds"), "tables": tables}
