from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.attacks import ATTEMPT_SCHEMA
from fincrime.bank.events import ATTACK_OFFSET, MIRROR_OFFSET
from fincrime.bank.policy import FEATURES, RulePolicy
from fincrime.bank.rings import ring_device
from fincrime.bank.simulate import OBSERVABLE, TRUTH, build_bank

# High attack rates so that a small build has enough episodes to test.
A = Assumptions(
    n_customers=5_000,
    ato_per_1000_per_year=60.0,
    card_testing_per_1000_per_year=100.0,
    mule_rings_per_100k=300.0,
    scatter_gather_per_ring=2.0,
    structuring_per_ring=2.0,
)
PERMISSIVE = RulePolicy(
    name="off",
    velocity_limit=10**6,
    new_device_amount=1e12,
    night_amount=1e12,
    young_cp_amount=1e12,
    big_amount=1e12,
)
STRICT = RulePolicy(
    name="strict",
    velocity_limit=3,
    new_device_amount=500.0,
    night_amount=1_000.0,
    young_cp_amount=500.0,
    big_amount=10_000.0,
)


def _build(tmp_path_factory, policy, name):
    """Build one world and read it back from Parquet."""
    path = tmp_path_factory.mktemp(name)
    summary = build_bank(A, policy, path)
    return SimpleNamespace(
        path=path,
        summary=summary,
        attempts=pl.read_parquet(path / "attempts" / "*.parquet"),
        attacks=pl.read_parquet(path / "attempts" / "attacks.parquet"),
        episodes=pl.read_parquet(path / "episodes.parquet"),
        reports=pl.read_parquet(path / "fraud_reports.parquet"),
        customers=pl.read_parquet(path / "customers.parquet"),
    )


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return _build(tmp_path_factory, RulePolicy(), "base")


@pytest.fixture(scope="module")
def off(tmp_path_factory):
    return _build(tmp_path_factory, PERMISSIVE, "off")


@pytest.fixture(scope="module")
def strict(tmp_path_factory):
    return _build(tmp_path_factory, STRICT, "strict")


def test_layers_do_not_leak_truth_into_observables():
    assert set(OBSERVABLE).isdisjoint(TRUTH)
    assert set(OBSERVABLE) | set(TRUTH) == set(ATTEMPT_SCHEMA)


def test_schema_ids_and_status_follow_decision(base):
    at = base.attempts
    assert dict(at.schema) == ATTEMPT_SCHEMA
    assert at["event_id"].n_unique() == at.height
    ok = at.filter(pl.col("decision") == "approve")["status"].unique().to_list()
    assert ok == ["approved"]
    assert set(at.filter(pl.col("decision") == "decline")["status"].unique()) == {"declined"}
    assert set(at.filter(pl.col("decision") == "step_up")["status"].unique()) <= {
        "approved",
        "stepup_failed",
    }
    assert at["ts"].dt.date().max() <= A.end_date


def test_policy_vectorised_and_single_row_paths_agree():
    rng = np.random.default_rng(1)
    n = 400
    df = pl.DataFrame(
        {
            "event_type": rng.choice(
                ["spei_out", "card_online", "card_pos", "atm", "bill_pay", "spei_in"], n
            ),
            "amount": rng.lognormal(7, 1.5, n),
            "hour": rng.integers(0, 24, n),
            "device_age_minutes": rng.choice([0, 30, 5000, 10**9], n),
            "velocity_10m": rng.integers(0, 9, n),
            "cp_age_days": [None if x < 0 else int(x) for x in rng.choice([-1, 1, 5, 20, 400], n)],
        },
        schema_overrides={"cp_age_days": pl.Int64, "hour": pl.Int64, "velocity_10m": pl.Int64},
    )
    assert set(FEATURES) <= set(df.columns)
    out = RulePolicy().decide(df)
    for row in out.iter_rows(named=True):
        feats = {k: row[k] for k in FEATURES}
        assert RulePolicy().decide_one(**feats) == (row["decision"], row["rule"])


def test_non_digital_events_are_never_touched_by_rules():
    p = RulePolicy()
    for etype in ("atm", "card_pos", "spei_in", "cash_in", "payroll"):
        assert p.decide_one(
            event_type=etype,
            amount=1e9,
            hour=3,
            device_age_minutes=0,
            velocity_10m=99,
            cp_age_days=0,
        ) == ("approve", None)
    assert p.decide_one(
        event_type="card_online", amount=10, hour=12, device_age_minutes=10**6, velocity_10m=6,
        cp_age_days=None,
    ) == ("decline", "velocity")  # fmt: skip


def test_blocked_genuine_transfers_have_no_receiving_side(base):
    g = base.attempts.filter(pl.col("event_id") < ATTACK_OFFSET)
    mirrors = g.filter(pl.col("event_id") >= MIRROR_OFFSET)
    sent = g.filter(pl.col("event_id") < MIRROR_OFFSET).select("event_id", "status")
    joined = mirrors.with_columns(sender=pl.col("event_id") - MIRROR_OFFSET).join(
        sent, left_on="sender", right_on="event_id", suffix="_out"
    )
    assert joined.height == mirrors.height  # every receiving side has a sending side
    assert (joined["status_out"] == "approved").all()


def test_same_world_under_different_policies(base, off, strict):
    cols = ["episode_id", "fraud_type", "crime_class", "ring_id", "victim_account", "start_ts"]
    for other in (off, strict):
        assert (
            base.episodes.select(cols)
            .sort("episode_id")
            .equals(other.episodes.select(cols).sort("episode_id"))
        )
        assert base.customers.equals(other.customers)
    quiet = pl.col("fraud_type").is_in(["scatter_gather", "structuring", "mule_aging"])
    ref = base.attacks.filter(quiet).sort("event_id")
    for other in (off, strict):
        assert ref.equals(other.attacks.filter(quiet).sort("event_id"))
    # genuine events are identical too; only decisions and the status of blocked ones move
    gen = lambda w: w.attempts.filter(pl.col("event_id") < ATTACK_OFFSET)  # noqa: E731
    keep = gen(strict).select("event_id").join(gen(off).select("event_id"), on="event_id")
    assert keep.height > 0.999 * gen(off).height


def test_stricter_policy_trades_fraud_losses_for_customer_friction(base, off, strict):
    assert off.summary.genuine_blocked == 0
    assert (
        off.summary.genuine_blocked
        <= base.summary.genuine_blocked
        <= strict.summary.genuine_blocked
    )
    assert strict.summary.genuine_blocked > off.summary.genuine_blocked
    assert off.summary.fraud_lost_amount >= base.summary.fraud_lost_amount
    assert base.summary.fraud_lost_amount >= strict.summary.fraud_lost_amount
    assert off.summary.fraud_lost_amount > strict.summary.fraud_lost_amount


def test_episode_losses_reconcile_with_attempts(base):
    fraud = base.attacks.filter(pl.col("is_fraud") & (pl.col("event_type") != "spei_in"))
    by_ep = (
        fraud.filter(pl.col("status") == "approved")
        .group_by("episode_id")
        .agg(pl.col("amount").sum().alias("lost"))
    )
    ep = base.episodes.filter(pl.col("crime_class") == "fraud").join(
        by_ep, on="episode_id", how="left"
    )
    assert ep.height > 50
    diff = (ep["lost_amount"] - ep["lost"].fill_null(0.0)).abs().max()
    assert diff < 0.01
    attempted = fraud.group_by("episode_id").agg(pl.col("amount").sum().alias("att"))
    ep2 = base.episodes.filter(pl.col("crime_class") == "fraud").join(attempted, on="episode_id")
    assert ((ep2["attempted_amount"] - ep2["att"]).abs() < 0.01).all()


def test_blocked_fraud_is_kept_with_its_true_label(base):
    blocked = base.attacks.filter(pl.col("is_fraud") & (pl.col("status") != "approved"))
    assert blocked.height > 0
    assert blocked["amount"].sum() > 0  # the counterfactual loss is known


def test_attacker_adapts_after_a_block(base):
    ato = base.attacks.filter(
        (pl.col("fraud_type") == "ato") & (pl.col("event_type") == "spei_out")
    )
    ato = ato.filter(pl.col("is_fraud")).sort("episode_id", "ts")
    multi = ato.group_by("episode_id").agg(pl.col("amount"), pl.col("status"))
    multi = multi.filter(pl.col("amount").list.len() > 1)
    assert multi.height > 0
    for amounts, statuses in zip(multi["amount"], multi["status"], strict=True):
        assert statuses[0] != "approved"  # a retry only follows a block
        assert amounts[1] == pytest.approx(max(amounts[0] / 2, 50.0), abs=0.02)


def test_reports_are_late_partial_and_only_for_losses(base):
    rep = base.reports.join(
        base.attacks.select("event_id", "ts", "status", "is_fraud", "event_type"), on="event_id"
    )
    assert rep.height > 0
    assert (rep["status"] == "approved").all() and rep["is_fraud"].all()
    assert (rep["reported_ts"] > rep["ts"]).all()
    lost_eps = base.episodes.filter(
        (pl.col("crime_class") == "fraud") & (pl.col("lost_amount") > 0)
    )
    share = base.reports["episode_id"].n_unique() / lost_eps.height
    assert 0.65 < share < 0.95  # between the 80% and 92% report probabilities, with noise
    delay = rep.filter(pl.col("fraud_type") == "card_testing").select(
        (pl.col("reported_ts") - pl.col("ts")).dt.total_days().alias("d")
    )["d"]
    assert 15 < delay.median() < 60  # chargeback-like lag, median 30 days by assumption


def test_mule_rings_have_the_planted_observable_signals(base):
    mules = base.customers.filter(pl.col("fraud_role") == "mule")
    assert mules.height > 50
    span = mules.group_by("ring_id").agg(
        (pl.col("onboarded_on").max() - pl.col("onboarded_on").min()).dt.total_days().alias("span")
    )
    assert span["span"].max() <= 3  # a ring opens its accounts within days
    # the CURP often encodes a different date of birth than the one declared
    declared = mules["birth_date"].dt.strftime("%y%m%d")
    mismatch = (mules["curp"].str.slice(4, 6) != declared).mean()
    assert 0.45 < mismatch < 0.75
    # ring accounts share phones
    ev = base.attacks.filter(
        pl.col("device_id").is_not_null() & pl.col("ring_id").is_not_null()
        & pl.col("account_id").is_in(mules["customer_id"].implode())
    )  # fmt: skip
    pool = {ring_device(r, k) for r in mules["ring_id"].unique() for k in range(A.ring_device_pool)}
    assert set(ev["device_id"].unique()) <= pool
    per_device = ev.group_by("device_id").agg(pl.col("account_id").n_unique())
    assert per_device["account_id"].max() >= 3


def test_laundering_conserves_money_and_structuring_stays_under_the_line(base):
    at = base.attacks
    sg = at.filter(pl.col("fraud_type") == "scatter_gather")
    complete = 0
    for ep in sg["episode_id"].unique():
        e = sg.filter(pl.col("episode_id") == ep)
        inflow = e.filter(pl.col("counterparty_bank").is_not_null() & (pl.col("direction") == "in"))
        outflow = e.filter(
            pl.col("counterparty_bank").is_not_null() & (pl.col("direction") == "out")
        )
        if inflow.height == 1 and outflow.height == 1:
            ratio = outflow["amount"][0] / inflow["amount"][0]
            assert 0.80 < ratio < 0.95  # two skims and a 2% remainder
            complete += 1
    assert complete >= 3
    st = at.filter((pl.col("fraud_type") == "structuring") & (pl.col("event_type") == "cash_in"))
    assert st.height > 20
    assert (st["amount"] < A.structuring_threshold_mxn).all()
    assert (st["amount"] >= 0.70 * A.structuring_threshold_mxn - 0.01).all()


def test_build_is_deterministic(tmp_path_factory, base):
    again = _build(tmp_path_factory, RulePolicy(), "again")
    assert again.attacks.equals(base.attacks)
    assert again.episodes.equals(base.episodes)
    assert again.reports.equals(base.reports)
    assert again.attempts.sort("event_id").equals(base.attempts.sort("event_id"))
