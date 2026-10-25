"""The counterfactual worlds: same people, same attackers, different rules; outcomes by customer."""

from datetime import datetime

import numpy as np
import polars as pl
import pytest

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.policy import RulePolicy
from fincrime.bank.simulate import build_bank
from fincrime.experiments import outcomes, worlds

A = Assumptions(
    n_customers=6_000,
    ato_per_1000_per_year=60.0,
    card_testing_per_1000_per_year=100.0,
    mule_rings_per_100k=300.0,
    scatter_gather_per_ring=2.0,
    structuring_per_ring=2.0,
)
STRICT = RulePolicy(
    name="strict",
    velocity_limit=3,
    new_device_amount=500.0,
    night_amount=1_000.0,
    young_cp_amount=500.0,
)


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("exp")
    for name, policy in (("baseline", RulePolicy()), ("strict", STRICT)):
        work = root / f"bank_{name}"
        build_bank(A, policy, work)
        dest = root / name
        dest.mkdir()
        for key, df in worlds.extract(work).items():
            df.write_parquet(dest / f"{key}.parquet")
        if name == "baseline":
            base_dir = work
    return outcomes.load(root, base_dir, ["strict"]), root, base_dir


def test_the_population_is_every_genuine_customer_and_ids_are_sorted(bench):
    b, _, base = bench
    cust = pl.read_parquet(base / "customers.parquet")
    assert b.n == cust.filter(pl.col("fraud_role").is_null()).height
    assert (np.diff(b.ids) > 0).all()


def test_both_worlds_contain_the_same_fraud_episodes(bench):
    """The rules decide what is lost; they do not change who is attacked or when."""
    _, root, _ = bench
    a = pl.read_parquet(root / "baseline" / "episodes.parquet").select("episode_id", "customer_id")
    s = pl.read_parquet(root / "strict" / "episodes.parquet").select("episode_id", "customer_id")
    assert a.sort("episode_id").equals(s.sort("episode_id"))


def test_a_stricter_rule_cannot_raise_loss_and_does_raise_friction(bench):
    b, _, _ = bench
    truth = dict(zip(outcomes.METRICS, b.truth("strict"), strict=True))
    assert truth["loss"] < 0
    assert truth["friction"] > 0 and truth["stepups"] > 0
    # no customer is worse off in loss terms: a tighter rule only removes approvals
    base, treated = b.potential("strict")
    assert (treated[:, 0] <= base[:, 0] + 1e-6).all()


def test_the_metrics_match_a_direct_computation_from_the_attempts(bench):
    b, _, base = bench
    att = pl.read_parquet(base / "attempts" / "attacks.parquet")
    direct = att.filter(
        pl.col("is_fraud")
        & (pl.col("crime_class") == "fraud")
        & (pl.col("status") == "approved")
        & (pl.col("direction") == "out")
        & (pl.col("ts") >= datetime(2025, 1, 1))
    )["amount"].sum()
    assert abs(b.arms["baseline"].y[:, 0].sum() - direct) < 0.01
    # the weekly table ends at the loss of the first 25 weeks, never above the whole window
    assert (b.arms["baseline"].weekly["loss"][:, -1] <= b.arms["baseline"].y[:, 0] + 1e-6).all()


def test_reported_loss_never_exceeds_true_loss_and_the_pre_period_is_filled(bench):
    b, _, _ = bench
    for arm in b.arms.values():
        assert (arm.weekly["loss_reported"] <= arm.weekly["loss"] + 1e-6).all()
    assert b.pre[:, 0].sum() > 0  # fraud happened before the experiment too
    assert b.x.shape == (b.n, 5)


def test_the_weekly_friction_table_is_a_cumulative_indicator(bench):
    b, _, _ = bench
    f = b.arms["strict"].weekly["friction"]
    assert (np.diff(f, axis=1) >= 0).all() and f.max() <= 1.0


def test_completion_weights_scale_reported_loss_up_and_never_below_it(bench):
    from datetime import date

    b, _, base = bench
    fits = outcomes.completion_fits(base, date(2024, 12, 31))
    arm = b.arms["baseline"]
    w = outcomes.weighted_weekly(arm, fits, b.n)
    reported = arm.weekly["loss_reported"]
    assert (w >= reported - 1e-6).all()  # every weight is at least 1
    assert w[:, -1].sum() > reported[:, -1].sum()
    # right after the start only a few claims have matured, so they carry large weights
    assert (w[:, 1].sum() / max(reported[:, 1].sum(), 1e-9)) >= (
        w[:, -1].sum() / reported[:, -1].sum()
    )
    assert set(fits) == {"ato", "card_testing"}
    # the readout targets losses that will eventually be reported, never more than all losses
    assert (arm.weekly["loss_ultimate"] <= arm.weekly["loss"] + 1e-6).all()
    assert (arm.weekly["loss_reported"] <= arm.weekly["loss_ultimate"] + 1e-6).all()
