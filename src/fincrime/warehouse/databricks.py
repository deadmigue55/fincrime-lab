"""Databricks side of the copy: connect, upload the staged files, load them as tables, rebuild marts.

Credentials come from a Databricks CLI profile (``~/.databrickscfg``), never from this repository.
Set ``FCL_DATABRICKS_PROFILE`` to use a profile other than ``fincrime``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fincrime.warehouse import manifest
from fincrime.warehouse.export import stage_dir
from fincrime.warehouse.tables import FEATURES, TABLES

CATALOG = "fincrime"
SCHEMAS = ("lake", "dim", "eval", "mart", "ops", "features", "staging")
VOLUME = f"/Volumes/{CATALOG}/staging/files"
SQL_DIR = Path(__file__).parent / "sql"


def _profile() -> str:
    return os.environ.get("FCL_DATABRICKS_PROFILE", "fincrime")


def client():
    """The Databricks workspace client for the configured profile."""
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(profile=_profile())


@contextmanager
def session(w=None):
    """A SQL session on the workspace's SQL warehouse, yielding ``run(sql) -> rows``."""
    from databricks import sql

    w = w or client()
    wh = next(iter(w.warehouses.list()))
    with sql.connect(
        server_hostname=w.config.host.removeprefix("https://"),
        http_path=wh.odbc_params.path,
        access_token=w.config.token,
        catalog=CATALOG if _catalog_exists(w) else None,
    ) as conn:

        def run(statement: str) -> Sequence[Sequence[Any]]:
            with conn.cursor() as cur:
                cur.execute(statement)
                return cur.fetchall() if cur.description else []

        yield run


def _catalog_exists(w) -> bool:
    return any(c.name == CATALOG for c in w.catalogs.list())


def setup(run: Callable[[str], Any]) -> None:
    """Create the catalog, one schema per local schema, and the staging volume (idempotent)."""
    run(f"create catalog if not exists {CATALOG}")
    run(f"use catalog {CATALOG}")
    for schema in SCHEMAS:
        run(f"create schema if not exists {schema}")
    run("create volume if not exists staging.files")


def upload(paths, w=None, only: Sequence[str] | None = None) -> int:
    """Upload staged files to the volume; a file already there with the same size is skipped."""
    w = w or client()
    out = stage_dir(paths)
    listing = json.loads((out / "STAGE.json").read_text(encoding="utf-8"))
    sent = 0
    for table, files in listing.items():
        if only and table not in only:
            continue
        for f in files:
            target = f"{VOLUME}/{f['path']}"
            try:
                if w.files.get_metadata(target).content_length == f["bytes"]:
                    continue
            except Exception:
                pass
            with (out / f["path"]).open("rb") as fh:
                w.files.upload(target, fh, overwrite=True)
            sent += 1
            print(f"uploaded {table}: {f['path']} ({f['bytes'] / 1e6:.0f} MB)")
    return sent


def load(run: Callable[[str], Any], only: Sequence[str] | None = None) -> None:
    """Create a Delta table from each uploaded folder."""
    for table in (*TABLES, FEATURES):
        if not table.copy or (only and table.full not in only):
            continue
        run(
            f"create or replace table {table.full} as "
            f"select * from read_files('{VOLUME}/{table.schema}/{table.name}/', format => 'parquet')"
        )
        print(f"loaded {table.full}")


def rebuild_marts(run: Callable[[str], Any]) -> None:
    """Run the Databricks SQL versions of the marts."""
    for f in sorted(SQL_DIR.glob("*.sql")):
        run(f.read_text(encoding="utf-8").strip().rstrip(";"))
        print(f"rebuilt from {f.name}")


def reconcile(run: Callable[[str], Any], before: dict[str, Any], only: Sequence[str] | None = None):
    """Fingerprint each table in Databricks and compare with the frozen local manifest."""
    problems: dict[str, list[str]] = {}
    for name, spec in before["tables"].items():
        if (not spec["copy"] and not spec["rebuild"]) or (only and name not in only):
            continue
        cols = [(c, s["category"]) for c, s in spec["columns"].items()]
        diffs = manifest.compare(spec, manifest.fingerprint(run, name, cols))
        print(f"{name}: {'identical' if not diffs else f'{len(diffs)} differences'}")
        if diffs:
            problems[name] = diffs
    return problems
