"""Write every table that is copied to Databricks as Parquet files, ready to upload.

Each table becomes a folder of files of at most ~128 MB, plus ``STAGE.json`` listing every
file with its size and SHA-256, so an interrupted upload can resume and a finished one can be
checked file by file.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import duckdb

from fincrime.config import Paths
from fincrime.warehouse.local import relation_of
from fincrime.warehouse.tables import FEATURES, TABLES, Table

FILE_BYTES = 128 * 1024 * 1024


def stage_dir(paths: Paths) -> Path:
    return paths.processed / "warehouse_stage"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _select(table: Table, rel: str) -> str:
    if not table.exclude:
        return f"select * from {rel}"
    return f"select * exclude ({', '.join(table.exclude)}) from {rel}"


def export(paths: Paths, with_features: bool = True) -> Path:
    """Export the tables flagged ``copy``; returns the stage folder."""
    out = stage_dir(paths)
    con = duckdb.connect(str(paths.database), read_only=True)
    listing: dict[str, list[dict[str, object]]] = {}
    for table in (*TABLES, *((FEATURES,) if with_features else ())):
        if not table.copy:
            continue
        target = out / table.schema / table.name
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)  # DuckDB creates only the last folder
        sql = _select(table, relation_of(table, paths))
        con.sql(
            f"copy ({sql}) to '{target.as_posix()}' "
            f"(format parquet, compression zstd, file_size_bytes {FILE_BYTES})"
        )
        files = sorted(target.glob("*.parquet"))
        listing[table.full] = [
            {"path": f.relative_to(out).as_posix(), "bytes": f.stat().st_size, "sha256": _sha256(f)}
            for f in files
        ]
        print(
            f"{table.full}: {len(files)} files, {sum(f.stat().st_size for f in files) / 1e6:.0f} MB"
        )
    con.close()
    (out / "STAGE.json").write_text(json.dumps(listing, indent=1), encoding="utf-8")
    return out
