"""Frequency x severity: the annual fraud-loss distribution, capital and stop-loss cover.

The classical collective risk model of non-life insurance, applied to fraud losses:

* **frequency**: claims per month and fraud type, negative binomial with mean ``exposure x rate``
  (the dispersion says whether claims cluster more than a Poisson process would);
* **severity**: pesos lost per claim, lognormal or lognormal body with a generalised Pareto tail;
* **aggregate**: the sum over a year, built exactly (no simulation noise) by the fast Fourier
  transform of the compound distribution on a peso grid, which makes thousands of bootstrap
  replicates affordable.

Everything is on the *reported* basis the bank can observe; the never-reported share is an explicit
gross-up (:func:`gross_up_means`), not hidden inside the rates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl
from scipy import optimize, special, stats

from fincrime.risk.claims import TYPES

CAP = 1_000_000.0  # no single claim can lose more than this (transfer limits); mass above it sits at the cap
GRID = 50.0  # pesos per cell of the aggregate grid
CELLS = 2**20  # 52 million pesos of support: far beyond any annual loss here
TAIL_QUANTILE = 0.90
PLANTED_REPORT_RATE = {"ato": 0.92, "card_testing": 0.80}


# --------------------------------------------------------------------------------------------
# frequency
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class NBFit:
    """Negative binomial (NB2): mean ``mu``, variance ``mu + alpha mu^2``; rate per exposure unit."""

    rate: float
    alpha: float
    trend: float  # log-linear change per month (0 when not fitted)
    loglik: float
    n_months: int
    cov: np.ndarray  # covariance of (log rate, log alpha)


def _nb_loglik(counts: np.ndarray, mu: np.ndarray, alpha: float) -> float:
    if alpha < 1e-8:
        return float(stats.poisson.logpmf(counts, mu).sum())
    r = 1.0 / alpha
    return float(
        (
            special.gammaln(counts + r)
            - special.gammaln(r)
            - special.gammaln(counts + 1)
            + r * np.log(r / (r + mu))
            + counts * np.log(mu / (r + mu))
        ).sum()
    )


def fit_nb(counts: np.ndarray, exposure: np.ndarray, trend: bool = False) -> NBFit:
    """Maximum likelihood for ``counts ~ NB(exposure x rate x exp(trend x t))``."""
    t = np.arange(len(counts), dtype=float) - (len(counts) - 1) / 2.0

    def nll(theta: np.ndarray) -> float:
        mu = exposure * np.exp(theta[0] + (theta[2] * t if trend else 0.0))
        return -_nb_loglik(counts, mu, float(np.exp(theta[1])))

    if len(counts) < 3 or exposure.sum() <= 0:
        raise ValueError("a frequency fit needs at least three complete months")
    start = np.array([np.log(max(counts.sum(), 0.5) / exposure.sum()), np.log(0.05), 0.0])
    free = [0, 1, 2] if trend else [0, 1]

    def wrapped(x: np.ndarray) -> float:
        full = start.copy()
        full[free] = x
        full[1] = np.clip(full[1], -14.0, 4.0)
        return nll(full)

    res = optimize.minimize(
        wrapped,
        start[free],
        method="Nelder-Mead",
        options={"xatol": 1e-8, "fatol": 1e-10, "maxiter": 4000},
    )
    full = start.copy()
    full[free] = res.x
    full[1] = np.clip(full[1], -14.0, 4.0)
    cov = _inv_hessian(wrapped, res.x)[:2, :2] if len(res.x) >= 2 else np.eye(2)
    # centre the trend at the middle month so the rate is the mid-window rate
    return NBFit(
        float(np.exp(full[0])),
        float(np.exp(full[1])),
        float(full[2]) if trend else 0.0,
        -res.fun,
        len(counts),
        cov,
    )


def _inv_hessian(f, x: np.ndarray, h: float = 1e-3) -> np.ndarray:
    k = len(x)
    H = np.zeros((k, k))
    for a in range(k):
        for b in range(a, k):
            ea, eb = np.eye(k)[a] * h, np.eye(k)[b] * h
            H[a, b] = H[b, a] = (
                f(x + ea + eb) - f(x + ea - eb) - f(x - ea + eb) + f(x - ea - eb)
            ) / (4 * h * h)
    try:
        return np.linalg.pinv(H)
    except np.linalg.LinAlgError:
        return np.eye(k)


def trend_test(counts: np.ndarray, exposure: np.ndarray) -> tuple[float, float]:
    """Likelihood-ratio test of a log-linear trend in the rate: (statistic, p-value)."""
    flat, with_trend = fit_nb(counts, exposure), fit_nb(counts, exposure, trend=True)
    stat = max(2 * (with_trend.loglik - flat.loglik), 0.0)
    return float(stat), float(stats.chi2.sf(stat, 1))


# --------------------------------------------------------------------------------------------
# severity
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Severity:
    """Lognormal (``u is None``) or lognormal body with a generalised Pareto tail above ``u``."""

    mu: float
    sigma: float
    u: float | None = None
    p_body: float = 1.0  # share of claims at or below u
    xi: float = 0.0
    beta: float = 1.0

    def survival(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        ln = stats.norm.sf((np.log(np.maximum(x, 1e-12)) - self.mu) / self.sigma)
        if self.u is None:
            return ln
        f_u = stats.norm.cdf((np.log(self.u) - self.mu) / self.sigma)
        body = (
            1.0
            - self.p_body
            * stats.norm.cdf((np.log(np.maximum(x, 1e-12)) - self.mu) / self.sigma)
            / f_u
        )
        tail = (1.0 - self.p_body) * stats.genpareto.sf(
            np.maximum(x - self.u, 0.0), self.xi, scale=self.beta
        )
        return np.where(x <= self.u, body, tail)

    def logpdf(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        lx = np.log(x)
        base = stats.norm.logpdf((lx - self.mu) / self.sigma) - np.log(self.sigma * x)
        if self.u is None:
            return base
        f_u = stats.norm.cdf((np.log(self.u) - self.mu) / self.sigma)
        body = np.log(self.p_body / f_u) + base
        tail = np.log(1.0 - self.p_body) + stats.genpareto.logpdf(
            np.maximum(x - self.u, 0.0), self.xi, scale=self.beta
        )
        return np.where(x <= self.u, body, tail)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Inverse-transform draws, capped at ``CAP``."""
        q = rng.random(n)
        if self.u is None:
            x = np.exp(self.mu + self.sigma * stats.norm.ppf(q))
        else:
            f_u = stats.norm.cdf((np.log(self.u) - self.mu) / self.sigma)
            body = np.exp(
                self.mu
                + self.sigma * stats.norm.ppf(np.clip(q / self.p_body * f_u, 1e-12, 1 - 1e-12))
            )
            tail = self.u + stats.genpareto.ppf(
                np.clip((q - self.p_body) / (1.0 - self.p_body), 0.0, 1 - 1e-12),
                self.xi,
                scale=self.beta,
            )
            x = np.where(q <= self.p_body, body, tail)
        return np.minimum(x, CAP)


def fit_severity(amounts: np.ndarray, kind: str = "lognormal") -> Severity:
    """``kind``: ``"lognormal"``; ``"splice"`` (body censored at the 90th percentile, GPD above); or
    ``"splice_nonneg"``, the same with the GPD shape held at 0 or above.

    The unconstrained GPD can come out with a negative shape, i.e. a *finite largest possible loss*,
    which a later, larger claim then contradicts (an infinite penalty out of sample). The constrained
    version keeps the tail at least exponential, so a bigger claim than any seen is possible.
    """
    x = np.asarray(amounts, dtype=float)
    x = x[x > 0]
    lx = np.log(x)
    if kind == "lognormal":
        return Severity(float(lx.mean()), float(lx.std(ddof=0)))
    u = float(np.quantile(x, TAIL_QUANTILE))
    body, exc = x[x <= u], x[x > u] - u
    lb = np.log(body)

    def nll(
        theta: np.ndarray,
    ) -> float:  # lognormal fitted to the body, claims above u treated as censored at u
        mu, sigma = theta[0], np.exp(theta[1])
        z = (lb - mu) / sigma
        ll = (-np.log(sigma) - 0.5 * z**2).sum() + len(exc) * stats.norm.logsf(
            (np.log(u) - mu) / sigma
        )
        return -float(ll)

    res = optimize.minimize(nll, [lb.mean(), np.log(lb.std() + 1e-6)], method="Nelder-Mead")
    xi, _, beta = stats.genpareto.fit(exc, floc=0.0)
    if kind == "splice_nonneg" and xi < 0:
        xi, beta = (
            0.0,
            float(exc.mean()),
        )  # the constrained maximum is on the boundary: exponential tail
    return Severity(
        float(res.x[0]), float(np.exp(res.x[1])), u, len(body) / len(x), float(xi), float(beta)
    )


def heldout_loglik(model: Severity, amounts: np.ndarray) -> float:
    """Mean log density of ``amounts`` under ``model`` (higher is better)."""
    return float(np.mean(model.logpdf(np.asarray(amounts, dtype=float))))


# --------------------------------------------------------------------------------------------
# discretisation and the FFT aggregate
# --------------------------------------------------------------------------------------------
def discretise(sev: Severity, h: float = GRID, cap: float = CAP) -> np.ndarray:
    """Probability mass on the grid 0, h, 2h, ... cap that **preserves the mean** (local moment matching).

    With L(x) = E[min(X, x)], mass_0 = 1 - L(h)/h, mass_j = (2L(jh) - L((j-1)h) - L((j+1)h))/h and the last
    cell takes the rest, so the grid distribution has exactly the capped mean of the model. L is
    the integral of the survival function, done on a grid ten times finer.
    """
    m = round(cap / h)
    fine = 10
    mid = (np.arange(m * fine) + 0.5) * (h / fine)
    inc = sev.survival(mid) * (h / fine)
    cum = np.concatenate([[0.0], np.cumsum(inc)])
    lev = cum[::fine]  # L at 0, h, ..., cap
    pmf = np.empty(m + 1)
    pmf[0] = 1.0 - lev[1] / h
    pmf[1:m] = (2 * lev[1:m] - lev[: m - 1] - lev[2 : m + 1]) / h
    pmf[m] = (lev[m] - lev[m - 1]) / h
    return np.clip(pmf, 0.0, None)


@dataclass(frozen=True)
class Aggregate:
    """Distribution of the annual loss on the grid ``0, h, 2h, ...``."""

    pmf: np.ndarray
    h: float = GRID

    @property
    def x(self) -> np.ndarray:
        return np.arange(len(self.pmf)) * self.h

    @property
    def mean(self) -> float:
        return float((self.x * self.pmf).sum())

    @property
    def std(self) -> float:
        return float(np.sqrt(((self.x - self.mean) ** 2 * self.pmf).sum()))

    def cdf(self) -> np.ndarray:
        return np.cumsum(self.pmf)

    def var(self, q: float) -> float:
        """Smallest grid value whose cumulative probability reaches ``q`` (value at risk)."""
        return float(self.x[min(int(np.searchsorted(self.cdf(), q)), len(self.pmf) - 1)])

    def tvar(self, q: float) -> float:
        """Mean loss in the worst ``1 - q`` of years (tail value at risk), splitting the atom at VaR."""
        v = self.var(q)
        i = round(v / self.h)
        cdf_before = self.cdf()[i] - self.pmf[i]
        tail_mass = 1.0 - q
        beyond = (self.x[i + 1 :] * self.pmf[i + 1 :]).sum()
        atom = (self.cdf()[i] - max(q, cdf_before)) * v
        return float((beyond + atom) / tail_mass)

    def stop_loss(self, retention: float) -> float:
        """Expected recovery of an aggregate stop-loss that pays the part of the year's loss above ``retention``."""
        return float(((self.x - retention).clip(0.0) * self.pmf).sum())

    def prob_at_most(self, loss: float) -> float:
        return float(self.cdf()[min(round(loss / self.h), len(self.pmf) - 1)])


def aggregate(
    sev_pmf: dict[str, np.ndarray],
    means: dict[str, float],
    alphas: dict[str, float],
    cells: int = CELLS,
    h: float = GRID,
) -> Aggregate:
    """Compound distribution of the year's loss, exactly, by FFT.

    For each type the number of claims in the year is negative binomial with mean ``means[k]`` and
    dispersion ``alphas[k]``, whose probability generating function is (1 + alpha mu (1 - z))^(-1/alpha).
    Applied to the transform of the severity mass this is the transform of the compound sum;
    the types are independent, so the log-transforms add. A year of independent NB months is
    matched to one NB with the same mean and variance (the caller passes the annual ``alpha``).
    """
    log_phi = np.zeros(cells, dtype=complex)
    for k, pmf in sev_pmf.items():
        phi = np.fft.fft(pmf, cells)
        mu, a = means[k], alphas[k]
        log_phi += mu * (phi - 1.0) if a < 1e-8 else -np.log1p(a * mu * (1.0 - phi)) / a
    out = np.fft.ifft(np.exp(log_phi)).real
    out = np.clip(out, 0.0, None)
    return Aggregate(out / out.sum(), h)


def annual_alpha(alpha: float, monthly_means: np.ndarray) -> float:
    """Dispersion of the sum of independent NB months expressed as one NB: alpha sum(mu_t^2) / (sum mu_t)^2."""
    return float(alpha * (monthly_means**2).sum() / monthly_means.sum() ** 2)


def simulate_mc(
    sevs: dict[str, Severity],
    means: dict[str, float],
    alphas: dict[str, float],
    years: int,
    seed: int = 0,
) -> np.ndarray:
    """Plain Monte Carlo of the same compound distribution (used by the tests, never for results)."""
    rng = np.random.default_rng(seed)
    total = np.zeros(years)
    for k, sev in sevs.items():
        mu, a = means[k], alphas[k]
        n = rng.poisson(mu, years) if a < 1e-8 else rng.poisson(rng.gamma(1.0 / a, a * mu, years))
        flat = sev.sample(int(n.sum()), rng)
        idx = np.repeat(np.arange(years), n)
        total += np.bincount(idx, weights=flat, minlength=years)
    return total


# --------------------------------------------------------------------------------------------
# putting it together
# --------------------------------------------------------------------------------------------
def future_exposure(exposure: pl.DataFrame, scenario: str, months: int = 12) -> np.ndarray:
    """Account-months of the next ``months`` months under a named onboarding scenario.

    ``base``: onboarding continues at the mean of the last six months; ``flat``: no new accounts;
    ``high``: onboarding 50% above ``base``. Open accounts at the end of the last observed month are
    its mean exposure plus half of that month's onboarding.
    """
    e = exposure.sort("month")
    onboarding = float(e["opened"].tail(6).mean())
    rate = {"base": onboarding, "flat": 0.0, "high": 1.5 * onboarding}[scenario]
    open_end = float(e["accounts"][-1] + e["opened"][-1] / 2.0)
    return np.array([open_end + rate * (t + 0.5) for t in range(months)])


def gross_up_means(means: dict[str, float], factor: float = 1.0) -> dict[str, float]:
    """Counts of claims that will ever be *reported* scaled to all claims: divide by the report rate.

    ``factor`` 1.0 uses the planted rates, 0.5 and 2.0 halve and double the share never reported.
    """
    return {k: m / (1.0 - factor * (1.0 - PLANTED_REPORT_RATE[k])) for k, m in means.items()}


@dataclass(frozen=True)
class Fit:
    """Everything the annual distribution needs, for one set of data."""

    nb: dict[str, NBFit]
    sev: dict[str, Severity]
    severity_kind: str


def fit_model(
    completed: np.ndarray,
    mask: np.ndarray,
    exposure: np.ndarray,
    amounts: dict[str, np.ndarray],
    severity_kind: str,
) -> Fit:
    """Fit frequency (completed counts on the complete cohorts) and severity by type.

    ``completed`` is (cohorts, types); ``mask`` marks the cohorts that enter the fit.
    """
    nb = {k: fit_nb(completed[mask[:, j], j], exposure[mask[:, j]]) for j, k in enumerate(TYPES)}
    sev = {k: fit_severity(amounts[k], severity_kind) for k in TYPES}
    return Fit(nb, sev, severity_kind)


def annual_distribution(
    fit: Fit, future: np.ndarray, gross: float | None = None, cells: int = CELLS
) -> Aggregate:
    """Loss distribution over the months of ``future`` (account-months): a year, or any window."""
    monthly = {k: fit.nb[k].rate * future for k in TYPES}
    means = {k: float(v.sum()) for k, v in monthly.items()}
    if gross is not None:
        means = gross_up_means(means, gross)
    alphas = {k: annual_alpha(fit.nb[k].alpha, monthly[k]) for k in TYPES}
    pmfs = {k: discretise(fit.sev[k]) for k in TYPES}
    return aggregate(pmfs, means, alphas, cells)


def summary(dist: Aggregate, expected_loss: float | None = None) -> dict[str, float]:
    """The reported figures of one annual distribution."""
    el = dist.mean if expected_loss is None else expected_loss
    var995 = dist.var(0.995)
    out = {
        "expected_loss": el,
        "std": dist.std,
        "var_95": dist.var(0.95),
        "var_995": var995,
        "capital": var995 - el,
        "tvar_99": dist.tvar(0.99),
    }
    for k in (1.05, 1.1, 1.2, 1.5, 2.0):
        out[f"stop_loss_{round(k * 100)}"] = dist.stop_loss(k * el)
    return out


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)
