"""Development triangles and the classical reserving methods, from first principles.

Rows are *cohorts* (the month a loss happened), columns the *development lag* (months from the loss
month to the month it was reported). At a month-end D the triangle is filled for lag <= (D month -
cohort month), the rest is the future. The job is to predict that future: the claims that have
happened and not yet been reported, i.e. IBNR (incurred but not reported).

Everything here is written for an array of *cumulative* values ``C[i, j]`` (NaN where unobserved),
and works the same for counts and pesos:

* :func:`chain_ladder`    volume-weighted development factors, a tail, and the ultimates;
* :func:`mack`            Mack's distribution-free standard error of the reserve (1993);
* :func:`odp_bootstrap`   England and Verrall's over-dispersed Poisson bootstrap (predictive
  distribution, process and parameter error together);
* :func:`bornhuetter_ferguson`  the prior-weighted estimate for immature cohorts.

:func:`build_triangle` cuts the claims at the as-of date first, so a triangle can never contain a
report from the future.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from fincrime.risk.claims import month_index, visible

TAIL_LAGS = 24
TAIL_POINTS = 3


# --------------------------------------------------------------------------------------------
# building the triangle
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Triangle:
    """Incremental values by cohort and lag, plus the labels needed to read them."""

    first_month: date  # cohort 0
    incr: np.ndarray  # (n, n), NaN where the cell lies in the future

    @property
    def n(self) -> int:
        return self.incr.shape[0]

    @property
    def cum(self) -> np.ndarray:
        return np.where(np.isnan(self.incr), np.nan, np.nancumsum(self.incr, axis=1))

    def latest(self) -> np.ndarray:
        """The last diagonal: what each cohort has reported so far."""
        c = self.cum
        return np.array([c[i, self.n - 1 - i] for i in range(self.n)])

    def cohort_months(self) -> list[date]:
        y, m = self.first_month.year, self.first_month.month
        out = []
        for _ in range(self.n):
            out.append(date(y, m, 1))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out


def build_triangle(
    claims: pl.DataFrame,
    as_of: date,
    value: str = "count",
    fraud_type: str | None = None,
    first_month: date = date(2024, 1, 1),
) -> Triangle:
    """The triangle the bank can see at the end of month ``as_of`` (a month-end).

    ``value`` is ``"count"`` or ``"amount"``. ``fraud_type`` selects one type, ``None`` pools them.
    """
    if (as_of.month, as_of.day) != _month_end(as_of):
        raise ValueError("triangles are built at month-ends so every cell is a whole month")
    seen = visible(claims, as_of)
    if fraud_type is not None:
        seen = seen.filter(pl.col("fraud_type") == fraud_type)
    base = first_month.year * 12 + first_month.month - 1
    n = as_of.year * 12 + as_of.month - 1 - base + 1
    incr = np.full((n, n), np.nan)
    for i in range(n):
        incr[i, : n - i] = 0.0
    if seen.height:
        df = seen.with_columns(
            (month_index(pl.col("loss_ts")) - base).alias("i"),
            (month_index(pl.col("reported_ts")) - month_index(pl.col("loss_ts"))).alias("j"),
        )
        weight = pl.len().cast(pl.Float64) if value == "count" else pl.col("amount").sum()
        cells = df.group_by("i", "j").agg(weight.alias("v"))
        for i, j, v in cells.iter_rows():
            if i >= 0:
                incr[i, j] += v
    return Triangle(first_month, incr)


def _month_end(d: date) -> tuple[int, int]:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return d.month, (nxt - date(d.year, d.month, 1)).days


# --------------------------------------------------------------------------------------------
# chain ladder
# --------------------------------------------------------------------------------------------
def dev_factors(cum: np.ndarray) -> np.ndarray:
    """Volume-weighted age-to-age factors: f_j = sum C[i,j+1] / sum C[i,j] over rows with both."""
    n = cum.shape[0]
    f = np.ones(n - 1)
    for j in range(n - 1):
        rows = np.arange(n - 1 - j)  # cohorts observed at lag j+1
        den = cum[rows, j].sum()
        if den > 0:
            f[j] = cum[rows, j + 1].sum() / den
    return f


def tail_factor(f: np.ndarray, lags: int = TAIL_LAGS, points: int = TAIL_POINTS) -> float:
    """Exponential-decay tail, fixed in advance (docs/methodology.md section 8).

    Fit ln(f_j - 1) = a + b j to the last ``points`` factors above 1 and extrapolate ``lags``
    further lags. Returns 1.0 when fewer than ``points`` factors exceed 1 or the fit does not decay.
    """
    j = np.flatnonzero(f > 1.0 + 1e-9)
    if len(j) < points:
        return 1.0
    j = j[-points:]
    b, a = np.polyfit(j, np.log(f[j] - 1.0), 1)
    if b >= 0:
        return 1.0
    ahead = np.arange(len(f), len(f) + lags)
    return float(np.prod(1.0 + np.exp(a + b * ahead)))


@dataclass(frozen=True)
class ChainLadder:
    factors: np.ndarray
    tail: float
    cdf: np.ndarray  # cdf[i] = factor from cohort i's latest cell to ultimate
    latest: np.ndarray
    ultimate: np.ndarray

    @property
    def ibnr(self) -> np.ndarray:
        return self.ultimate - self.latest


def chain_ladder(cum: np.ndarray, tail: float | None = None) -> ChainLadder:
    """Volume-weighted chain ladder with the fixed tail rule (or an explicit ``tail``)."""
    n = cum.shape[0]
    f = dev_factors(cum)
    t = tail_factor(f) if tail is None else tail
    to_ult = (
        np.append(np.cumprod(f[::-1])[::-1], 1.0) * t
    )  # to_ult[k]: factor from lag k to ultimate
    latest = np.array([cum[i, n - 1 - i] for i in range(n)])
    cdf = np.array([to_ult[n - 1 - i] for i in range(n)])
    return ChainLadder(f, t, cdf, latest, latest * cdf)


# --------------------------------------------------------------------------------------------
# Mack's standard error
# --------------------------------------------------------------------------------------------
def mack(cum: np.ndarray) -> tuple[np.ndarray, float]:
    """Mack (1993) standard errors of the IBNR: per cohort and for the total.

    Distribution-free: assumes only that the next cumulative value has mean f_j times the current
    one and a variance proportional to it. There is no tail: the figure is the standard error of the
    reserve *within* the triangle, which understates the uncertainty when a tail is applied.
    """
    n = cum.shape[0]
    f = dev_factors(cum)
    sigma2 = np.zeros(n - 1)
    for j in range(n - 2):
        rows = np.arange(n - 1 - j)
        c = cum[rows, j]
        ok = c > 0
        r = cum[rows, j + 1][ok] / c[ok] - f[j]
        sigma2[j] = (c[ok] * r**2).sum() / max(ok.sum() - 1, 1)
    if n >= 4:
        s_a, s_b = sigma2[n - 3], sigma2[n - 4]
        sigma2[n - 2] = min(s_a**2 / s_b if s_b > 0 else 0.0, s_a, s_b)
    full = _project(cum, f)
    col_sum = np.array([cum[: n - 1 - k, k].sum() for k in range(n - 1)])
    ult = full[:, -1]
    mse = np.zeros(n)
    for i in range(1, n):
        for k in range(n - 1 - i, n - 1):
            mse[i] += sigma2[k] / f[k] ** 2 * (1.0 / max(full[i, k], 1e-9) + 1.0 / col_sum[k])
    mse *= ult**2
    total = mse.sum()
    for i in range(1, n):
        for ell in range(i + 1, n):
            ks = range(n - 1 - i, n - 1)
            inner = sum(sigma2[k] / f[k] ** 2 / col_sum[k] for k in ks)
            total += 2 * ult[i] * ult[ell] * inner
    return np.sqrt(mse), float(np.sqrt(total))


def _project(cum: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Fill the lower triangle with chain-ladder projections (no tail)."""
    n = cum.shape[0]
    full = cum.copy()
    for i in range(n):
        for j in range(n - i, n):
            full[i, j] = full[i, j - 1] * f[j - 1]
    return full


# --------------------------------------------------------------------------------------------
# over-dispersed Poisson bootstrap
# --------------------------------------------------------------------------------------------
def odp_bootstrap(
    incr: np.ndarray,
    reps: int = 1000,
    seed: int = 0,
    tail: float | None = None,
) -> np.ndarray:
    """Predictive draws of the IBNR by cohort, shape (reps, cohorts) (England and Verrall, 1999/2002).

    Steps per replicate: resample the scaled Pearson residuals of the chain-ladder fit, rebuild a
    pseudo triangle, refit the factors (parameter error), project the future cells and draw each
    from a gamma with the over-dispersed Poisson mean-variance relation (process error). The tail
    is the one fitted to the real triangle, applied to each simulated ultimate-at-last-lag.
    shortcut: the tail is held fixed, so it has neither parameter nor process error; refitting it
    on every pseudo triangle was tried and let the exponential extrapolation blow up (mean 40%
    above the chain ladder), a worse error than the one it fixes.
    """
    rng = np.random.default_rng(seed)
    n = incr.shape[0]
    obs = ~np.isnan(incr)
    cum = np.where(obs, np.nancumsum(np.where(obs, incr, 0.0), axis=1), np.nan)
    tail = tail_factor(dev_factors(cum)) if tail is None else tail
    fitted = _fitted_incr(cum)
    m_obs = fitted[obs]
    pos = m_obs > 1e-9
    resid = np.zeros(obs.sum())
    resid[pos] = (incr[obs][pos] - m_obs[pos]) / np.sqrt(m_obs[pos])
    n_cells, n_par = obs.sum(), 2 * n - 1
    phi = max((resid**2).sum() / max(n_cells - n_par, 1), 1e-9)
    adj = resid * np.sqrt(n_cells / max(n_cells - n_par, 1))
    pool = adj[pos]  # cells fitted at zero carry no information about spread
    if pool.size == 0:
        return np.zeros((reps, n))
    draws = np.empty((reps, n))
    for r in range(reps):
        star = np.zeros_like(incr)
        star[obs] = np.where(
            pos, m_obs + rng.choice(pool, size=n_cells) * np.sqrt(np.maximum(m_obs, 0.0)), 0.0
        )
        star = np.where(obs, np.maximum(star, 0.0), np.nan)
        cstar = np.where(obs, np.cumsum(np.where(obs, star, 0.0), axis=1), np.nan)
        fs = dev_factors(cstar)
        full = _project(cstar, fs)
        future = np.diff(full, axis=1, prepend=0.0)
        future = np.where(np.arange(n)[None, :] >= (n - np.arange(n))[:, None], future, 0.0)
        mean = np.maximum(future, 0.0)
        shape = np.where(mean > 0, mean / phi, 1.0)
        sim = np.where(mean > 0, rng.gamma(shape, phi), 0.0)
        ult_last = np.array([cstar[i, n - 1 - i] for i in range(n)]) + sim.sum(axis=1)
        draws[r] = sim.sum(axis=1) + (tail - 1.0) * ult_last
    return draws


def _fitted_incr(cum: np.ndarray) -> np.ndarray:
    """Chain-ladder fitted incrementals on the observed cells, by back-casting the last diagonal."""
    n = cum.shape[0]
    f = dev_factors(cum)
    fit = np.full((n, n), np.nan)
    for i in range(n):
        last = n - 1 - i
        c = cum[i, last]
        col = np.empty(last + 1)
        col[last] = c
        for j in range(last - 1, -1, -1):
            col[j] = col[j + 1] / f[j] if f[j] > 0 else col[j + 1]
        fit[i, : last + 1] = np.diff(np.concatenate([[0.0], col]))
    return fit


# --------------------------------------------------------------------------------------------
# Bornhuetter-Ferguson
# --------------------------------------------------------------------------------------------
def bornhuetter_ferguson(
    latest: np.ndarray, cdf: np.ndarray, prior_ultimate: np.ndarray
) -> np.ndarray:
    """BF ultimates: reported so far + prior ultimate x the share still to come (1 - 1/CDF).

    A weighted average of the chain-ladder answer and the prior, with the weight on the data equal
    to the share already reported: young cohorts lean on the prior, old ones on what happened.
    """
    return latest + prior_ultimate * (1.0 - 1.0 / cdf)
