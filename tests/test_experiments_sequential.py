"""Group-sequential boundaries and the always-valid test: their promise is a false-positive rate."""

import numpy as np

from fincrime.experiments import analysis, sequential


def _walks(runs, looks, seed=0):
    rng = np.random.default_rng(seed)
    return rng.standard_normal((runs, looks)).cumsum(axis=1) / np.sqrt(np.arange(1, looks + 1))


def test_one_look_is_the_ordinary_test():
    for kind in ("obf", "pocock"):
        assert abs(sequential.boundaries(kind, 1)[0] - 1.96) < 0.01


def test_first_obf_boundary_is_analytic():
    """At the first look nothing has been spent before, so c = 1.96 / sqrt(t) exactly."""
    c = sequential.boundaries("obf", 5)
    assert abs(c[0] - 1.959964 / np.sqrt(0.2)) < 0.01
    assert (np.diff(c) < 0).all()  # the boundary relaxes as information accrues
    assert 2.0 < c[-1] < 2.1


def test_pocock_is_nearly_flat_and_matches_the_published_five_look_constant():
    c = sequential.boundaries("pocock", 5)
    assert c.max() - c.min() < 0.07
    assert 2.38 < c.mean() < 2.45  # Pocock's classic constant is 2.413


def test_boundaries_spend_exactly_five_percent_on_a_random_walk():
    z = _walks(300_000, 10)
    for kind in ("obf", "pocock"):
        rate = (np.abs(z) > sequential.boundaries(kind, 10)).any(axis=1).mean()
        assert abs(rate - 0.05) < 0.003, (kind, rate)


def test_peeking_with_the_plain_test_inflates_the_error_rate():
    z = _walks(100_000, 25)
    naive = (np.abs(z) > 1.96).any(axis=1).mean()
    assert 0.2 < naive < 0.32


def test_msprt_keeps_its_level_under_continuous_monitoring_and_detects_an_effect():
    runs, looks = 100_000, 40
    rng = np.random.default_rng(2)
    null = rng.standard_normal((runs, looks)).cumsum(axis=1)
    k = np.arange(1, looks + 1)
    se = 1 / np.sqrt(k)
    eff0 = null / k  # the mean of the first k unit-variance draws
    stop0 = sequential.first_crossing(sequential.msprt_log_lambda(eff0, se, 0.5) > np.log(20))
    assert (stop0 >= 0).mean() < 0.052
    eff1 = (null + 0.8 * k) / k
    stop1 = sequential.first_crossing(sequential.msprt_log_lambda(eff1, se, 0.5) > np.log(20))
    assert (stop1 >= 0).mean() > 0.9


def test_look_table_matches_the_plain_estimator_at_every_look():
    rng = np.random.default_rng(1)
    n, looks = 500, 6
    c = rng.random((n, looks)).cumsum(axis=1)
    b = c + 0.3
    t = np.zeros(n, dtype=bool)
    t[rng.permutation(n)[:250]] = True
    eff, se = sequential.LookTable(c, b).statistics(t)
    for k in range(looks):
        y = np.where(t, b[:, k], c[:, k])
        ref = analysis.difference_in_means(y, t)
        assert abs(eff[k] - ref.effect) < 1e-9 and abs(se[k] - ref.se) < 1e-9


def test_decisions_reports_the_first_look_that_crosses():
    eff = np.array([[0.0, 3.0, 5.0], [0.0, 0.0, 0.0]])
    se = np.ones((2, 3))
    out = sequential.decisions(eff, se, {"flat": np.array([2.5, 2.5, 2.5])}, tau=1.0)
    assert out["naive"].tolist() == [1, -1]
    assert out["flat"].tolist() == [1, -1]
    assert out["msprt"].tolist() == [2, -1]  # needs more evidence than a 2.5 z at look one
