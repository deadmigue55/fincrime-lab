import shutil
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.policy import RulePolicy
from fincrime.bank.simulate import build_bank
from fincrime.lake import contracts, landing, warehouse
from fincrime.lake.loader import Lake

A = Assumptions(
    n_customers=2_500,
    start_date=date(2024, 1, 1),
    end_date=date(2024, 2, 5),
    ato_per_1000_per_year=150.0,
    card_testing_per_1000_per_year=200.0,
    mule_rings_per_100k=200.0,
)
MARTS = (
    "mart.daily_kpis",
    "mart.account_day",
    "mart.device_account_day",
    "mart.transfer_edge_day",
    "eval.daily_fraud",
)


def posix(path: Path) -> str:
    return path.as_posix()


@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    """A small bank, built once and split into daily drops."""
    root = tmp_path_factory.mktemp("platform")
    build_bank(A, RulePolicy(), root / "bank")
    counts = landing.export_landing(root / "bank", root / "landing")
    return root, counts


def new_lake(root: Path, name: str, source: Path | None = None) -> Lake:
    return Lake(source or root / "landing", root / f"lake-{name}", None)


def cents(rows: list) -> list:
    """Round money to the cent: parallel sums add in different orders, so the last digits wobble."""
    return [tuple(round(v, 2) if isinstance(v, float) else v for v in row) for row in rows]


def snapshot(lake: Lake) -> dict:
    """Everything a reader could observe: lake contents and marts, order independent."""
    con = lake.con
    out = {
        "lake": cents(
            con.sql(
                "select count(*), sum(amount), count(distinct event_id) from lake.attempts"
            ).fetchall()
        ),
        "truth": con.sql("select count(*) from lake.truth").fetchall(),
    }
    for t in MARTS:
        out[t] = cents(con.sql(f"select * from {t} order by all").fetchall())
    return out


def test_landing_split_by_arrival_with_late_events(landed):
    root, counts = landed
    drops = landing.drops(root / "landing")
    assert len(drops) > 30
    assert counts["attempts"] == counts["truth"] > 20_000
    land = posix(root / "landing")
    late = duckdb.sql(
        "select avg((arrival > ts::date)::int) from (select ts, "
        "regexp_extract(filename, 'arrival=([0-9-]+)', 1)::date as arrival "
        f"from read_parquet('{land}/attempts/*/*.parquet', filename=true))"
    ).fetchall()[0][0]
    assert 0.02 < late < 0.04  # about 3% of events arrive late
    # observables and truth are separate: no truth column in the attempts landing
    cols = {
        r[0]
        for r in duckdb.sql(
            f"describe select * from read_parquet('{land}/attempts/*/*.parquet')"
        ).fetchall()
    }
    assert cols.isdisjoint({"is_fraud", "fraud_type", "crime_class", "ring_id", "episode_id"})
    dims = {
        r[0]
        for r in duckdb.sql(
            f"describe select * from read_parquet('{land}/dim/accounts.parquet')"
        ).fetchall()
    }
    assert dims.isdisjoint({"fraud_role", "ring_id"})


def test_full_load_then_reload_is_a_noop(landed):
    root, _ = landed
    lake = new_lake(root, "full")
    results = lake.load_pending()
    assert results
    assert all(r.status == "loaded" for r in results), [r for r in results if r.status != "loaded"]
    first = snapshot(lake)
    assert lake.load_pending() == []  # nothing pending
    again = [lake.load_drop(d) for d in landing.drops(root / "landing")]
    assert all(r.status == "skipped" for r in again)
    assert snapshot(lake) == first
    # forced reload rewrites the same data: same lake, same marts
    for d in landing.drops(root / "landing")[:6]:
        assert lake.load_drop(d, force=True).status == "loaded"
    assert snapshot(lake) == first
    lake.close()


def test_incremental_loading_in_pieces_equals_loading_at_once(landed):
    root, _ = landed
    whole = new_lake(root, "whole")
    whole.load_pending()
    pieces = new_lake(root, "pieces")
    pieces.load_pending(limit=10)
    assert snapshot(pieces) != snapshot(whole)  # not finished yet
    pieces.load_pending(limit=10)
    pieces.load_pending()
    assert snapshot(pieces) == snapshot(whole)
    whole.close()
    pieces.close()


def test_marts_agree_with_the_lake_and_with_the_source(landed):
    root, counts = landed
    lake = new_lake(root, "check")
    lake.load_pending()
    con = lake.con
    bank = posix(root / "bank")
    k = con.sql(
        "select sum(attempts), sum(approved + declined + stepup_failed) from mart.daily_kpis"
    ).fetchall()[0]
    assert k[0] == k[1] == counts["attempts"]
    assert (
        con.sql("select sum(n_attempts) from mart.account_day").fetchall()[0][0]
        == counts["attempts"]
    )
    # marts built from the lake equal the same query run straight on the source bank files
    src = con.sql(
        f"select count(*) from read_parquet('{bank}/attempts/*.parquet') where status = 'approved'"
    ).fetchall()[0][0]
    assert con.sql("select sum(approved) from mart.daily_kpis").fetchall()[0][0] == src
    # late data was filed under the day it happened, not the day it arrived
    days = con.sql("select count(distinct event_day) from lake.attempts").fetchall()[0][0]
    assert days == (A.end_date - A.start_date).days + 1
    # truth stays in eval, observables in mart
    fraud = con.sql("select sum(fraud_attempts) from eval.daily_fraud").fetchall()[0][0]
    direct = con.sql(
        f"select count(*) filter (where is_fraud and event_type <> 'spei_in') from read_parquet('{bank}/attempts/*.parquet')"
    ).fetchall()[0][0]
    assert fraud == direct > 0
    reports = con.sql("select count(*) from lake.reports").fetchall()[0][0]
    assert reports == pl.read_parquet(root / "bank" / "fraud_reports.parquet").height
    lake.close()


def corrupted_landing(root: Path, name: str, drop: str, transform: str) -> Path:
    """Copy the landing zone and rewrite one drop's attempts file with a SQL transformation."""
    dest = root / f"landing-{name}"
    if not dest.exists():
        shutil.copytree(root / "landing", dest)
    f = next((dest / "attempts" / f"arrival={drop}").glob("*.parquet"))
    new = f.with_suffix(".new")
    sql = transform.replace("SRC", f"read_parquet('{posix(f)}')")
    duckdb.sql(f"copy ({sql}) to '{posix(new)}' (format parquet)")
    f.unlink()
    new.rename(f)
    return dest


BAD_DROPS = [
    ("negative", "select * replace (-amount as amount) from SRC", "amount_positive"),
    ("dupes", "select * from SRC union all select * from SRC", "event_id_unique_in_drop"),
    ("badstatus", "select * replace ('weird' as status) from SRC", "allowed_values"),
    (
        "noaccount",
        "select * replace (account_id + 99000000 as account_id) from SRC",
        "account_exists",
    ),
    ("future", "select * replace (ts + interval 40 day as ts) from SRC", "not_from_the_future"),
]


@pytest.mark.parametrize(("name", "transform", "expected"), BAD_DROPS)
def test_bad_drop_is_quarantined_and_publishes_nothing(landed, name, transform, expected):
    root, _ = landed
    drop = landing.drops(root / "landing")[12]
    bad = corrupted_landing(root, name, drop, transform)
    lake = new_lake(root, f"bad-{name}", bad)
    lake.load_pending(limit=12)
    before = snapshot(lake)
    result = lake.load_drop(drop)
    assert result.status == "quarantined"
    assert any(expected in f for f in result.failed), result.failed
    assert snapshot(lake) == before  # nothing from the bad drop reached the lake or the marts
    assert lake.ledger(drop)[0] == "quarantined"
    failed = lake.con.sql(
        f"select contract from ops.dq_results where drop_id = '{drop}' and not passed"
    ).fetchall()
    assert expected in [r[0] for r in failed]
    lake.close()


def test_redelivered_drop_replaces_the_old_version_without_duplicates(landed):
    root, _ = landed
    drop = landing.drops(root / "landing")[20]
    db, lake_dir = root / "redeliver.duckdb", root / "lake-redeliver"
    lake = Lake(root / "landing", lake_dir, db)
    lake.load_pending(limit=25)
    n, total = lake.con.sql("select count(*), sum(amount) from lake.attempts").fetchall()[0]
    size = lake.con.sql(
        f"select count(*) from lake.attempts where filename like '%drop-{drop}-%'"
    ).fetchall()[0][0]
    lake.close()
    # the source corrects that drop: every amount is one peso higher. Same warehouse, new delivery.
    fixed = corrupted_landing(
        root, "redeliver", drop, "select * replace (amount + 1 as amount) from SRC"
    )
    lake2 = Lake(fixed, lake_dir, db)
    assert lake2.pending()[0] == drop  # a changed delivery is noticed by its fingerprint
    assert lake2.load_drop(drop).status == "loaded"
    n2, total2 = lake2.con.sql("select count(*), sum(amount) from lake.attempts").fetchall()[0]
    assert n2 == n  # no duplicates left behind
    assert total2 == pytest.approx(
        total + size, abs=0.01
    )  # one peso more on each event of that drop
    kpi = lake2.con.sql("select sum(approved_amount) from mart.daily_kpis").fetchall()[0][0]
    assert kpi > 0  # marts for the affected days were rebuilt without error
    lake2.close()


def test_crash_after_publish_but_before_the_ledger_is_recovered(landed, monkeypatch):
    root, _ = landed
    lake = new_lake(root, "crash")
    lake.load_pending(limit=5)
    drop = landing.drops(root / "landing")[5]
    original = lake._set_ledger

    def power_cut(drop_id, status, **kw):
        if status == "loaded":
            raise RuntimeError("power cut")
        return original(drop_id, status, **kw)

    monkeypatch.setattr(lake, "_set_ledger", power_cut)
    with pytest.raises(RuntimeError):
        lake.load_drop(drop)
    monkeypatch.setattr(lake, "_set_ledger", original)
    assert lake.ledger(drop)[0] == "started"
    assert drop in lake.pending()  # an interrupted drop is picked up again
    lake.load_pending()
    clean = new_lake(root, "crash-clean")
    clean.load_pending()
    assert snapshot(lake) == snapshot(clean)  # recovery leaves no duplicates or gaps
    lake.close()
    clean.close()


def test_sql_statement_splitter_and_lake_views():
    sql = (
        "-- comment; with semicolon\ncreate table t(x int); -- trailing\ninsert into t values (1);"
    )
    assert warehouse.statements(sql) == ["create table t(x int)", "insert into t values (1)"]
    con = warehouse.connect(None, Path("does-not-exist"))
    assert not warehouse.has_lake(con, "attempts")


def staged(rows: str, registry: list[int] | None = None):
    """An in-memory warehouse with a one-row report drop staged and an optional event registry."""
    con = warehouse.connect(None, Path("does-not-exist"))
    con.execute("create temp view stg_attempts as select 0::bigint as event_id where false")
    con.execute(
        f"create temp view stg_reports as select * from (values {rows}) t(event_id, reported_ts)"
    )
    for event_id in registry or []:
        con.execute(
            "insert into ops.event_registry values (?, date '2024-01-01', 'earlier')", [event_id]
        )
    return con


def test_report_before_its_event_is_a_warning_not_a_rejection():
    con = staged("(42, timestamp '2024-01-03 10:00:00')")
    results = contracts.run(con, "2024-01-03", rows=1, has_attempts=False, has_reports=True)
    orphan = next(r for r in results if r.contract == "report_references_known_event")
    assert (orphan.severity, orphan.passed, orphan.violations) == ("warn", False, 1)
    assert contracts.blocking(results) == []  # out-of-order delivery must not reject anything
    assert warehouse.orphan_reports(con) == 0  # nothing in the lake yet, so nothing to count
    # once the event is known, the same report is fine
    con = staged("(42, timestamp '2024-01-03 10:00:00')", registry=[42])
    results = contracts.run(con, "2024-01-03", rows=1, has_attempts=False, has_reports=True)
    assert all(r.passed for r in results)


def test_malformed_report_is_an_error_that_belongs_to_reports_only():
    con = staged("(42, null)", registry=[42])
    results = contracts.run(con, "2024-01-03", rows=1, has_attempts=False, has_reports=True)
    bad = contracts.blocking(results)
    assert [(r.contract, r.kind) for r in bad] == [("report_keys_not_null", "reports")]


def test_bad_reports_hold_back_only_the_reports(landed):
    root, _ = landed
    drops = [
        d
        for d in landing.drops(root / "landing")
        if (root / "landing" / "reports" / f"arrival={d}").exists()
        and (root / "landing" / "attempts" / f"arrival={d}").exists()
    ]
    drop = drops[len(drops) // 2]
    bad = root / "landing-badreports"
    if not bad.exists():
        shutil.copytree(root / "landing", bad)
    f = next((bad / "reports" / f"arrival={drop}").glob("*.parquet"))
    new = f.with_suffix(".new")
    duckdb.sql(
        f"copy (select * replace (null::timestamp as reported_ts) from read_parquet('{posix(f)}')) "
        f"to '{posix(new)}' (format parquet)"
    )
    f.unlink()
    new.rename(f)
    lake = new_lake(root, "badreports", bad)
    lake.load_pending()
    # the drop itself is loaded: its transactions are not punished for a malformed reports file
    assert lake.ledger(drop)[0] == "loaded"
    held = lake.con.sql(f"select message from ops.load_ledger where drop_id = '{drop}'").fetchall()[
        0
    ][0]
    assert "reports held back" in held and "report_keys_not_null" in held
    n_tx = lake.con.sql(
        f"select count(*) from lake.attempts where filename like '%drop-{drop}-%'"
    ).fetchall()[0][0]
    assert n_tx > 0
    assert (
        lake.con.sql(
            f"select count(*) from lake.reports where filename like '%drop-{drop}-%'"
        ).fetchall()[0][0]
        == 0
    )
    # every other drop's reports are present
    assert lake.con.sql("select count(*) from lake.reports").fetchall()[0][0] > 0
    lake.close()


def test_full_run_resolves_all_out_of_order_reports(landed):
    root, _ = landed
    lake = new_lake(root, "orphans")
    results = lake.load_pending()
    assert not [r for r in results if r.status == "quarantined"]
    assert warehouse.orphan_reports(lake.con) == 0  # every report found its event in the end
    lake.close()
