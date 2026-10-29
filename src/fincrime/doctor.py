"""Environment checks behind ``fincrime doctor``.

The mortality project lost time to Windows Smart App Control silently blocking native
libraries (DuckDB among them), so every engine is checked by actually *using* it, not just
importing it. Each check returns a :class:`Check` so the CLI and the tests share one code path.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fincrime.config import Paths, kaggle_configured

# Disk needed for the biggest public benchmark (AMLworld Large: ~16 GB CSV, plus Parquet copy).
MIN_FREE_GB = 60


@dataclass(frozen=True)
class Check:
    """Outcome of one environment check."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def check_duckdb() -> Check:
    """Import DuckDB and run a query that reads and writes Parquet."""
    try:
        import duckdb

        with tempfile.TemporaryDirectory() as tmp:
            path = (Path(tmp) / "t.parquet").as_posix()
            con = duckdb.connect()
            con.sql(f"copy (select i as id from range(1000) t(i)) to '{path}' (format parquet)")
            total = con.sql(f"select sum(id) from '{path}'").fetchall()[0][0]
            con.close()
        if total != 499500:
            return Check("duckdb", False, f"wrong result {total}")
        return Check("duckdb", True, f"{duckdb.__version__}, Parquet round trip OK")
    except Exception as err:  # a blocked native library raises many exception types
        return Check("duckdb", False, f"{type(err).__name__}: {err}")


def check_polars() -> Check:
    """Import Polars and aggregate a small frame."""
    try:
        import polars as pl

        total = pl.DataFrame({"x": [1, 2, 3]}).select(pl.col("x").sum()).item()
        return Check("polars", total == 6, f"{pl.__version__}", required=False)
    except Exception as err:  # a blocked native library raises many exception types
        return Check("polars", False, f"{type(err).__name__}: {err}", required=False)


def check_pyarrow() -> Check:
    """Import PyArrow and build a table."""
    try:
        import pyarrow as pa

        n = pa.table({"x": [1, 2, 3]}).num_rows
        return Check("pyarrow", n == 3, f"{pa.__version__}")
    except Exception as err:  # a blocked native library raises many exception types
        return Check("pyarrow", False, f"{type(err).__name__}: {err}")


def check_disk(paths: Paths, min_free_gb: float = MIN_FREE_GB) -> Check:
    """Warn when the drive holding the data has little free space."""
    target = paths.data if paths.data.exists() else paths.root
    free_gb = shutil.disk_usage(target).free / 1e9
    return Check(
        "disk space",
        free_gb >= min_free_gb,
        f"{free_gb:,.0f} GB free on the data drive (want {min_free_gb}+ for the big benchmark)",
        required=False,
    )


def check_kaggle() -> Check:
    """Kaggle credentials are only needed for the public benchmarks, so this never blocks."""
    ok = kaggle_configured()
    detail = "configured" if ok else "not set (optional, for the benchmarks: see .env.example)"
    return Check("kaggle login", ok, detail, required=False)


def run_all(paths: Paths) -> list[Check]:
    """Run every check."""
    simple: list[Callable[[], Check]] = [check_duckdb, check_polars, check_pyarrow]
    return [*(fn() for fn in simple), check_disk(paths), check_kaggle()]
