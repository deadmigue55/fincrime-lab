"""From claims to capital: the maturation and loss models run end to end, with their uncertainty.

One function, :func:`forecast`, does for any bank (the main one, or each of the 200 worlds of
``simcheck``) what the bank would do at an as-of date:

1. complete the recent months with the delay model (M4), so that a new month is not read as "no fraud";
2. fit frequency per fraud type on the months that are at least 95% complete;
3. fit severity per fraud type on the claims reported so far;
4. build the loss distribution of a future window of months, by FFT;
5. repeat 2 to 4 on bootstrap replicates: the monthly counts are redrawn from the fitted negative
   binomial (parametric bootstrap, which carries the sampling error of the rate) and the claims are
   resampled with replacement for the severity, so every figure carries the uncertainty of the data
   behind it. The months entering the frequency fit are at least 95% complete, so the remaining
   uncertainty about their unreported claims is small and is not redrawn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from fincrime.risk import delay, lossmodel
from fincrime.risk.claims import TYPES, visible

COMPLETE = 0.95  # a month enters the frequency fit when this share of its claims is expected to be reported


@dataclass
class Completed:
    """Claims per month and type, completed for what is still to be reported."""

    months: list[date]
    reported: np.ndarray  # (months, types)
    point: np.ndarray  # reported / completion
    completion: np.ndarray
    mask: np.ndarray  # months that enter the frequency fit
    draws: np.ndarray  # (reps, months, types): reported + a predictive draw of the unreported


def complete(claims: pl.DataFrame, as_of: date, reps: int, seed: int) -> Completed:
    """M4 completion of the monthly counts, with predictive draws for the bootstrap."""
    pred = delay.predictive(claims, as_of, reps=max(reps, 1), seed=seed)
    mat = pred["maturity"]
    return Completed(
        mat.months,
        mat.reported_n,
        mat.ultimate_n,
        mat.completion,
        mat.completion >= COMPLETE,
        mat.reported_n[None] + pred["counts"],
    )


def _amounts(
    claims: pl.DataFrame, as_of: date, rng: np.random.Generator | None = None
) -> dict[str, np.ndarray]:
    seen = visible(claims, as_of)
    out = {}
    for k in TYPES:
        a = seen.filter(pl.col("fraud_type") == k)["amount"].to_numpy()
        out[k] = a if rng is None else rng.choice(a, size=len(a))
    return out


def simulate_counts(
    nb: dict[str, lossmodel.NBFit], exposure: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """One parametric-bootstrap draw of the monthly claim counts, (months, types), from fitted NB models."""
    out = np.zeros((len(exposure), len(TYPES)))
    for j, k in enumerate(TYPES):
        mu = nb[k].rate * exposure
        a = nb[k].alpha
        out[:, j] = rng.poisson(mu) if a < 1e-8 else rng.poisson(rng.gamma(1.0 / a, a * mu))
    return out


def _fit(
    counts: np.ndarray, mask: np.ndarray, exposure: np.ndarray, amounts: dict, kind: str
) -> lossmodel.Fit:
    return lossmodel.fit_model(counts, mask, exposure, amounts, kind)


def forecast(
    claims: pl.DataFrame,
    exposure: pl.DataFrame,
    as_of: date,
    future: np.ndarray,
    reps: int = 200,
    seed: int = 0,
    severity_kind: str = "lognormal",
    gross: float | None = None,
    cells: int = lossmodel.CELLS,
    keep_pmf: bool = False,
) -> dict:
    """Point estimate and bootstrap replicates of the loss over the months with exposure ``future``.

    ``exposure`` holds the months up to ``as_of`` (``month``, ``accounts``). Returns the point
    summary, one summary per replicate, and (``keep_pmf``) the average of the replicate
    distributions, which is the predictive distribution used to score a forecast.
    """
    rng = np.random.default_rng(seed)
    e = exposure.sort("month")["accounts"].to_numpy()
    done = complete(claims, as_of, 1, seed + 1)
    e = e[: len(done.months)]
    point_fit = _fit(done.point, done.mask, e, _amounts(claims, as_of), severity_kind)
    dist = lossmodel.annual_distribution(point_fit, future, gross, cells)
    out = {
        "fit": point_fit,
        "point": lossmodel.summary(dist),
        "completed": done,
        "reps": [],
        # section 8 keeps a trend only if p < 0.01; none is modelled, so the test is reported with every forecast
        "trend_p": {
            k: lossmodel.trend_test(done.point[done.mask[:, j], j], e[done.mask[:, j]])[1]
            for j, k in enumerate(TYPES)
        },
    }
    reps_n = max(reps, 1)
    pmf_sum = np.zeros(cells) if keep_pmf else None
    for _ in range(reps):
        fit = _fit(
            simulate_counts(point_fit.nb, e, rng),
            done.mask,
            e,
            _amounts(claims, as_of, rng),
            severity_kind,
        )
        d = lossmodel.annual_distribution(fit, future, gross, cells)
        out["reps"].append(lossmodel.summary(d))
        if keep_pmf:
            pmf_sum += d.pmf
    if keep_pmf:
        out["predictive"] = lossmodel.Aggregate(pmf_sum / reps_n)
    return out


def intervals(reps: list[dict], level: float = 0.95) -> dict[str, tuple[float, float]]:
    """Percentile interval of every summary figure across the bootstrap replicates."""
    lo, hi = 100 * (1 - level) / 2, 100 * (1 + level) / 2
    return {k: tuple(np.percentile([r[k] for r in reps], [lo, hi])) for k in reps[0]}


def realised_loss(claims: pl.DataFrame, first: date, last: date) -> float:
    """Pesos of the claims lost in ``first`` to ``last`` (months, inclusive) that are ever reported."""
    end = lossmodel.month_end(last)
    s = claims.filter(
        pl.col("reported_ts").is_not_null()
        & (pl.col("loss_ts") >= pl.datetime(first.year, first.month, 1))
        & (pl.col("loss_ts") <= pl.datetime(end.year, end.month, end.day, 23, 59, 59))
    )
    return float(s["amount"].sum())
