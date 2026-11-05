"""The dbt project builds the same marts as the Python pipeline, and its own tests pass."""

from pathlib import Path

import duckdb
import pytest

from test_lake import MARTS, cents, landed, new_lake, snapshot  # noqa: F401  (``landed`` is a fixture)

dbt_cli = pytest.importorskip("dbt.cli.main")

DBT = Path(__file__).resolve().parents[1] / "dbt"
# The tables dbt reads (sources.yml). ``filename`` is the DuckDB-only path column, left out as in Databricks.
SOURCES = {
    "lake.attempts": "* exclude (filename)",
    "lake.truth": "* exclude (filename)",
    "dim.accounts": "*",
    "ops.load_ledger": "*",
}


def run_dbt(command, db, target):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FINCRIME_DBT_DB", str(db))  # read by env_var() in dbt/profiles.yml
        return dbt_cli.dbtRunner().invoke(
            [
                command,
                "--project-dir",
                str(DBT),
                "--profiles-dir",
                str(DBT),
                "--target-path",
                str(target),
            ]
        )


@pytest.fixture(scope="module")
def built(landed, tmp_path_factory):  # noqa: F811
    """Copy the source tables into a database file, run ``dbt build`` on it, return everything."""
    root, _ = landed
    lake = new_lake(root, "dbt")
    lake.load_pending()
    expected = snapshot(lake)
    db = tmp_path_factory.mktemp("dbt") / "fincrime.duckdb"
    lake.con.execute(f"attach '{db.as_posix()}' as ci")
    for schema in ("lake", "dim", "ops"):
        lake.con.execute(f"create schema if not exists ci.{schema}")
    for table, cols in SOURCES.items():
        lake.con.execute(f"create table ci.{table} as select {cols} from {table}")
    lake.con.execute("detach ci")
    lake.close()
    target = tmp_path_factory.mktemp("target")
    return run_dbt("build", db, target), db, target, expected


def test_dbt_build_passes_every_model_and_test(built):
    result, *_ = built
    assert result.success, result.exception or [
        r.node.name for r in result.result if r.status not in ("success", "pass")
    ]
    # five models and the tests on them, so a silently empty project cannot pass
    assert len([r for r in result.result if r.node.resource_type.value == "model"]) == 5
    assert len([r for r in result.result if r.node.resource_type.value == "test"]) > 25


def test_dbt_marts_equal_the_python_pipeline_marts(built):
    _, db, _, expected = built
    con = duckdb.connect(str(db))  # dbt still holds the file in this process, so no read_only
    for table in MARTS:
        got = cents(con.sql(f"select * from {table} order by all").fetchall())
        assert got == expected[table], table
    con.close()


def test_dbt_tests_fail_on_bad_data(built):
    """The tests have teeth: a duplicated event and an event after the last drop are both caught."""
    _, db, target, _ = built
    con = duckdb.connect(str(db))
    con.execute(
        "insert into lake.attempts select * replace (date '2099-01-01' as event_day) "
        "from lake.attempts limit 1"
    )  # same event_id as an existing row, dated far after the last drop
    con.close()
    failed = {r.node.name for r in run_dbt("test", db, target).result if r.status == "fail"}
    assert "no_event_after_last_drop" in failed
    assert any(name.startswith("source_unique_lake_attempts_event_id") for name in failed)
