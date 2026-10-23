from datetime import datetime, timedelta

import duckdb
import polars as pl
import pytest

from fincrime.lake import bench_report, benchmark


@pytest.fixture
def tiny(tmp_path):
    """A 2,000-row stand-in for the AMLworld transactions and accounts files."""
    n = 2_000
    start = datetime(2022, 9, 1)
    trans = pl.DataFrame(
        {
            "txn_id": list(range(1, n + 1)),
            "ts": [start + timedelta(minutes=i) for i in range(n)],
            "from_bank": ["010"] * n,
            "from_account": [f"A{i % 50}" for i in range(n)],
            "to_account": [f"A{(i * 7) % 50}" for i in range(n)],
            "amount_paid": [float(10 + i % 90) for i in range(n)],
            "payment_format": ["ACH" if i % 3 else "Cheque" for i in range(n)],
        }
    )
    accounts = pl.DataFrame(
        {
            "bank_id": ["010"] * 50,
            "account_number": [f"A{i}" for i in range(50)],
            "bank_name": ["Bank X"] * 50,
        }
    )
    t, a = tmp_path / "t.parquet", tmp_path / "a.parquet"
    trans.write_parquet(t)
    accounts.write_parquet(a)
    return t, a, tmp_path


QUERIES = {k: benchmark.QUERIES[k] for k in ("scan_aggregate", "join_dimension")}


def test_results_are_saved_as_they_arrive_and_resume_skips_them(tiny):
    t, a, tmp = tiny
    out = tmp / "r.csv"
    seen = []
    res = benchmark.benchmark(
        t, a, (500, 1_000), QUERIES, repeats=2, csv_path=out, progress=seen.append
    )
    assert [(m.rows, m.query, m.status) for m in res] == [
        (500, "scan_aggregate", "ok"),
        (500, "join_dimension", "ok"),
        (1000, "scan_aggregate", "ok"),
        (1000, "join_dimension", "ok"),
    ]
    saved = benchmark.read_csv(out)
    assert len(saved) == 4 and all(m.median_s > 0 and m.peak_mb > 0 for m in saved)
    # resume: nothing is measured again, the file does not grow, and a new size is added
    seen.clear()
    res2 = benchmark.benchmark(
        t,
        a,
        (500, 1_000, None),
        QUERIES,
        repeats=1,
        csv_path=out,
        resume=True,
        progress=seen.append,
    )
    assert sum("already measured" in s for s in seen) == 4
    assert len(benchmark.read_csv(out)) == 6 and len(res2) == 6
    assert {m.rows for m in res2} == {500, 1000, 2000}  # None means every row
    # a fresh write replaces the file
    benchmark.write_csv(out, res2[:1])
    assert len(benchmark.read_csv(out)) == 1


def test_memory_cap_is_applied_and_reported(tiny):
    t, a, tmp = tiny
    seen = []
    benchmark.benchmark(
        t,
        a,
        (500,),
        {"scan_aggregate": QUERIES["scan_aggregate"]},
        repeats=1,
        memory_limit_gb=3,
        temp_dir=tmp / "spill",
        progress=seen.append,
    )
    assert any("memory limit 3 GB" in s for s in seen)
    assert (tmp / "spill").is_dir()
    assert benchmark.default_memory_limit_gb() >= 2


def test_a_failing_query_is_recorded_not_raised():
    con = duckdb.connect()
    status, timings, peak, detail = benchmark.run_query(
        con, "select * from does_not_exist", repeats=2
    )
    assert status == "error" and timings == [] and "does_not_exist" in detail and peak > 0


def test_slow_first_run_is_not_repeated(monkeypatch):
    monkeypatch.setattr(benchmark, "SLOW_FIRST_RUN_S", -1.0)  # every run counts as slow
    con = duckdb.connect()
    status, timings, _, _ = benchmark.run_query(con, "select 1", repeats=5)
    assert status == "ok" and len(timings) == 1


def test_report_renders_a_table_and_figures(tiny, tmp_path):
    t, a, tmp = tiny
    out = tmp / "r.csv"
    benchmark.benchmark(
        t,
        a,
        (500, 1_000, None),
        benchmark.QUERIES,
        repeats=1,
        csv_path=out,
        progress=lambda s: None,
    )
    made = bench_report.build(out, tmp / "report.md", tmp / "img")
    text = (tmp / "report.md").read_text(encoding="utf-8")
    assert "| Scan and aggregate |" in text and "Growth 1M to max" in text
    assert (tmp / "img" / "lake_scaling.png").stat().st_size > 10_000
    assert len(made) == 2  # no layout file, so no layout figure
