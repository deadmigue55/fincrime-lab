"""The validation studies of ``docs/methodology.md`` section 9, each a function of the bench.

Everything here is a Monte Carlo over *random assignments of the same customers*: the world is
fixed (200,000 customers, both rule sets' outcomes known) and only the coin flips of the experiment
are redrawn. So a rejection rate is "how often would this test have rejected, over the random
assignments an experimenter could have drawn", which is exactly what a significance level promises.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from fincrime.experiments import analysis, outcomes, sequential
from fincrime.experiments.outcomes import Bench

ESTIMATORS = ("E1", "E2", "E3")
ALPHA = 0.05


def assign(n: int, rng: np.random.Generator, share: float = 0.5) -> np.ndarray:
    """Completely randomised assignment: exactly ``round(n * share)`` customers treated."""
    t = np.zeros(n, dtype=bool)
    t[rng.permutation(n)[: round(n * share)]] = True
    return t


def band(reps: int, level: float = 0.99, p: float = ALPHA) -> tuple[float, float]:
    """The range of rejection rates a correct test lands in over ``reps`` runs, with ``level``."""
    lo, hi = stats.binom.ppf([(1 - level) / 2, 1 - (1 - level) / 2], reps, p)
    return float(lo / reps), float(hi / reps)


def _welch(y: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Difference in means and Welch standard error of every column of ``y``."""
    tf = t.astype(float)
    u = 1.0 - tf
    n1, n0 = tf.sum(), u.sum()
    sq = y * y
    m1, m0 = (tf @ y) / n1, (u @ y) / n0
    v1 = ((tf @ sq) - n1 * m1**2) / (n1 - 1)
    v0 = ((u @ sq) - n0 * m0**2) / (n0 - 1)
    return m1 - m0, np.sqrt(v1 / n1 + v0 / n0)


def _cuped(y: np.ndarray, pre: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """E2 for every metric, each adjusted by its own pre-period value."""
    xc = pre - pre.mean(axis=0)
    var = (xc * xc).mean(axis=0)
    theta = np.where(
        var > 0, (xc * (y - y.mean(axis=0))).mean(axis=0) / np.where(var > 0, var, 1), 0
    )
    return _welch(y - theta * xc, t)


def estimates(
    y: np.ndarray, pre: np.ndarray, x: np.ndarray, t: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """E1, E2, E3 for every metric: arrays of shape (3, metrics) for the effect and its se."""
    e1, s1 = _welch(y, t)
    e2, s2 = _cuped(y, pre, t)
    e3, s3 = analysis.lin_many(y, x, t)
    return np.stack([e1, e2, e3]), np.stack([s1, s2, s3])


@dataclass
class Replications:
    """Effect and standard error of every estimator and metric over many random assignments."""

    effect: np.ndarray  # (reps, 3, metrics)
    se: np.ndarray
    truth: np.ndarray  # (metrics,) the true average effect (zero for an A/A)

    @property
    def p(self) -> np.ndarray:
        return 2 * stats.norm.sf(np.abs(self.effect) / np.where(self.se > 0, self.se, np.inf))

    def summary(self) -> list[dict]:
        """Per estimator and metric: rejection rate, bias, interval coverage and spread."""
        eff, se, p = self.effect, self.se, self.p
        reps = eff.shape[0]
        rows = []
        for e, name in enumerate(ESTIMATORS):
            for m, metric in enumerate(outcomes.METRICS):
                err = eff[:, e, m] - self.truth[m]
                half = analysis.Z975 * se[:, e, m]
                rows.append(
                    {
                        "estimator": name,
                        "metric": metric,
                        "reject": float((p[:, e, m] < ALPHA).mean()),
                        "bias": float(err.mean()),
                        "bias_mc_se": float(err.std(ddof=1) / math.sqrt(reps)),
                        "sd": float(eff[:, e, m].std(ddof=1)),
                        "mean_se": float(se[:, e, m].mean()),
                        "coverage": float((np.abs(err) <= half).mean()),
                        "truth": float(self.truth[m]),
                        "reps": reps,
                    }
                )
        return rows


def replicate(bench: Bench, treatment: str | None, reps: int, seed: int) -> Replications:
    """Randomise ``reps`` times and estimate every metric. ``treatment=None`` is an A/A test.

    In an A/A test every customer shows the baseline outcome whatever the arm, so the truth is zero
    and any rejection is a false positive.
    """
    rng = np.random.default_rng(seed)
    ya = bench.arms["baseline"].y
    yb = bench.arms[treatment].y if treatment else ya
    d = yb - ya
    truth = d.mean(axis=0)
    eff = np.empty((reps, 3, ya.shape[1]))
    se = np.empty_like(eff)
    for r in range(reps):
        t = assign(bench.n, rng)
        y = ya + d * t[:, None]
        eff[r], se[r] = estimates(y, bench.pre, bench.x, t)
    return Replications(eff, se, truth)


def permutation_a_a(
    bench: Bench, metrics: list[int], reps: int, shuffles: int, seed: int
) -> dict[str, float]:
    """A/A rejection rate of the permutation test next to that of the Welch test, same draws."""
    rng = np.random.default_rng(seed)
    ya = bench.arms["baseline"].y
    perm_hits = np.zeros(len(metrics))
    welch_hits = np.zeros(len(metrics))
    for _ in range(reps):
        t = assign(bench.n, rng)
        for j, m in enumerate(metrics):
            perm_hits[j] += analysis.permutation_p(ya[:, m], t, shuffles, rng) < ALPHA
            welch_hits[j] += analysis.difference_in_means(ya[:, m], t).p < ALPHA
    return {
        outcomes.METRICS[m]: {
            "permutation": float(perm_hits[j] / reps),
            "welch": float(welch_hits[j] / reps),
        }
        for j, m in enumerate(metrics)
    }


def srm(bench: Bench, reps: int, seed: int) -> list[dict]:
    """Sample-ratio-mismatch checks, with two kinds of broken analysis table.

    *Random loss*: a share of the treated arm's customers silently missing from the table. It biases
    nothing, but the arm sizes betray it. *Outcome-dependent loss*: treated customers with a failed
    verification missing, as if the logging of an error also dropped the customer. That biases the
    friction estimate and the arm sizes betray that too, which is why the check is run first.
    """
    rng = np.random.default_rng(seed)
    y = bench.arms["device500"].y
    ya = bench.arms["baseline"].y
    rows = []
    for name, drop in (
        ("none", 0.0),
        ("random 0.2%", 0.002),
        ("random 1%", 0.01),
        ("random 5%", 0.05),
        ("failed-verification customers", None),
    ):
        flagged, g1 = 0, []
        for _ in range(reps):
            t = assign(bench.n, rng)
            obs = ya + (y - ya) * t[:, None]
            keep = np.ones(bench.n, dtype=bool)
            if drop is None:
                keep &= ~(t & (obs[:, 4] > 0))
            elif drop > 0:
                keep &= ~(t & (rng.random(bench.n) < drop))
            n1, n0 = int((t & keep).sum()), int((~t & keep).sum())
            flagged += analysis.srm_p_value(n1, n0) < 0.001
            g1.append(analysis.difference_in_means(obs[keep, 4], t[keep]).effect)
        rows.append(
            {
                "analysis_table": name,
                "flagged": flagged / reps,
                "friction_effect": float(np.mean(g1)),
                "true_friction_effect": float((y - ya)[:, 4].mean()),
            }
        )
    return rows


# --- sequential ---------------------------------------------------------------------------------


def schedule_tau(
    pre_mean: float, relative: float = 0.25, weeks: int = outcomes.WEEKS
) -> np.ndarray:
    """Mixing sd of the mSPRT at each look: a share of the pre-period mean, grown with elapsed time.

    It is fixed before the experiment (the pre-period is history), as the validity of the test needs.
    """
    return relative * pre_mean * 7.0 * np.arange(1, weeks + 1) / 184.0


def sequential_runs(
    bench: Bench, treatment: str | None, metric: str, reps: int, seed: int
) -> dict[str, np.ndarray]:
    """Weekly looks: effect and se of every run, and the first rejecting week of every procedure."""
    rng = np.random.default_rng(seed)
    key = {"loss": "loss", "friction": "friction"}[metric]
    col = outcomes.METRICS.index(metric)
    a = bench.arms["baseline"].weekly[key]
    table = sequential.LookTable(a, bench.arms[treatment].weekly[key] if treatment else None)
    eff = np.empty((reps, outcomes.WEEKS))
    se = np.empty_like(eff)
    for r in range(reps):
        eff[r], se[r] = table.statistics(assign(bench.n, rng))
    bounds = {k: sequential.boundaries(k, outcomes.WEEKS) for k in ("obf", "pocock")}
    tau = schedule_tau(float(bench.pre[:, col].mean()))
    dec = sequential.decisions(eff, se, bounds, tau)
    truth = (
        (bench.arms[treatment].weekly[key] - a).mean(axis=0)
        if treatment
        else np.zeros(outcomes.WEEKS)
    )
    return {"effect": eff, "se": se, "first": dec, "truth": truth, "bounds": bounds, "tau": tau}


def sequential_summary(run: dict[str, np.ndarray]) -> list[dict]:
    """Rejection rate at the end and at any look, median stopping week and wrong-sign stops."""
    eff, truth = run["effect"], run["truth"]
    rows = []
    for name, first in run["first"].items():
        stopped = first >= 0
        idx = np.where(stopped, first, 0)
        sign_ok = (
            np.sign(eff[np.arange(len(first)), idx]) == np.sign(truth[idx]) if truth.any() else None
        )
        rows.append(
            {
                "procedure": name,
                "any_reject": float(stopped.mean()),
                "median_week": float(np.median(first[stopped]) + 1) if stopped.any() else None,
                "wrong_sign": float((stopped & ~sign_ok).sum() / max(stopped.sum(), 1))
                if sign_ok is not None
                else None,
            }
        )
    return rows


def fixed_horizon_power(run: dict[str, np.ndarray]) -> np.ndarray:
    """Share of runs a plain 5% test would reject if read at the end of each week only."""
    z = np.abs(run["effect"]) / run["se"]
    return (z > stats.norm.ppf(1 - ALPHA / 2)).mean(axis=0)


def mde(se: np.ndarray, power: float = 0.8) -> np.ndarray:
    """Smallest true effect a 5% test detects with ``power``: ``(z_(1-a/2) + z_power) * se``."""
    return (stats.norm.ppf(1 - ALPHA / 2) + stats.norm.ppf(power)) * se


# --- delayed labels -----------------------------------------------------------------------------


def delayed_readout(
    bench: Bench, treatment: str, weights: np.ndarray, reps: int, seed: int
) -> dict[str, np.ndarray]:
    """The weekly effect on fraud loss read three ways, averaged over random assignments.

    ``true`` counts every loss when it happens (not knowable by the bank); ``reported`` counts a loss
    only once its report has arrived; ``weighted`` divides each reported loss by the chance it would
    have been reported by now. ``weights`` is the (n, weeks) cumulative weighted-loss table of the
    treated arm followed by that of the control arm, stacked along a new first axis.
    """
    rng = np.random.default_rng(seed)
    a, b = bench.arms["baseline"], bench.arms[treatment]
    tables = {
        "true": (a.weekly["loss"], b.weekly["loss"]),
        "reported": (a.weekly["loss_reported"], b.weekly["loss_reported"]),
        "weighted": (weights[0], weights[1]),
    }
    luts = {k: sequential.LookTable(*v) for k, v in tables.items()}
    out = {k: np.empty((reps, outcomes.WEEKS)) for k in tables}
    for r in range(reps):
        t = assign(bench.n, rng)
        for k, lut in luts.items():
            out[k][r] = lut.statistics(t)[0]
    out["truth"] = (b.weekly["loss"] - a.weekly["loss"]).mean(axis=0)
    return out


# --- selection ----------------------------------------------------------------------------------


def selection(bench: Bench, treatment: str, metric: str, reps: int, seed: int) -> dict[str, dict]:
    """A rule rolled out by customer age instead of by a coin flip, and what fixes it.

    ``deterministic``: the newest third of customers get the rule, the rest do not. No older customer
    has a treated twin, so no adjustment can fix the comparison; the naive gap and a tenure-adjusted
    regression both land away from the truth. ``tilted``: the same split, but new customers are
    treated with probability 0.8, middle 0.5, old 0.2. Now a stratified estimator and an
    inverse-probability one recover the truth, a plain gap still does not.
    """
    rng = np.random.default_rng(seed)
    m = outcomes.METRICS.index(metric)
    ya, yb = bench.arms["baseline"].y[:, m], bench.arms[treatment].y[:, m]
    truth = float((yb - ya).mean())
    order = np.argsort(np.argsort(bench.tenure))  # 0 = newest
    tercile = np.minimum(order * 3 // bench.n, 2)  # 0 newest, 1, 2 oldest
    prop = np.array([0.8, 0.5, 0.2])[tercile]

    def naive(t: np.ndarray, y: np.ndarray) -> float:
        return float(y[t].mean() - y[~t].mean())

    def stratified(t: np.ndarray, y: np.ndarray) -> float:
        total = 0.0
        for g in range(3):
            s = tercile == g
            total += s.mean() * (y[s & t].mean() - y[s & ~t].mean())
        return float(total)

    def ipw(t: np.ndarray, y: np.ndarray) -> float:
        w1, w0 = t / prop, (~t) / (1 - prop)
        return float((w1 * y).sum() / w1.sum() - (w0 * y).sum() / w0.sum())

    def regression(t: np.ndarray, y: np.ndarray) -> float:
        z = np.column_stack([np.ones(len(y)), t, bench.tenure - bench.tenure.mean()])
        return float(np.linalg.lstsq(z, y, rcond=None)[0][1])

    det = tercile == 0
    y_det = np.where(det, yb, ya)
    res: dict[str, dict] = {
        "truth": {"effect": truth},
        "deterministic": {
            "naive": naive(det, y_det),
            "tenure_regression": regression(det, y_det),
        },
    }
    ests = {k: [] for k in ("randomised", "tilted_naive", "tilted_stratified", "tilted_ipw")}
    for _ in range(reps):
        t = assign(bench.n, rng)
        ests["randomised"].append(naive(t, np.where(t, yb, ya)))
        t = rng.random(bench.n) < prop
        y = np.where(t, yb, ya)
        ests["tilted_naive"].append(naive(t, y))
        ests["tilted_stratified"].append(stratified(t, y))
        ests["tilted_ipw"].append(ipw(t, y))
    for k, v in ests.items():
        v = np.asarray(v)
        res[k] = {
            "mean": float(v.mean()),
            "sd": float(v.std(ddof=1)),
            "bias": float(v.mean() - truth),
        }
    return res


def rate_trap(bench: Bench, treatment: str) -> dict[str, float]:
    """The failure rate per step-up against the number of customers hurt, baseline against rule."""
    a, b = bench.arms["baseline"].y, bench.arms[treatment].y
    stepups = lambda y: float(y[:, 5].sum())  # noqa: E731
    failed = lambda y: float(y[:, 4].sum())  # noqa: E731
    return {
        "failed_per_stepup_baseline": failed(a) / stepups(a),
        "failed_per_stepup_rule": failed(b) / stepups(b),
        "customers_hurt_baseline": failed(a),
        "customers_hurt_rule": failed(b),
        "stepups_baseline": stepups(a),
        "stepups_rule": stepups(b),
    }
