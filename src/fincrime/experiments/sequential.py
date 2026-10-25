"""Looking at an experiment before it ends without lying about the error rate.

Fraud rules are tested for weeks and managers ask on Tuesday how it is going. A test built for one
look at the end rejects a true null about 5% of the time; the same test applied at 25 weekly looks
rejects far more often, because each look is another chance to cross the line. Two honest ways to
look:

* **Group-sequential boundaries** (Lan and DeMets 1983): a spending function says how much of the
  5% may be used by each look; the boundary at each look is the value that spends exactly that,
  found here by numerical integration of the Brownian-motion path of the test statistic.
  Pocock-type spending is nearly flat and stops early on a big effect; O'Brien-Fleming-type
  spending is very strict early and almost the plain 1.96 at the end.
* **A mixture sequential probability ratio test** (Robbins 1970; Johari et al. 2017): an
  "always-valid" statistic that can be read at any moment, with no schedule fixed in advance, at the
  price of some power.

All of them assume the estimate is approximately a random walk in information time, which holds when
a look adds customers' worth of outcomes and the standard error is estimated, not known. The
validation in ``docs/experiment-lab.md`` is a check of exactly that approximation.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

ALPHA = 0.05


def spending(kind: str, t: np.ndarray | float, alpha: float = ALPHA) -> np.ndarray | float:
    """Cumulative type-I error that may be spent by information fraction ``t``."""
    t = np.asarray(t, dtype=float)
    if kind == "obf":
        return 2.0 * (1.0 - stats.norm.cdf(stats.norm.ppf(1.0 - alpha / 2.0) / np.sqrt(t)))
    if kind == "pocock":
        return alpha * np.log(1.0 + (math.e - 1.0) * t)
    raise ValueError(f"unknown spending function {kind!r}")


def boundaries(kind: str, looks: int, alpha: float = ALPHA, step: float = 0.004) -> np.ndarray:
    """Two-sided critical z-values of ``looks`` equally spaced looks.

    The statistic ``S`` at information fraction ``t`` is a Brownian motion, ``S_k ~ N(0, t_k)``. The
    mass that has not yet crossed is carried on a fine grid; at each look the cutoff is the value
    outside of which sits exactly the error the spending function allots to that look.
    """
    t = np.arange(1, looks + 1) / looks
    edges = np.arange(-8.0, 8.0 + step / 2, step)
    mid = (edges[:-1] + edges[1:]) / 2
    dt = 1.0 / looks
    # first look: the walk starts at zero
    mass = np.diff(stats.norm.cdf(edges, scale=np.sqrt(t[0])))
    kernel = None
    keep = np.ones_like(mid)
    cuts = []
    for k in range(looks):
        if k > 0:
            if kernel is None:
                kernel = np.diff(
                    stats.norm.cdf((edges[None, :] - mid[:, None]) / np.sqrt(dt)), axis=1
                )
            mass = (mass * keep) @ kernel
        # the cutoff leaves 1 - spent(t_k) of the total mass inside, as a symmetric interval
        target_in = 1.0 - float(spending(kind, t[k], alpha))
        order = np.abs(mid).argsort()
        inside = np.cumsum(mass[order])
        j = int(np.searchsorted(inside, target_in))
        j = min(max(j, 1), len(order) - 1)
        lo, hi = inside[j - 1], inside[j]
        edge = np.abs(mid[order[j - 1]]) + step * (target_in - lo) / (hi - lo if hi > lo else 1.0)
        cuts.append(edge)
        keep = (np.abs(mid) < edge).astype(float)
    return np.asarray(cuts) / np.sqrt(t)


def msprt_log_lambda(effect: np.ndarray, se: np.ndarray, tau: float) -> np.ndarray:
    """Log of the mixture likelihood ratio against zero effect, normal prior of sd ``tau``.

    Reject when it exceeds ``log(1 / alpha)``: by Ville's inequality that happens under the null
    with probability at most ``alpha`` however often it is read.
    """
    v = se**2
    return 0.5 * np.log(v / (v + tau**2)) + tau**2 * effect**2 / (2.0 * v * (v + tau**2))


def first_crossing(rejects: np.ndarray) -> np.ndarray:
    """Index of the first look at which each run rejected, or -1 if it never did.

    ``rejects`` is a boolean array of shape (runs, looks).
    """
    any_ = rejects.any(axis=1)
    return np.where(any_, rejects.argmax(axis=1), -1)


class LookTable:
    """Cumulative outcomes of both worlds at every look, with their squares precomputed.

    ``control`` and ``treated`` have one row per customer and one column per look. In a real
    experiment a customer shows only one of them; here, an assignment picks which, so thousands of
    assignments cost four matrix-vector products each.
    """

    def __init__(self, control: np.ndarray, treated: np.ndarray | None = None) -> None:
        self.c1, self.c0 = (control if treated is None else treated), control
        self.q1, self.q0 = self.c1 * self.c1, self.c0 * self.c0

    def statistics(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Effect and Welch standard error at every look for assignment ``t`` (1 = treated)."""
        t = t.astype(float)
        u = 1.0 - t
        n1, n0 = t.sum(), u.sum()
        m1, m0 = (t @ self.c1) / n1, (u @ self.c0) / n0
        v1 = ((t @ self.q1) - n1 * m1**2) / (n1 - 1)
        v0 = ((u @ self.q0) - n0 * m0**2) / (n0 - 1)
        return m1 - m0, np.sqrt(v1 / n1 + v0 / n0)


def decisions(
    effect: np.ndarray,
    se: np.ndarray,
    bounds: dict[str, np.ndarray],
    tau: float,
    alpha: float = ALPHA,
) -> dict[str, np.ndarray]:
    """Index of the first rejecting look, for every procedure, from effect and se of every look.

    ``effect`` and ``se`` have shape (runs, looks). Procedures: ``naive`` (1.96 at each look),
    one per entry of ``bounds`` (group-sequential), and ``msprt``.
    """
    z = np.abs(effect) / np.where(se > 0, se, np.inf)
    out = {"naive": first_crossing(z > stats.norm.ppf(1 - alpha / 2))}
    for name, c in bounds.items():
        out[name] = first_crossing(z > c[None, :])
    log_l = msprt_log_lambda(effect, np.where(se > 0, se, np.inf), tau)
    out["msprt"] = first_crossing(log_l > math.log(1 / alpha))
    return out
