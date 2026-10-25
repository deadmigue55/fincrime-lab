"""Frequency, severity and the FFT aggregate, each against a case with a known answer."""

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest
from scipy import stats

from fincrime.risk import lossmodel as lm
from fincrime.risk import pipeline


def test_discretisation_preserves_the_capped_mean_and_sums_to_one():
    for sev in (
        lm.Severity(6.0, 1.2),
        lm.Severity(6.0, 1.0, u=2000.0, p_body=0.9, xi=0.2, beta=1500.0),
    ):
        pmf = lm.discretise(sev)
        assert pmf.sum() == pytest.approx(1.0, abs=1e-9)
        x = np.arange(len(pmf)) * lm.GRID
        # exact capped mean: integral of the survival function on a very fine grid
        fine = (np.arange(2_000_000) + 0.5) * (lm.CAP / 2_000_000)
        exact = sev.survival(fine).sum() * (lm.CAP / 2_000_000)
        assert (x * pmf).sum() == pytest.approx(exact, rel=2e-4)


def test_fft_aggregate_matches_monte_carlo_in_mean_spread_and_tail():
    sev = {"a": lm.Severity(8.0, 0.9), "b": lm.Severity(5.5, 1.4)}
    means, alphas = {"a": 40.0, "b": 120.0}, {"a": 0.0, "b": 0.08}
    agg = lm.aggregate({k: lm.discretise(s) for k, s in sev.items()}, means, alphas, cells=2**17)
    mc = lm.simulate_mc(sev, means, alphas, years=200_000, seed=1)
    assert agg.mean == pytest.approx(mc.mean(), rel=0.01)
    assert agg.std == pytest.approx(mc.std(), rel=0.03)
    assert agg.var(0.995) == pytest.approx(np.quantile(mc, 0.995), rel=0.03)
    assert agg.tvar(0.99) == pytest.approx(mc[mc >= np.quantile(mc, 0.99)].mean(), rel=0.03)


def test_poisson_compound_of_a_constant_claim_has_the_exact_quantiles():
    """Every claim loses exactly 100 pesos, so the aggregate is 100 x Poisson and VaR is known."""
    pmf = np.zeros(int(lm.CAP / lm.GRID) + 1)
    pmf[2] = 1.0  # 100 pesos = two grid cells
    agg = lm.aggregate({"x": pmf}, {"x": 30.0}, {"x": 0.0}, cells=2**15)
    assert agg.mean == pytest.approx(3000.0, rel=1e-9)
    assert agg.var(0.995) == pytest.approx(100.0 * stats.poisson.ppf(0.995, 30.0), abs=lm.GRID)
    # stop-loss at 3000 pesos: 100 x E[(N - 30)+]
    n = np.arange(0, 200)
    expected = (stats.poisson.pmf(n, 30.0) * np.clip(n - 30, 0, None)).sum() * 100.0
    assert agg.stop_loss(3000.0) == pytest.approx(expected, rel=1e-6)


def test_negative_binomial_fit_recovers_rate_and_dispersion():
    rng = np.random.default_rng(0)
    exposure = np.linspace(2e4, 2e5, 60)
    rate, alpha = 8e-4, 0.15
    mu = rate * exposure
    counts = rng.poisson(rng.gamma(1 / alpha, alpha * mu))
    fit = lm.fit_nb(counts, exposure)
    assert fit.rate == pytest.approx(rate, rel=0.08)
    assert fit.alpha == pytest.approx(alpha, abs=0.07)
    pois = lm.fit_nb(rng.poisson(mu), exposure)
    assert pois.alpha < 0.05


def test_trend_test_rejects_a_trend_and_accepts_none():
    rng = np.random.default_rng(1)
    e = np.full(40, 1e5)
    flat = rng.poisson(30.0, 40)
    rising = rng.poisson(30.0 * np.exp(0.05 * (np.arange(40) - 20)))
    assert lm.trend_test(flat, e)[1] > 0.01
    assert lm.trend_test(rising, e)[1] < 0.01


def _mixture(seed: int, n_body: int = 3600, n_tail: int = 2000) -> np.ndarray:
    """Lognormal body below 3000 pesos and a planted generalised Pareto tail (shape 0.35) above it."""
    rng = np.random.default_rng(seed)
    body = np.exp(rng.normal(6.0, 0.8, n_body * 2))
    body = body[body < 3000.0][:n_body]
    return np.concatenate(
        [body, 3000.0 + stats.genpareto.rvs(0.35, scale=2500.0, size=n_tail, random_state=seed)]
    )


def test_severity_fits_recover_a_planted_pareto_tail_and_the_choice_follows_the_data():
    heavy, new = _mixture(3), _mixture(4)
    sp = lm.fit_severity(heavy, "splice")
    assert sp.xi == pytest.approx(0.35, abs=0.15)
    ln = lm.fit_severity(heavy, "lognormal")
    assert lm.heldout_loglik(sp, new) > lm.heldout_loglik(ln, new)
    # data that really is lognormal: the plain model is at least as good out of sample
    rng = np.random.default_rng(5)
    plain, held = np.exp(rng.normal(6.0, 1.0, 3000)), np.exp(rng.normal(6.0, 1.0, 3000))
    assert (
        lm.heldout_loglik(lm.fit_severity(plain, "lognormal"), held)
        >= lm.heldout_loglik(lm.fit_severity(plain, "splice"), held) - 0.02
    )


def test_sampling_follows_the_survival_function_and_respects_the_cap():
    sev = lm.Severity(7.0, 1.5)
    x = sev.sample(200_000, np.random.default_rng(2))
    assert x.max() <= lm.CAP
    for q in (500.0, 2000.0, 20000.0):
        assert (x > q).mean() == pytest.approx(float(sev.survival(np.array([q]))[0]), abs=0.004)


def test_gross_up_and_exposure_scenarios_by_hand():
    up = lm.gross_up_means({"ato": 92.0, "card_testing": 80.0})
    assert up["ato"] == pytest.approx(100.0) and up["card_testing"] == pytest.approx(100.0)
    assert lm.gross_up_means({"ato": 92.0, "card_testing": 80.0}, factor=0.0)[
        "ato"
    ] == pytest.approx(92.0)
    ex = pl.DataFrame(
        {
            "month": [date(2025, m, 1) for m in range(1, 7)],
            "accounts": [100.0] * 6,
            "opened": [10] * 6,
        }
    )
    base = lm.future_exposure(ex, "base", months=3)
    assert base.tolist() == [110.0, 120.0, 130.0]
    assert lm.future_exposure(ex, "flat", months=2).tolist() == [105.0, 105.0]
    assert lm.future_exposure(ex, "high", months=1).tolist() == [105.0 + 7.5]


def test_the_pipeline_runs_end_to_end_and_its_interval_covers_a_known_expected_loss():
    """A world with a planted rate and size: the point and the bootstrap interval bracket the truth."""
    rng = np.random.default_rng(7)
    months = [date(2024, m, 1) for m in range(1, 13)]
    acc = np.linspace(40_000, 90_000, 12)
    rows, eid = [], 0
    for m, a in zip(months, acc, strict=True):
        for kind, rate, med in (("ato", 6e-4, 2.0), ("card_testing", 9e-4, 15.0)):
            for _ in range(rng.poisson(rate * a)):
                t = datetime(m.year, m.month, 1) + timedelta(days=float(rng.random() * 27))
                lag = med * float(np.exp(0.7 * rng.standard_normal()))
                rows.append(
                    (eid, kind, t, float(np.exp(rng.normal(6.5, 1.0))), t + timedelta(days=lag))
                )
                eid += 1
    claims = pl.DataFrame(
        rows,
        schema={
            "episode_id": pl.Int64,
            "fraud_type": pl.String,
            "loss_ts": pl.Datetime("us"),
            "amount": pl.Float64,
            "reported_ts": pl.Datetime("us"),
        },
        orient="row",
    )
    ex = pl.DataFrame({"month": months, "accounts": acc, "opened": [7000] * 12})
    future = np.full(6, 100_000.0)
    out = pipeline.forecast(claims, ex, date(2024, 12, 31), future, reps=40, seed=3, cells=2**16)
    truth = (6e-4 + 9e-4) * 100_000 * 6 * np.exp(6.5 + 0.5)
    lo, hi = pipeline.intervals(out["reps"])["expected_loss"]
    assert lo < truth < hi
    assert out["point"]["expected_loss"] == pytest.approx(truth, rel=0.15)
    assert out["point"]["capital"] > 0
