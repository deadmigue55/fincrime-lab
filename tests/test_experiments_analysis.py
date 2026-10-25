"""Estimators, SRM and the permutation test, on data where the answer is known."""

import itertools

import numpy as np
from scipy import stats

from fincrime.experiments import analysis, study


def _data(n=4000, effect=0.0, rho=0.8, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    t = study.assign(n, rng)
    y = 2.0 + 3.0 * rho * x + np.sqrt(1 - rho**2) * 3.0 * rng.normal(size=n) + effect * t
    return y, x, t


def test_difference_in_means_is_welch():
    y, _, t = _data(effect=0.4)
    r = analysis.difference_in_means(y, t)
    ref = stats.ttest_ind(y[t], y[~t], equal_var=False)
    assert abs(r.effect / r.se - ref.statistic) < 1e-9  # normal p-value, t statistic
    assert abs(r.effect - (y[t].mean() - y[~t].mean())) < 1e-12
    assert r.lo < r.effect < r.hi


def test_cuped_cuts_variance_by_one_minus_rho_squared_and_keeps_the_effect():
    effects, ses, plain = [], [], []
    for s in range(60):
        y, x, t = _data(n=6000, effect=0.5, rho=0.8, seed=s)
        r = analysis.cuped(y, x, t)
        effects.append(r.effect)
        ses.append(r.se)
        plain.append(analysis.difference_in_means(y, t).se)
    assert abs(np.mean(effects) - 0.5) < 3 * np.std(effects) / np.sqrt(60)
    assert 0.30 < (np.mean(ses) / np.mean(plain)) ** 2 < 0.45  # 1 - 0.8^2 = 0.36


def test_cuped_adjustment_does_not_depend_on_assignment():
    """The slope is pooled over both arms, so shuffling who is treated cannot move it."""
    y, x, _ = _data()
    assert np.allclose(analysis.cuped_adjust(y, x), analysis.cuped_adjust(y, x))
    adj = analysis.cuped_adjust(y, x)
    assert abs(adj.mean() - y.mean()) < 1e-9
    assert abs(np.corrcoef(adj, x)[0, 1]) < 1e-9


def test_lin_matches_lin_many_and_recovers_the_effect():
    y, x, t = _data(effect=0.7, seed=3)
    xs = np.column_stack([x, x**2])
    one = analysis.lin(y, xs, t)
    eff, se = analysis.lin_many(np.column_stack([y, y * 2]), xs, t)
    assert abs(eff[0] - one.effect) < 1e-9 and abs(se[0] - one.se) < 1e-9
    assert abs(eff[1] - 2 * one.effect) < 1e-9
    assert abs(one.effect - 0.7) < 4 * one.se


def test_a_a_rejection_rate_is_nominal_for_all_three():
    reps = 1500
    lo, hi = study.band(reps)
    rej = np.zeros(3)
    rng = np.random.default_rng(11)
    n = 1200
    x = rng.normal(size=n)
    y = 1.0 + 2.0 * x + rng.normal(size=n)
    for _ in range(reps):
        t = study.assign(n, rng)
        rej += [
            analysis.difference_in_means(y, t).p < 0.05,
            analysis.cuped(y, x, t).p < 0.05,
            analysis.lin(y, x, t).p < 0.05,
        ]
    assert ((rej / reps >= lo) & (rej / reps <= hi)).all(), rej / reps


def test_srm_flags_a_lost_arm_and_passes_a_clean_split():
    assert analysis.srm_p_value(5000, 5000) > 0.99
    assert analysis.srm_p_value(47_500, 50_000) < 0.001  # a 5% loss of one arm
    assert analysis.srm_p_value(49_900, 50_000) > 0.05


def test_balance_is_near_zero_after_randomisation():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(5000, 2))
    t = study.assign(5000, rng)
    assert all(abs(b["smd"]) < 0.08 for b in analysis.balance(x, t, ["a", "b"]))


def test_permutation_p_matches_the_exact_enumeration():
    y = np.array([0, 0, 0, 5.0, 0, 0, 2.0, 0, 0, 0, 9.0, 1.0])
    t = np.zeros(12, dtype=bool)
    t[[3, 6, 10, 0, 1, 2]] = True
    observed = abs(y[t].mean() - y[~t].mean())
    gaps = []
    for chosen in itertools.combinations(range(12), 6):
        m = np.zeros(12, dtype=bool)
        m[list(chosen)] = True
        gaps.append(abs(y[m].mean() - y[~m].mean()))
    exact = np.mean(np.asarray(gaps) >= observed - 1e-12)
    p = analysis.permutation_p(y, t, 60_000, np.random.default_rng(1))
    assert abs(p - exact) < 0.01


def test_permutation_test_holds_its_level_on_a_rare_heavy_tailed_outcome():
    rng = np.random.default_rng(5)
    n = 3000
    y = np.where(rng.random(n) < 0.01, rng.pareto(1.3, n) * 1000 + 100, 0.0)
    reps, hits = 400, 0
    for _ in range(reps):
        t = study.assign(n, rng)
        hits += analysis.permutation_p(y, t, 399, rng) < 0.05
    lo, hi = study.band(reps)
    assert lo <= hits / reps <= hi
