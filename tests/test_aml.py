"""The AML module: weekly tables, point-in-time snapshots, cases, and the benchmark state."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from fincrime.aml import amlworld, build, detect, evaluate
from fincrime.aml import snapshots as sn
from fincrime.bank.assumptions import Assumptions
from fincrime.bank.policy import RulePolicy
from fincrime.bank.simulate import build_bank

A = Assumptions(n_customers=6_000, mule_rings_per_100k=300.0)
START = np.datetime64(build.START)
DAY0 = datetime(2024, 1, 1)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("aml")
    bank = root / "bank"
    build_bank(A, RulePolicy(), bank)
    build.build(bank, root / "aml")
    return sn.Tables(root / "aml", bank), bank


def test_truth_holds_only_planted_laundering(world):
    t, _ = world
    assert set(t.truth["fraud_type"].unique()) <= {
        "scatter_gather", "structuring", "mule_aging", "mule_forward",
    }  # fmt: skip
    assert t.truth.height > 0 and t.rules.height > 0


def test_folds_keep_every_ring_in_one_fold(world):
    t, bank = world
    fold = detect.folds(bank, t.n)
    cust = pl.read_parquet(bank / "customers.parquet")
    acc = pl.read_parquet(bank / "accounts.parquet").join(cust, on="customer_id")
    rings = acc.filter(pl.col("ring_id").is_not_null())
    per_ring = rings.with_columns(fold=pl.Series(fold[rings["account_id"].to_numpy()]))
    assert per_ring.group_by("ring_id").agg(pl.col("fold").n_unique())["fold"].max() == 1
    assert len(set(per_ring["fold"].to_list())) > 1  # and the rings are spread over the folds


def test_a_snapshot_never_reads_past_its_week(world):
    t, _ = world
    w = 20
    full = sn.snapshot(t, w, 4, START).sort("account_id")
    cut = SimpleNamespace(**vars(t))
    for name in ("aw", "edges", "dev", "rules", "truth"):
        cut.__dict__[name] = getattr(t, name).filter(pl.col("w") <= w)
    early = sn.snapshot(cut, w, 4, START).sort("account_id")  # type: ignore[arg-type]
    assert full.equals(early)


def _events(rows):
    cols = ["account_id", "day", "hour", "event_type", "direction", "amount", "cp"]
    df = pl.DataFrame(rows, schema=cols, orient="row")
    return df.select(
        "account_id",
        pl.struct("day", "hour").map_elements(
            lambda r: DAY0 + timedelta(days=r["day"], hours=r["hour"]), return_dtype=pl.Datetime
        ).alias("ts"),
        "event_type", "direction", "amount",
        pl.col("cp").alias("counterparty_account"),
        pl.lit(None, dtype=pl.String).alias("device_id"),
        pl.lit("approved").alias("status"),
        pl.lit(None, dtype=pl.String).alias("crime_class"),
        pl.lit(None, dtype=pl.String).alias("fraud_type"),
        pl.lit(None, dtype=pl.Int64).alias("episode_id"),
        pl.lit(None, dtype=pl.Int64).alias("ring_id"),
    )  # fmt: skip


def test_daily_rules_flag_the_day_of_the_event_and_after_never_before(tmp_path):
    rows = [(1, 2, h, "cash_in", "in", 14_000.0, None) for h in (9, 10, 11)]  # R1: 3 near-line
    rows += [
        (2, 10, 9, "spei_in", "in", 60_000.0, None),
        (2, 11, 9, "spei_out", "out", 50_000.0, 9),
    ]
    rows += [(4, 20, h, "spei_out", "out", 30_000.0, 100 + h) for h in (8, 9, 10, 11)]  # R3
    (tmp_path / "bank" / "attempts").mkdir(parents=True)
    _events(rows).write_parquet(tmp_path / "bank" / "attempts" / "p.parquet")
    build.build(tmp_path / "bank", tmp_path / "aml")
    rules = pl.read_parquet(tmp_path / "aml" / "rules.parquet")

    def weeks(a, r):
        return sorted(rules.filter((pl.col("account_id") == a) & pl.col(r))["w"].to_list())

    assert weeks(1, "r1") == [0, 1]  # day 2 to day 8: a flag one day early would put one in week -1
    assert weeks(2, "r2") == [1]  # received on day 10, sent on day 11: flagged on day 11 only
    assert weeks(4, "r3") == [2, 3]  # day 20 and the day after, which falls in the next week


def test_cases_join_accounts_that_share_a_transfer_or_a_phone():
    edges = pl.DataFrame({"w": [3], "src": [1], "dst": [2], "n": [1], "amt": [10.0]})
    dev = pl.DataFrame({"w": [3, 3], "device_id": ["d", "d"], "account_id": [3, 4]})
    t = SimpleNamespace(n=10, edges=edges, dev=dev)
    got = detect.cases(t, 3, 4, np.array([1, 3, 5, 2, 4]))  # type: ignore[arg-type]
    assert [a.tolist() for a, _ in got] == [[1, 2], [3, 4], [5]]  # best first, members by rank
    assert [r.tolist() for _, r in got] == [[0, 3], [1, 4], [2]]


def test_a_budget_works_only_the_accounts_ranked_above_the_next_case():
    wk = [
        (np.array([1, 2]), np.array([0, 5])),  # case 1: its second account is ranked far down
        (np.array([3]), np.array([1])),
        (np.array([4]), np.array([2])),
    ]
    got = evaluate.members(wk, 2)
    assert [a.tolist() for a in got] == [[1], [3]]  # account 2 (rank 5) is past case 3's rank 2
    assert [a.tolist() for a in evaluate.members(wk, 3)] == [[1, 2], [3], [4]]  # no next case
    assert [a.tolist() for a in evaluate.members(wk, None)] == [[1, 2], [3], [4]]


def test_an_episode_is_detected_only_in_its_active_weeks_or_the_next():
    t0 = datetime(2024, 1, 15)  # a Monday, the start of week 2
    eps = pl.DataFrame({"accounts": [[7], [8]], "w0": [2, 2], "w1": [3, 3], "t0": [t0, t0]})
    one = lambda x: [(np.array(x), np.array([0]))]  # noqa: E731
    cases = [[] for _ in range(8)]
    cases[4] = one([7])  # the week after the episode ends: counts
    cases[6] = one([8])  # two weeks after: does not
    d = evaluate.detection(eps, cases, None, 8)
    assert d["flag"].tolist() == [True, False]
    assert d["delay"] == [21.0, None]  # from Monday 15 Jan to the end of week 4 (5 Feb)


def test_bootstrap_resamples_whole_rings():
    eps = pl.DataFrame({"ring_id": [1, 1, 2, 2]})
    out = evaluate.bootstrap(eps, {"a": np.array([True, True, False, False])}, reps=500)
    assert out["a"]["recall"] == 0.5
    assert out["a"]["lo"] == 0.0 and out["a"]["hi"] == 1.0  # one ring in, or the other


def test_benchmark_features_use_the_state_before_the_transaction():
    ts = pl.datetime_range(pl.datetime(2022, 9, 1), pl.datetime(2022, 9, 1, 0, 3), "1m", eager=True)
    tx = pl.DataFrame(
        {
            "i": [0, 1, 2, 3],
            "ts": ts,
            "src": ["a", "a", "b", "a"],
            "dst": ["b", "c", "a", "b"],
            "amount_paid": [10.0, 20.0, 5.0, 1.0],
            "amount_received": [10.0, 20.0, 5.0, 1.0],
        }
    )
    st = amlworld._states(tx)
    sender = st.filter(pl.col("is_out") == 1).sort("i")
    assert sender["out_n"].to_list() == [0, 1, 0, 2]  # a: nothing, then one, b: nothing, a: two
    assert sender["out_dist"].to_list() == [0, 1, 0, 2]  # a had sent to b and c before i=3
    assert sender["in_amt"].to_list() == [0.0, 0.0, 10.0, 5.0]  # a got 5 from b before i=3
    receiver = st.filter(pl.col("is_out") == 0).sort("i")
    assert receiver["in_n"].to_list() == [0, 0, 0, 1]  # b had received once from a before i=3


def test_benchmark_rows_in_the_same_minute_do_not_see_each_other():
    ts = [datetime(2022, 9, 1, 0, 0)] * 2 + [datetime(2022, 9, 1, 0, 1)]
    tx = pl.DataFrame(
        {
            "i": [0, 1, 2],
            "ts": ts,
            "src": ["a", "a", "a"],
            "dst": ["b", "c", "b"],
            "amount_paid": [10.0, 20.0, 1.0],
            "amount_received": [10.0, 20.0, 1.0],
        }
    )
    sender = amlworld._states(tx).filter(pl.col("is_out") == 1).sort("i")
    assert sender["out_n"].to_list() == [
        0,
        0,
        2,
    ]  # the second row of minute 0 has not seen the first
    assert sender["out_dist"].to_list() == [0, 0, 2]
