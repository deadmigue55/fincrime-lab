from pathlib import Path

from test_lake import landed, new_lake  # noqa: F401  (``landed`` is a fixture)

DRILLS = sorted(
    p
    for p in (Path(__file__).resolve().parents[1] / "drills").glob("[0-9][0-9]_*.sql")
    if not p.name.endswith(".databricks.sql")
)


def test_thirty_numbered_drills_each_with_a_question():
    assert [int(p.name[:2]) for p in DRILLS] == list(range(1, 31))
    for p in DRILLS:
        first, second = p.read_text(encoding="utf-8").splitlines()[:2]
        assert first.startswith(f"-- Drill {p.name[:2]} |")
        assert second.startswith("-- Question: ")


def test_every_drill_runs_on_a_small_bank(landed):  # noqa: F811
    """Right table and column names, valid SQL. The answers on the full data are in drills/answers.md."""
    root, _ = landed
    lake = new_lake(root, "drills")
    lake.load_pending()
    for drill in DRILLS:
        try:
            lake.con.sql(drill.read_text(encoding="utf-8")).fetchall()
        except Exception as e:
            raise AssertionError(f"{drill.name}: {e}") from e
    lake.close()
