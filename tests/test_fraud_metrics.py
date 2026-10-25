"""The fraud metrics: hand-checked toys, and the cluster bootstrap validated on planted structure."""

import numpy as np
import pytest

from fincrime.fraud import metrics


def test_recall_at_share_on_a_toy():
    y = np.array([0, 1, 0, 1, 0, 0, 0, 0, 0, 0])
    score = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0])
    r = metrics.recall_at_share(y, score, shares=(0.1, 0.2, 0.5))
    assert r == {0.1: 0.5, 0.2: 1.0, 0.5: 1.0}  # top 1 event holds one of two frauds, top 2 both


def test_recall_at_fpr_on_a_toy():
    y = np.array([1, 0, 1, 0, 0, 1, 0, 0, 0, 0])
    score = np.array([9, 8, 7, 6, 5, 4, 3, 2, 1, 0], dtype=float)  # 7 genuine events
    r = metrics.recall_at_fpr(y, score, fprs=(0.0, 1 / 7, 3 / 7, 1.0))
    # no genuine flagged -> first fraud; one genuine -> two frauds; three genuine -> all three
    assert r[0.0] == pytest.approx(1 / 3)
    assert r[1 / 7] == pytest.approx(2 / 3)
    assert r[3 / 7] == pytest.approx(1.0)
    assert r[1.0] == pytest.approx(1.0)


def test_average_precision_and_auc_known_values():
    y = np.array([1, 0, 1])
    score = np.array([3.0, 2.0, 1.0])
    assert metrics.average_precision(y, score) == pytest.approx((1 + 2 / 3) / 2)
    assert metrics.roc_auc(y, score) == pytest.approx(0.5)
    assert metrics.roc_auc(y, np.array([3.0, 1.0, 2.0])) == pytest.approx(1.0)
    assert metrics.roc_auc(np.array([1, 0]), np.array([1.0, 1.0])) == pytest.approx(0.5)  # ties
    rng = np.random.default_rng(1)
    yy, ss = rng.random(50_000) < 0.01, rng.random(50_000)
    assert metrics.roc_auc(yy, ss) == pytest.approx(0.5, abs=0.04)


def test_calibration_table_of_a_calibrated_score():
    rng = np.random.default_rng(2)
    p = rng.random(200_000) * 0.2
    y = rng.random(200_000) < p
    for predicted, observed, n in metrics.calibration_table(y, p, bins=5):
        assert abs(predicted - observed) < 0.01
        assert n == 40_000


def test_bootstrap_matches_the_binomial_interval_and_widens_for_clusters():
    """Planted structure: 400 frauds, each caught with probability 0.7. With independent frauds
    the interval is the binomial one; when frauds come in all-or-nothing groups of 10 (a
    takeover with ten transactions) it must widen by about sqrt(10)."""
    rng = np.random.default_rng(7)
    n, per = 40_000, 10
    base = {"shares": (0.01,), "fprs": (0.05,), "reps": 400, "seed": 3}

    def run(group):
        y = np.zeros(n, dtype=bool)
        y[:400] = True
        caught = np.zeros(n, dtype=bool)
        if group == 1:
            caught[:400] = rng.random(400) < 0.7
        else:
            caught[:400] = np.repeat(rng.random(400 // per) < 0.7, per)
        score = rng.random(n)
        score[caught] += 10
        cluster = np.arange(n)
        cluster[:400] = np.repeat(np.arange(400 // group), group) + n  # frauds share a cluster
        ci = metrics.cluster_bootstrap(y, score, cluster, **base)
        lo, hi = ci[("share", 0.01)]
        return hi - lo, ci

    narrow, ci = run(1)
    assert narrow == pytest.approx(2 * 1.96 * np.sqrt(0.7 * 0.3 / 400), rel=0.25)
    assert ci["clusters"] == 400
    wide, ci = run(per)
    assert ci["clusters"] == 40
    assert 2.2 < wide / narrow < 4.4
