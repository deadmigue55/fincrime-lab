"""The delay model and the backtest bookkeeping, on worlds where the answer is built in."""

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from fincrime.risk import backtest, delay


def _world(n=6000, median=20.0, sigma=0.8, seed=0, days=420):
    """Claims lost uniformly over ``days`` from 2024-01-01 with lognormal delays; all ever reported."""
    rng = np.random.default_rng(seed)
    start = datetime(2024, 1, 1)
    loss = [start + timedelta(days=float(u)) for u in rng.random(n) * days]
    lag = np.exp(np.log(median) + sigma * rng.standard_normal(n))
    return pl.DataFrame(
        {
            "episode_id": np.arange(n),
            "fraud_type": ["card_testing"] * n,
            "loss_ts": loss,
            "amount": np.exp(rng.normal(5, 1, n)),
            "reported_ts": [t + timedelta(days=float(d)) for t, d in zip(loss, lag, strict=True)],
        },
        schema_overrides={"loss_ts": pl.Datetime("us"), "reported_ts": pl.Datetime("us")},
    )


def test_truncated_likelihood_recovers_the_planted_delay_and_the_naive_fit_does_not():
    claims = _world()
    as_of = date(2024, 12, 31)
    fit = delay.fit_by_type(claims, as_of)["card_testing"]
    assert fit.median_days == pytest.approx(20.0, rel=0.06)
    assert fit.sigma == pytest.approx(0.8, abs=0.05)
    # a young book (loss window of 100 days): truncation is heavy and the naive fit is clearly low
    young = _world(n=3000, seed=1, days=100)
    seen = delay.visible(young, date(2024, 4, 10))
    d = (seen["reported_ts"] - seen["loss_ts"]).dt.total_seconds().to_numpy() / 86400
    fit = delay.fit_by_type(young, date(2024, 4, 10))["card_testing"]
    assert np.exp(np.log(d).mean()) < 0.85 * 20.0
    assert fit.median_days == pytest.approx(20.0, rel=0.2)


def test_completion_is_one_for_old_cohorts_and_small_for_the_current_month():
    old = delay.cohort_completion(np.log(20.0), 0.8, date(2024, 12, 31), date(2024, 1, 1))
    now = delay.cohort_completion(np.log(20.0), 0.8, date(2024, 12, 31), date(2024, 12, 1))
    assert old > 0.99 and 0.2 < now < 0.5


def test_completed_counts_recover_the_ultimate_within_sampling_noise():
    claims = _world(n=9000, seed=2, days=330)
    as_of = date(2024, 11, 30)
    mat, _ = delay.maturity(claims, as_of)
    truth = claims.filter(pl.col("loss_ts") <= datetime(2024, 11, 30, 23, 59, 59)).height
    assert mat.ultimate_n.sum() == pytest.approx(truth, rel=0.05)
    assert mat.reported_n.sum() < 0.95 * truth  # a visible share is still missing


def test_predictive_draws_are_centred_on_the_point_estimate():
    claims = _world(n=3000, seed=4, days=330)
    pred = delay.predictive(claims, date(2024, 11, 30), reps=200, seed=1)
    point = pred["maturity"].ibnr_n.sum()
    assert pred["counts"].sum(axis=(1, 2)).mean() == pytest.approx(point, rel=0.15)
    assert pred["counts"].sum(axis=(1, 2)).std() > 0


def test_removing_the_future_does_not_change_the_delay_fit():
    """Leak guard: the fit at an as-of date uses reports up to that date and nothing later."""
    claims = _world(n=2500, seed=5)
    as_of = date(2024, 9, 30)
    cut = claims.filter(pl.col("reported_ts") <= datetime(2024, 9, 30, 23, 59, 59))
    a = delay.fit_by_type(claims, as_of)["card_testing"]
    b = delay.fit_by_type(cut, as_of)["card_testing"]
    assert (a.mu, a.sigma) == (b.mu, b.sigma)


def test_true_ibnr_counts_what_is_reported_only_after_the_as_of_date():
    d = datetime
    claims = pl.DataFrame(
        [
            (1, "ato", d(2024, 1, 10), 100.0, d(2024, 1, 20)),
            (2, "ato", d(2024, 1, 25), 50.0, d(2024, 2, 3)),  # late: reported after 31 January
            (
                3,
                "card_testing",
                d(2024, 2, 5),
                10.0,
                d(2024, 4, 9),
            ),  # late for both 29 Feb and 31 Mar
            (
                4,
                "ato",
                d(2024, 2, 8),
                7.0,
                None,
            ),  # never reported: not IBNR, it is not a future report
        ],
        schema={
            "episode_id": pl.Int64,
            "fraud_type": pl.String,
            "loss_ts": pl.Datetime("us"),
            "amount": pl.Float64,
            "reported_ts": pl.Datetime("us"),
        },
        orient="row",
    )
    jan = backtest.true_ibnr(claims, date(2024, 1, 31))
    assert jan["n"].tolist() == [1.0] and jan["amt"].tolist() == [50.0]
    mar = backtest.true_ibnr(claims, date(2024, 3, 31))
    assert mar["n"].tolist() == [0.0, 1.0, 0.0] and mar["ult_n"].tolist() == [2.0, 1.0, 0.0]


def test_the_newest_cohort_interval_carries_the_estimation_error_of_its_size():
    """With a completion of about a third, the unreported count is far more spread than a Poisson."""
    claims = _world(n=3000, seed=6, days=330)
    pred = delay.predictive(claims, date(2024, 11, 30), reps=800, seed=2)
    last = pred["counts"][:, -1, 0]
    c = pred["maturity"].completion[-1, 0]
    assert c < 0.5
    assert (
        last.var() > 1.8 * last.mean()
    )  # Poisson would give a ratio of 1; the gamma mixing gives about 1 / c
