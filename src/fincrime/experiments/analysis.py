"""Estimators of a treatment effect from a randomised experiment, and the checks that come first.

Three estimators are fixed in advance (``docs/methodology.md`` section 9):

* **E1** difference in means with the Welch standard error;
* **E2** CUPED (Deng et al. 2013): subtract the part of the outcome that the pre-period value
  predicts, then take the difference in means. The slope is estimated on both arms pooled, which
  does not depend on who was treated, so the result stays unbiased;
* **E3** the interacted regression adjustment of Lin (2013): fit the outcome on the covariates
  separately in each arm, centred at the overall mean, and difference the intercepts.

All three are written for many outcomes and many random assignments at once: an assignment is a
0/1 vector, an outcome table has one column per metric, and the sums that matter are computed
without a Python loop over customers, because validating a test needs thousands of reruns.

The ``Result`` rows are what a real analyst would see: nothing here reads the other arm's
counterfactual outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

Z975 = float(stats.norm.ppf(0.975))


@dataclass(frozen=True)
class Result:
    """One estimate of the effect of the treatment, with its normal 95% interval."""

    effect: float
    se: float
    lo: float
    hi: float
    p: float
    n_treated: int
    n_control: int

    def covers(self, truth: float) -> bool:
        return self.lo <= truth <= self.hi


def _result(effect: float, se: float, n1: int, n0: int) -> Result:
    p = float(2 * stats.norm.sf(abs(effect) / se)) if se > 0 else float(effect != 0)
    return Result(effect, se, effect - Z975 * se, effect + Z975 * se, p, n1, n0)


def difference_in_means(y: np.ndarray, t: np.ndarray) -> Result:
    """E1: ``mean(y | treated) - mean(y | control)`` with the unequal-variance standard error."""
    t = t.astype(bool)
    a, b = y[t], y[~t]
    se = float(np.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size))
    return _result(float(a.mean() - b.mean()), se, a.size, b.size)


def cuped_adjust(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """``y - theta * (x - mean x)`` with ``theta = cov(x, y) / var(x)`` over the whole sample.

    Pooling both arms makes ``theta`` independent of the assignment, so the adjusted outcome has the
    same expectation as ``y`` in each arm and a smaller variance, by the factor
    ``1 - corr(x, y)^2``.
    """
    v = float(x.var())
    if v == 0.0:
        return y.copy()
    theta = float(np.cov(x, y, ddof=0)[0, 1] / v)
    return y - theta * (x - x.mean())


def cuped(y: np.ndarray, x: np.ndarray, t: np.ndarray) -> Result:
    """E2: CUPED difference in means."""
    return difference_in_means(cuped_adjust(y, x), t)


def lin(y: np.ndarray, x: np.ndarray, t: np.ndarray) -> Result:
    """E3: regression adjustment, separate slopes per arm, covariates centred at the overall mean.

    The intercept of each arm is that arm's mean outcome at the average customer; their difference
    is the effect. Its variance is the residual variance of each arm over its size, which in a
    randomised experiment needs no robust correction (the covariates are not chosen by the arm).
    """
    x = x.reshape(len(y), -1)
    xc = x - x.mean(axis=0)
    t = t.astype(bool)
    est, var = [], 0.0
    for arm in (t, ~t):
        z = np.column_stack([np.ones(arm.sum()), xc[arm]])
        beta, *_ = np.linalg.lstsq(z, y[arm], rcond=None)
        resid = y[arm] - z @ beta
        est.append(float(beta[0]))
        var += float(resid @ resid) / (arm.sum() - z.shape[1]) / arm.sum()
    return _result(est[0] - est[1], float(np.sqrt(var)), int(t.sum()), int((~t).sum()))


def lin_many(y: np.ndarray, x: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """E3 for every column of ``y`` at once, from sums, for the thousands of reruns of a validation.

    Returns ``(effect, se)``, each one number per metric. Equal to :func:`lin` column by column
    (the tests check it) but a hundred times faster, because it never forms the residuals: the
    residual sum of squares of an arm is ``y'y - b'Z'y`` once ``b`` solves the normal equations.
    """
    x = x.reshape(len(y), -1)
    xc = x - x.mean(axis=0)
    z = np.column_stack([np.ones(len(y)), xc])
    t = t.astype(bool)
    eff, var = [], 0.0
    for sign, arm in ((1.0, t), (-1.0, ~t)):
        za, ya = z[arm], y[arm]
        beta = np.linalg.solve(za.T @ za, za.T @ ya)
        rss = (ya * ya).sum(axis=0) - (beta * (za.T @ ya)).sum(axis=0)
        n = arm.sum()
        eff.append(sign * beta[0])
        var = var + rss / (n - z.shape[1]) / n
    return eff[0] + eff[1], np.sqrt(var)


def srm_p_value(n_treated: int, n_control: int, ratio: float = 0.5) -> float:
    """Sample-ratio-mismatch test: do the arm sizes match the planned split?

    A mismatch (convention: p below 0.001) means units were lost or added after assignment, and
    every later number is suspect, so it is checked before the outcome is looked at.
    """
    n = n_treated + n_control
    return float(stats.binomtest(n_treated, n, ratio).pvalue)


def balance(x: np.ndarray, t: np.ndarray, names: list[str]) -> list[dict]:
    """Standardised mean difference of each pre-treatment covariate; about 0.01 in a clean split."""
    x = x.reshape(len(t), -1)
    t = t.astype(bool)
    out = []
    for j, name in enumerate(names):
        a, b = x[t, j], x[~t, j]
        sd = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
        out.append({"covariate": name, "smd": float((a.mean() - b.mean()) / sd) if sd else 0.0})
    return out


def permutation_p(y: np.ndarray, t: np.ndarray, reps: int, rng: np.random.Generator) -> float:
    """Randomisation test of the sharp null: how often does a re-shuffle beat the observed gap?

    It uses only the fact that assignment was random, not a normal approximation to a mean that a
    few giant claims dominate. A re-shuffle only has to say which of the *non-zero* outcomes land
    in the treated arm (the rest add nothing to either sum), and that number is hypergeometric, so
    the test costs a few hundred draws per shuffle instead of a permutation of 190,000 customers.
    """
    t = t.astype(bool)
    n, n1 = len(y), int(t.sum())
    observed = abs(y[t].mean() - y[~t].mean())
    ynz = y[y != 0]
    m, total = len(ynz), float(ynz.sum())
    k_treated = rng.hypergeometric(n1, n - n1, m, size=reps)
    # which k of the m non-zero outcomes: the k smallest of m uniform ranks
    ranks = np.argsort(rng.random((reps, m)), axis=1).argsort(axis=1)
    pick = ranks < k_treated[:, None]
    s1 = (pick * ynz).sum(axis=1)
    gap = np.abs(s1 / n1 - (total - s1) / (n - n1))
    return float(((gap >= observed - 1e-12).sum() + 1) / (reps + 1))
