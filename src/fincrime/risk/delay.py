"""M4: an explicit reporting-delay model, fitted to what has been reported so far.

The triangle methods read the pattern off a coarse monthly grid. Here the delay between the loss
and its report is modelled directly, by fraud type, as a lognormal, and the completion factor of a
cohort is the probability that one of its claims has been reported by the as-of date.

The one subtlety, and the reason this is not a plain lognormal fit: at an as-of date D the bank only
sees claims whose delay is *short enough* to have arrived, a claim lost ``c`` days before D is in the
data only if its delay is at most ``c``. Fitting a lognormal to the observed delays ignores that and
is biased low (the slow reports are the missing ones). The likelihood of a delay ``t`` given that it
was observed is ``f(t) / F(c)``: a **right-truncated** lognormal, which is what is maximised here.

The cohort's ultimate is then ``reported / completion`` (the Poisson maximum likelihood estimate
of the number of claims given that each one is reported by D with that probability).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import polars as pl
from scipy import optimize, stats

from fincrime.risk.claims import TYPES, visible

DAY = 86400.0
GRID = 60  # points used to average the completion over the loss times of one month


@dataclass(frozen=True)
class DelayFit:
    """Lognormal delay in days: ``ln T ~ Normal(mu, sigma)``, with the covariance of (mu, ln sigma)."""

    mu: float
    sigma: float
    cov: np.ndarray
    n: int

    @property
    def median_days(self) -> float:
        return float(np.exp(self.mu))

    def cdf(self, days: np.ndarray | float) -> np.ndarray:
        return stats.norm.cdf((np.log(np.maximum(days, 1e-9)) - self.mu) / self.sigma)


def _nll(theta: np.ndarray, log_t: np.ndarray, log_c: np.ndarray) -> float:
    mu, sigma = theta[0], np.exp(theta[1])
    z = (log_t - mu) / sigma
    ll = -np.log(sigma) - 0.5 * z**2 - stats.norm.logcdf((log_c - mu) / sigma)
    return float(-ll.sum())


def fit_delay(delay_days: np.ndarray, trunc_days: np.ndarray) -> DelayFit:
    """Right-truncated lognormal maximum likelihood; ``trunc_days[i] >= delay_days[i]``."""
    log_t, log_c = np.log(delay_days), np.log(np.maximum(trunc_days, delay_days))
    start = np.array([log_t.mean(), np.log(max(log_t.std(), 0.2))])
    res = optimize.minimize(
        _nll,
        start,
        args=(log_t, log_c),
        method="Nelder-Mead",
        options={"xatol": 1e-7, "fatol": 1e-9, "maxiter": 2000},
    )
    theta = res.x
    cov = np.linalg.pinv(_hessian(theta, log_t, log_c))
    return DelayFit(float(theta[0]), float(np.exp(theta[1])), cov, len(delay_days))


def _hessian(
    theta: np.ndarray, log_t: np.ndarray, log_c: np.ndarray, h: float = 1e-4
) -> np.ndarray:
    k = len(theta)
    H = np.zeros((k, k))
    f0 = _nll(theta, log_t, log_c)
    for a in range(k):
        for b in range(a, k):
            ea, eb = np.eye(k)[a] * h, np.eye(k)[b] * h
            v = (
                _nll(theta + ea + eb, log_t, log_c)
                - _nll(theta + ea - eb, log_t, log_c)
                - _nll(theta - ea + eb, log_t, log_c)
                + _nll(theta - ea - eb, log_t, log_c)
            ) / (4 * h * h)
            H[a, b] = H[b, a] = v
    return H if np.all(np.isfinite(H)) else np.eye(k) * f0


def cohort_completion(
    mu: float, sigma: float, as_of: date, month_start: date, grid: int = GRID
) -> float:
    """Mean probability that a claim lost in the month is reported by the end of ``as_of``.

    Loss times are taken as uniform within the month; growth of the exposure inside a month is
    ignored (shortcut, a few percent of one month at most).
    """
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    span = (nxt - month_start).days
    end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59)
    u = (np.arange(grid) + 0.5) / grid * span
    c = (
        end - datetime(month_start.year, month_start.month, month_start.day)
    ).total_seconds() / DAY - u
    return float(stats.norm.cdf((np.log(np.maximum(c, 1e-9)) - mu) / sigma).mean())


def months_from(first: date, n: int) -> list[date]:
    out, y, m = [], first.year, first.month
    for _ in range(n):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fit_by_type(claims: pl.DataFrame, as_of: date) -> dict[str, DelayFit]:
    """One delay model per fraud type, from the claims reported by the end of ``as_of``."""
    end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59)
    seen = visible(claims, as_of)
    out = {}
    for k in TYPES:
        s = seen.filter(pl.col("fraud_type") == k)
        if s.height < 8:
            continue
        t = (s["reported_ts"] - s["loss_ts"]).dt.total_seconds().to_numpy() / DAY
        c = (
            s.select((pl.lit(end) - pl.col("loss_ts")).dt.total_seconds() / DAY)
            .to_series()
            .to_numpy()
        )
        out[k] = fit_delay(np.maximum(t, 1e-3), c)
    return out


@dataclass(frozen=True)
class Maturity:
    """Per cohort and type: what is reported, how complete it is, and the completed estimate."""

    months: list[date]
    types: tuple[str, ...]
    reported_n: np.ndarray  # (cohorts, types)
    reported_amt: np.ndarray
    completion: np.ndarray

    @property
    def ultimate_n(self) -> np.ndarray:
        return self.reported_n / self.completion

    @property
    def ultimate_amt(self) -> np.ndarray:
        return self.reported_amt / self.completion

    @property
    def ibnr_n(self) -> np.ndarray:
        return self.ultimate_n - self.reported_n

    @property
    def ibnr_amt(self) -> np.ndarray:
        return self.ultimate_amt - self.reported_amt


def maturity(
    claims: pl.DataFrame,
    as_of: date,
    fits: dict[str, DelayFit] | None = None,
    first_month: date = date(2024, 1, 1),
) -> tuple[Maturity, dict[str, DelayFit]]:
    """M4 at the month-end ``as_of``: delay fits by type, completion by cohort, completed totals."""
    fits = fits or fit_by_type(claims, as_of)
    n = (as_of.year - first_month.year) * 12 + as_of.month - first_month.month + 1
    months = months_from(first_month, n)
    seen = visible(claims, as_of).with_columns(
        (
            (pl.col("loss_ts").dt.year() - first_month.year) * 12
            + pl.col("loss_ts").dt.month()
            - first_month.month
        ).alias("i")
    )
    types = tuple(k for k in TYPES if k in fits)
    rn, ra, comp = (np.zeros((n, len(types))) for _ in range(3))
    for j, k in enumerate(types):
        s = (
            seen.filter(pl.col("fraud_type") == k)
            .group_by("i")
            .agg(pl.len().alias("n"), pl.col("amount").sum().alias("a"))
        )
        for i, c, a in s.iter_rows():
            rn[i, j], ra[i, j] = c, a
        for i, m in enumerate(months):
            comp[i, j] = cohort_completion(fits[k].mu, fits[k].sigma, as_of, m)
    return Maturity(months, types, rn, ra, comp), fits


def predictive(
    claims: pl.DataFrame,
    as_of: date,
    reps: int = 1000,
    seed: int = 0,
    fits: dict[str, DelayFit] | None = None,
) -> dict:
    """Predictive draws of the unreported claims, by cohort and type.

    Per replicate: draw the delay parameters from their asymptotic normal distribution (parameter
    error), recompute the completion, draw the number of still-unreported claims of each cohort as
    Poisson whose mean is itself gamma distributed with mean ``reported (1 - F) / F`` (process error
    plus the estimation error of the cohort's size), and give each the amount of a claim
    drawn at random from the reported ones of its type (amount independent of delay: tested in
    :func:`amount_delay_correlation`). Returns ``counts`` and ``amounts`` of shape (reps, cohorts, types).
    """
    rng = np.random.default_rng(seed)
    mat, fits = maturity(claims, as_of, fits)
    seen = visible(claims, as_of)
    pool = {k: seen.filter(pl.col("fraud_type") == k)["amount"].to_numpy() for k in mat.types}
    n, kt = mat.reported_n.shape
    counts = np.zeros((reps, n, kt))
    amounts = np.zeros((reps, n, kt))
    for j, k in enumerate(mat.types):
        f = fits[k]
        try:
            draws = rng.multivariate_normal([f.mu, np.log(f.sigma)], f.cov, size=reps)
        except (np.linalg.LinAlgError, ValueError):
            draws = np.tile([f.mu, np.log(f.sigma)], (reps, 1))
        for r in range(reps):
            mu, sigma = draws[r, 0], float(np.exp(draws[r, 1]))
            for i, m in enumerate(mat.months):
                if mat.reported_n[i, j] == 0:
                    continue
                c = cohort_completion(mu, sigma, as_of, m, grid=20)
                # unreported ~ Poisson(lam), lam ~ Gamma(reported, (1-c)/c): the gamma carries the
                # estimation error of the cohort's size (a plain Poisson would drop it, and the
                # newest cohorts, where c is small, would get intervals about 1.7 times too narrow)
                lam = rng.gamma(mat.reported_n[i, j], (1.0 - c) / max(c, 1e-6))
                kk = rng.poisson(lam)
                counts[r, i, j] = kk
                if kk:
                    amounts[r, i, j] = rng.choice(pool[k], size=kk).sum()
    return {
        "counts": counts,
        "amounts": amounts,
        "months": mat.months,
        "types": mat.types,
        "maturity": mat,
        "fits": fits,
    }


def amount_delay_correlation(claims: pl.DataFrame, as_of: date) -> dict[str, float]:
    """Spearman correlation of amount and delay among reported claims, by type (assumption check)."""
    seen = visible(claims, as_of)
    out = {}
    for k in TYPES:
        s = seen.filter(pl.col("fraud_type") == k)
        if s.height > 20:
            d = (s["reported_ts"] - s["loss_ts"]).dt.total_seconds().to_numpy()
            out[k] = float(stats.spearmanr(s["amount"].to_numpy(), d).statistic)
    return out


__all__ = [
    "DelayFit",
    "Maturity",
    "amount_delay_correlation",
    "cohort_completion",
    "fit_by_type",
    "fit_delay",
    "maturity",
    "predictive",
]
