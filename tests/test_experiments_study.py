"""The validation studies on a small synthetic bench where the effects are planted by hand."""

import numpy as np
import polars as pl

from fincrime.experiments import outcomes, sequential, study

N = 6000
W = outcomes.WEEKS


def _bench(effect=-0.3, seed=0, N=N) -> outcomes.Bench:
    """Rare heavy-tailed loss, a friction indicator and step-ups; the rule cuts loss and adds friction."""
    rng = np.random.default_rng(seed)
    tenure = rng.uniform(-100, 500, N)
    hit = rng.random(N) < 0.05 + 0.0001 * np.clip(tenure, 0, None)
    loss_a = np.where(hit, rng.lognormal(5.0, 1.0, N), 0.0)
    cut = rng.random(N) < -effect  # the rule prevents this share of the losses
    loss_b = np.where(cut, 0.0, loss_a)
    fric_a = (rng.random(N) < 0.02).astype(float)
    fric_b = np.maximum(fric_a, (rng.random(N) < 0.02).astype(float))
    pre_step = rng.poisson(0.4, N).astype(float)
    step_a = pre_step + rng.poisson(0.1, N)
    step_b = step_a + rng.poisson(0.5, N)

    def table(loss, fric, step):
        y = np.zeros((N, len(outcomes.METRICS)))
        y[:, 0], y[:, 1], y[:, 4], y[:, 5] = loss, loss > 0, fric, step
        y[:, 2], y[:, 3] = loss * (tenure > 200), loss * (tenure <= 200)
        # each customer's events land in one random week, so the looks are correlated but not equal
        week = rng.integers(0, W, (N, 1))
        ramp = (np.arange(W)[None, :] >= week).astype(float)
        weekly = {"loss": loss[:, None] * ramp, "friction": fric[:, None] * ramp}
        weekly["loss_reported"] = weekly["loss_ultimate"] = weekly["loss"]
        weekly["stepups"] = step[:, None] * ramp
        return outcomes.Arm("x", y, pl.DataFrame(), weekly, np.ones((N, 2)), (fric > 0) * 1.0)

    pre = np.column_stack(
        [rng.poisson(0.2, N) * 100.0, rng.poisson(0.01, N) * 1.0]
        + [np.zeros(N)] * 2
        + [(rng.random(N) < 0.02) * 1.0, pre_step]
    )
    x = np.column_stack([rng.normal(size=N), rng.normal(size=N), pre[:, 4], pre[:, 5], tenure])
    arms = {"baseline": table(loss_a, fric_a, step_a), "rule": table(loss_b, fric_b, step_b)}
    return outcomes.Bench(np.arange(N), x, pre, tenure, arms)


def test_a_a_holds_its_level_and_a_planted_effect_is_recovered():
    b = _bench()
    reps = 500
    lo, hi = study.band(reps)
    aa = study.replicate(b, None, reps, 1)
    for row in aa.summary():
        if row["metric"] in ("loss", "friction", "stepups"):
            assert lo - 0.01 <= row["reject"] <= hi + 0.01, row
    pl_ = study.replicate(b, "rule", reps, 2)
    for row in pl_.summary():
        if row["metric"] in ("loss", "stepups"):
            assert abs(row["bias"]) < 4 * row["bias_mc_se"], row
            assert row["coverage"] > 0.92, row
    # the step-up effect is large and certain; the loss effect is real but hard to see
    stepups = next(r for r in pl_.summary() if r["metric"] == "stepups" and r["estimator"] == "E1")
    assert stepups["reject"] > 0.99


def test_cuped_helps_a_metric_that_its_pre_period_predicts_and_not_one_it_does_not():
    b = _bench()
    s = {
        (r["estimator"], r["metric"]): r["mean_se"]
        for r in study.replicate(b, None, 60, 3).summary()
    }
    assert s[("E2", "stepups")] < s[("E1", "stepups")] * 0.97
    assert s[("E2", "friction")] <= s[("E1", "friction")] * 1.001


def test_srm_catches_both_kinds_of_loss_and_the_outcome_dependent_one_biases_friction():
    rows = {r["analysis_table"]: r for r in study.srm(_bench_big(), 30, 1)}
    assert rows["none"]["flagged"] == 0
    assert rows["random 5%"]["flagged"] == 1
    assert rows["failed-verification customers"]["flagged"] == 1
    bad = rows["failed-verification customers"]
    assert bad["friction_effect"] < 0 < bad["true_friction_effect"]


def _bench_big():
    """The SRM check needs the larger sample a 0.001 threshold is meant for."""
    b = _bench(seed=4, N=120_000)
    b.arms["device500"] = b.arms["rule"]
    return b


def test_peeking_inflates_and_the_sequential_procedures_do_not():
    b = _bench()
    run = study.sequential_runs(b, None, "friction", 1500, 5)
    rate = {r["procedure"]: r["any_reject"] for r in study.sequential_summary(run)}
    _, hi = study.band(1500, level=0.995)
    assert rate["naive"] > 0.12
    for k in ("obf", "pocock", "msprt"):
        assert rate[k] <= hi + 0.005, (k, rate)


def test_the_power_curve_rises_with_time_and_mde_shrinks():
    b = _bench()
    run = study.sequential_runs(b, "rule", "friction", 400, 6)
    power = study.fixed_horizon_power(run)
    assert power[-1] > power[0]
    # in absolute terms the smallest detectable effect grows with the cumulative mean; relative to
    # that mean (what a manager asks: "what percentage can you see?") it shrinks as time passes
    rel = study.mde(run["se"].mean(axis=0)) / b.arms["baseline"].weekly["friction"].mean(axis=0)
    assert rel[-1] < rel[0] * 0.6


def test_a_rollout_by_customer_age_is_biased_and_the_adjustments_work_only_with_overlap():
    b = _bench()
    # make the loss depend on tenure so that a rollout by age is confounded
    res = study.selection(b, "rule", "loss", 300, 7)
    truth = res["truth"]["effect"]
    assert abs(res["randomised"]["bias"]) < 3 * res["randomised"]["sd"] / np.sqrt(300) + 1e-9
    assert abs(res["tilted_naive"]["bias"]) > abs(res["tilted_stratified"]["bias"])
    assert abs(res["tilted_stratified"]["bias"]) < 0.5 * abs(truth) + 3 * res["tilted_stratified"][
        "sd"
    ] / np.sqrt(300)
    assert abs(res["deterministic"]["naive"] - truth) > abs(res["tilted_ipw"]["mean"] - truth)


def test_rate_trap_shows_a_steady_rate_with_more_customers_hurt():
    b = _bench()
    r = study.rate_trap(b, "rule")
    assert r["customers_hurt_rule"] > r["customers_hurt_baseline"]
    assert r["stepups_rule"] > r["stepups_baseline"]


def test_tau_schedule_grows_with_time_and_scales_with_the_pre_period_mean():
    tau = study.schedule_tau(10.0)
    assert (np.diff(tau) > 0).all() and abs(tau[-1] - 0.25 * 10.0 * 175 / 184) < 1e-9
    assert study.schedule_tau(0.0).sum() == 0
    _ = sequential  # the module is exercised in test_experiments_sequential
