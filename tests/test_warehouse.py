import re
import subprocess
from pathlib import Path

import duckdb
import pytest

from fincrime.warehouse import databricks, manifest
from fincrime.warehouse.local import columns
from fincrime.warehouse.tables import TABLES
from test_lake import landed, new_lake, snapshot  # noqa: F401  (``landed`` is a fixture)

ROOT = Path(__file__).resolve().parents[1]


def test_ported_marts_equal_the_incremental_marts(landed):  # noqa: F811
    """The Databricks SQL (full rebuild) gives the same marts as the DuckDB per-day refresh."""
    root, _ = landed
    lake = new_lake(root, "port")
    lake.load_pending()
    first = snapshot(lake)
    files = sorted(databricks.SQL_DIR.glob("*.sql"))
    assert len(files) == 5
    for f in files:
        lake.con.execute(f.read_text(encoding="utf-8"))
    assert snapshot(lake) == first
    lake.close()


def test_every_rebuilt_table_has_a_ported_query():
    sql = " ".join(f.read_text(encoding="utf-8") for f in databricks.SQL_DIR.glob("*.sql"))
    for table in TABLES:
        if table.rebuild:
            assert f"create or replace table {table.full} " in sql


def _tiny():
    con = duckdb.connect()
    con.sql(
        "create table t as select * from (values (1, 2.5::double, 'a', true, date '2024-01-01', "
        "timestamp '2024-01-01 10:00:00'), (2, null, 'b', false, date '2024-01-03', "
        "timestamp '2024-01-02 11:30:00')) v(i, x, s, b, d, ts)"
    )
    cols = columns(con, "t", ())
    run = lambda q: con.sql(q).fetchall()  # noqa: E731
    return con, cols, run


def test_manifest_is_identical_for_equal_tables_and_names_every_difference():
    con, cols, run = _tiny()
    assert {c for _, c in cols} == {"int", "float", "text", "bool", "date", "ts"}
    before = manifest.fingerprint(run, "t", cols)
    assert manifest.compare(before, manifest.fingerprint(run, "t", cols)) == []
    con.sql("update t set x = 2.6 where i = 1")
    con.sql("update t set s = 'bb' where i = 2")
    con.sql(
        "insert into t values (3, 1.0, 'c', true, date '2024-02-01', timestamp '2024-02-01 00:00:00')"
    )
    diffs = manifest.compare(before, manifest.fingerprint(run, "t", cols))
    text = " | ".join(diffs)
    assert "rows" in text and "x.sum" in text and "s.length" in text and "d.max" in text


def test_manifest_rejects_a_column_type_it_has_no_rule_for():
    with pytest.raises(ValueError):
        manifest.category("STRUCT(a INTEGER)")


FORBIDDEN = {
    "an access token": r"dapi[0-9a-f]{20,}",
    "a Databricks workspace address": r"dbc-[0-9a-f]{8}-[0-9a-f]{4}\.cloud\.databricks\.com",
    "a SQL warehouse path": r"/sql/1\.0/warehouses/[0-9a-f]{8,}",
    "a personal mailbox": r"miguelito\.abarca@gmail\.com",
    "a Windows user folder": r"C:[\\/]+Users[\\/]+\w",
}


def test_no_credentials_or_personal_paths_in_tracked_files():
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    hits = []
    for name in tracked:
        path = ROOT / name
        if name == "tests/test_warehouse.py" or path.suffix in {".png", ".webp", ".parquet"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
        hits += [f"{name}: {what}" for what, pat in FORBIDDEN.items() if re.search(pat, text)]
    assert not hits, hits
