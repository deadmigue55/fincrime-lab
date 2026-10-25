"""The step 7 report: runs the protocol of docs/methodology.md section 8 on the main bank, writes the
numbers to ``results.json`` and draws the figures of docs/fraud-loss.md.

Order of work (each block uses only what the protocol allows it to):

1. claims and the as-of cut; the delay model against the planted parameters;
2. the IBNR backtest on the selection dates, the choice, then the confirmation dates;
3. maturity of the newest months at the end of the data;
4. frequency, severity (chosen on held-out claims), and the annual loss under three exposure scenarios;
5. an out-of-time check of the loss forecast on this bank;
6. the 200-world calibration, if it has been run.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import matplotlib
import matplotlib.ticker
import numpy as np
import polars as pl
from scipy import stats

matplotlib.use("Agg")

from fincrime import figures as fg
from fincrime.risk import backtest, delay, lossmodel, pipeline, simcheck
from fincrime.risk import claims as cl

END = date(2025, 6, 30)
SCENARIOS = ("flat", "base", "high")
GROSS = (0.5, 1.0, 2.0)
DAYS = (30, 60, 90, 120, 180, 365)
TYPE_NAME = {"ato": "Account takeover", "card_testing": "Card testing"}
COLORS = {"M1": fg.BLUE, "M2": fg.ORANGE, "M3": fg.MUTED, "M4": fg.AQUA, "M4s": "#8a63d2"}


def analyse(
    bank_dir: Path, worlds_file: Path | None, reps: int = 1000, seed: int = 0
) -> tuple[dict, dict]:
    """Every number of the step 7 report, and the arrays the figures need."""
    claims = cl.load_claims(bank_dir)
    expo = cl.exposure(
        pl.read_parquet(bank_dir / "accounts.parquet"),
        pl.read_parquet(bank_dir / "customers.parquet"),
        date(2024, 1, 1),
        date(2025, 6, 1),
    )
    res: dict = {"as_of": END.isoformat(), "reps": reps}
    plot: dict = {}

    # ---- 1. claims and the delay model --------------------------------------------------------
    res["claims"] = _claims_summary(claims)
    res["delay"], plot["delay"] = _delay_block(claims)

    # ---- 2. the IBNR backtest --------------------------------------------------------------
    bt = backtest.run(claims, expo, reps=reps, seed=seed)
    summ = backtest.summarise(bt)
    res["backtest"] = {
        "chosen": backtest.choose(summ),
        "summary": summ.to_dicts(),
        "variants_tried": len(backtest.METHODS) + len(backtest.EXPLORATORY),
    }
    plot["backtest"] = bt

    # ---- 3. maturity of the newest months -------------------------------------------------------
    res["maturity"], plot["maturity"] = _maturity_block(claims, reps, seed)

    # ---- 4. frequency, severity, aggregate ------------------------------------------------------
    res["frequency"] = _frequency_block(claims, expo, reps, seed)
    res["severity"], plot["severity"], kind = _severity_block(claims)
    res["loss"], plot["loss"] = _loss_block(claims, expo, kind, reps, seed)

    # ---- 5. an out-of-time check on this bank -------------------------------------------------
    res["out_of_time"] = _out_of_time(claims, expo, kind, seed)

    # ---- 6. the worlds -------------------------------------------------------------------------
    if worlds_file is not None and worlds_file.exists():
        worlds = simcheck.load(worlds_file)
        res["worlds"] = simcheck.summarise(worlds)
        res["worlds"]["best_method_by_pesos"] = simcheck.best_method(res["worlds"])
        plot["worlds"] = worlds
    return res, plot


# --------------------------------------------------------------------------------------------
def _claims_summary(claims: pl.DataFrame) -> dict:
    out = {"claims": claims.height, "ever_reported": int(claims["reported_ts"].is_not_null().sum())}
    for k in cl.TYPES:
        s = claims.filter(pl.col("fraud_type") == k)
        a = s["amount"].to_numpy()
        out[k] = {
            "claims": s.height,
            "reported": int(s["reported_ts"].is_not_null().sum()),
            "pesos": float(a.sum()),
            "median": float(np.median(a)),
            "p90": float(np.quantile(a, 0.9)),
            "max": float(a.max()),
        }
    return out


def _delay_block(claims: pl.DataFrame) -> tuple[dict, dict]:
    fits = delay.fit_by_type(claims, END)
    mature = claims.filter(
        pl.col("loss_ts") < pl.datetime(2025, 1, 1)
    )  # at least six months old at the end
    out: dict = {"fits": {}, "arrived_by_day": {}, "spearman_amount_delay": {}, "report_rate": {}}
    curves = {}
    for k, f in fits.items():
        planted_med, planted_sig = simcheck.PLANTED[k]
        se_mu = float(np.sqrt(f.cov[0, 0]))
        se_ls = float(np.sqrt(f.cov[1, 1]))
        out["fits"][k] = {
            "median_days": f.median_days,
            "median_ci": [float(np.exp(f.mu - 1.96 * se_mu)), float(np.exp(f.mu + 1.96 * se_mu))],
            "sigma": f.sigma,
            "sigma_ci": [
                float(f.sigma * np.exp(-1.96 * se_ls)),
                float(f.sigma * np.exp(1.96 * se_ls)),
            ],
            "planted_median": planted_med,
            "planted_sigma": planted_sig,
            "claims": f.n,
        }
        s = mature.filter((pl.col("fraud_type") == k) & pl.col("reported_ts").is_not_null())
        d = (s["reported_ts"] - s["loss_ts"]).dt.total_seconds().to_numpy() / 86400
        out["arrived_by_day"][k] = {
            str(day): {"fitted": float(f.cdf(day)), "empirical": float((d <= day).mean())}
            for day in DAYS
        }
        curves[k] = {"fit": f, "delays": np.sort(d)}
        allk = claims.filter(pl.col("fraud_type") == k)
        m = mature.filter(pl.col("fraud_type") == k)
        n_rep, n = int(m["reported_ts"].is_not_null().sum()), m.height
        out["report_rate"][k] = {
            "reported": n_rep,
            "claims": n,
            "share": n_rep / n,
            "planted": lossmodel.PLANTED_REPORT_RATE[k],
            "ci": [float(x) for x in stats.beta.interval(0.95, n_rep + 0.5, n - n_rep + 0.5)],
            "all_claims": allk.height,
        }
        seen = delay.visible(claims, END).filter(pl.col("fraud_type") == k)
        dd = (seen["reported_ts"] - seen["loss_ts"]).dt.total_seconds().to_numpy()
        rho = stats.spearmanr(seen["amount"].to_numpy(), dd)
        out["spearman_amount_delay"][k] = {"rho": float(rho.statistic), "p": float(rho.pvalue)}
    # the share of ALL eventual reports that has arrived by the purge gap of step 6, weighted by claim counts
    mature_rep = mature.filter(pl.col("reported_ts").is_not_null())
    dall = (mature_rep["reported_ts"] - mature_rep["loss_ts"]).dt.total_seconds().to_numpy() / 86400
    out["pooled_arrived_by_day"] = {str(day): float((dall <= day).mean()) for day in DAYS}
    return out, curves


def _maturity_block(claims: pl.DataFrame, reps: int, seed: int) -> tuple[dict, dict]:
    pred = delay.predictive(claims, END, reps=reps, seed=seed)
    mat = pred["maturity"]
    truth = backtest.true_ibnr(claims, END)
    n_obs = mat.reported_n.sum(axis=1)
    point = mat.ultimate_n.sum(axis=1)
    draws = n_obs[None, :] + pred["counts"].sum(axis=2)
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
    amt_obs = mat.reported_amt.sum(axis=1)
    rows = []
    for i, m in enumerate(mat.months):
        rows.append(
            {
                "month": m.isoformat(),
                "observed": float(n_obs[i]),
                "completed": float(point[i]),
                "lo": float(lo[i]),
                "hi": float(hi[i]),
                "truth": float(truth["ult_n"][i]),
                "observed_pesos": float(amt_obs[i]),
                "truth_pesos": float(truth["ult_amt"][i]),
                "completion": {k: float(mat.completion[i, j]) for j, k in enumerate(mat.types)},
            }
        )
    newest = slice(-6, None)
    out = {
        "cohorts": rows,
        "last6_observed": float(n_obs[newest].sum()),
        "last6_completed": float(point[newest].sum()),
        "last6_truth": float(truth["ult_n"][newest].sum()),
        "last6_interval": [
            float(lo[newest].sum() * 0 + np.percentile(draws[:, newest].sum(axis=1), 2.5)),
            float(np.percentile(draws[:, newest].sum(axis=1), 97.5)),
        ],
        "total_ibnr": float((point - n_obs).sum()),
        "total_ibnr_truth": float(truth["n"].sum()),
        "total_ibnr_interval": [
            float(x) for x in np.percentile(pred["counts"].sum(axis=(1, 2)), [2.5, 97.5])
        ],
    }
    return out, {"rows": rows}


def _frequency_block(claims: pl.DataFrame, expo: pl.DataFrame, reps: int, seed: int) -> dict:
    done = pipeline.complete(claims, END, reps, seed + 1)
    e = expo.sort("month")["accounts"].to_numpy()[: len(done.months)]
    out: dict = {"cohorts_in_fit": {}, "types": {}}
    for j, k in enumerate(cl.TYPES):
        m = done.mask[:, j]
        cnt = done.point[m, j]
        f = lossmodel.fit_nb(cnt, e[m])
        stat, p = lossmodel.trend_test(cnt, e[m])
        mu = f.rate * e[m]
        rates, alphas = [], []
        rng = np.random.default_rng(seed + 11 + j)
        for _ in range(min(reps, 300)):
            g = lossmodel.fit_nb(_draw(f, e[m], rng), e[m])
            rates.append(g.rate)
            alphas.append(g.alpha)
        out["types"][k] = {
            "months": int(m.sum()),
            "claims_per_1000_account_months": 1000 * f.rate,
            "rate_ci": [1000 * float(x) for x in np.percentile(rates, [2.5, 97.5])],
            "alpha": f.alpha,
            "alpha_ci": [float(x) for x in np.percentile(alphas, [2.5, 97.5])],
            "trend_lr": stat,
            "trend_p": p,
            "pearson_chi2_per_df": float(
                (((cnt - mu) ** 2) / (mu + f.alpha * mu**2)).sum() / max(len(cnt) - 2, 1)
            ),
            "monthly_rate_per_1000": [1000 * float(c / x) for c, x in zip(cnt, e[m], strict=True)],
        }
    both = done.mask.all(axis=1)
    c = (
        done.point[both] / e[both, None]
    )  # rates, so the common growth of exposure does not fake a correlation
    out["corr_ato_vs_card_testing"] = (
        float(np.corrcoef(c[:, 0], c[:, 1])[0, 1]) if both.sum() > 3 else None
    )
    return out


def _draw(fit: lossmodel.NBFit, exposure: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    mu = fit.rate * exposure
    return (
        rng.poisson(mu)
        if fit.alpha < 1e-8
        else rng.poisson(rng.gamma(1.0 / fit.alpha, fit.alpha * mu))
    )


def _finite(x: float) -> float | None:
    return float(x) if np.isfinite(x) else None


def _severity_block(claims: pl.DataFrame) -> tuple[dict, dict, str]:
    ever = claims.filter(pl.col("reported_ts").is_not_null())
    train = ever.filter(pl.col("loss_ts") < pl.datetime(2025, 1, 1))
    test = ever.filter(pl.col("loss_ts") >= pl.datetime(2025, 1, 1))
    out: dict = {"candidates": {}, "tested_on": {}}
    totals = {"lognormal": 0.0, "splice": 0.0, "splice_nonneg": 0.0}
    for k in cl.TYPES:
        a, b = (
            train.filter(pl.col("fraud_type") == k)["amount"].to_numpy(),
            test.filter(pl.col("fraud_type") == k)["amount"].to_numpy(),
        )
        out["tested_on"][k] = {"fit": len(a), "heldout": len(b)}
        for kind in totals:
            s = lossmodel.fit_severity(a, kind)
            ll = lossmodel.heldout_loglik(s, b)
            totals[kind] += len(b) * ll
            pmf = lossmodel.discretise(s)
            entry = {
                "mu": s.mu,
                "sigma": s.sigma,
                "heldout_loglik": _finite(ll),
                "model_mean": float(pmf @ (np.arange(len(pmf)) * lossmodel.GRID)),
                "fit_data_mean": float(a.mean()),
                "heldout_mean": float(b.mean()),
            }
            if kind != "lognormal":
                entry |= {
                    "threshold": s.u,
                    "xi": s.xi,
                    "beta": s.beta,
                    "endpoint": float(s.u - s.beta / s.xi) if s.xi < 0 else None,
                    "heldout_above_endpoint": int((b > s.u - s.beta / s.xi).sum())
                    if s.xi < 0
                    else 0,
                }
            out["candidates"].setdefault(k, {})[kind] = entry
    kind = max(totals, key=lambda x: totals[x])
    out["chosen"] = kind
    out["total_heldout_loglik"] = {k: _finite(v) for k, v in totals.items()}
    seen = delay.visible(claims, END)
    qq = {}
    for k in cl.TYPES:
        a = np.sort(seen.filter(pl.col("fraud_type") == k)["amount"].to_numpy())
        s = lossmodel.fit_severity(a, "lognormal")
        p = (np.arange(1, len(a) + 1) - 0.5) / len(a)
        qq[k] = (a, np.exp(s.mu + s.sigma * stats.norm.ppf(p)))
    return out, {"qq": qq}, kind


def _loss_block(
    claims: pl.DataFrame, expo: pl.DataFrame, kind: str, reps: int, seed: int
) -> tuple[dict, dict]:
    out: dict = {"severity_kind": kind, "scenarios": {}, "gross_up": {}}
    plot: dict = {}
    for sc in SCENARIOS:
        fut = lossmodel.future_exposure(expo, sc)
        f = pipeline.forecast(
            claims,
            expo,
            END,
            fut,
            reps=reps,
            seed=seed,
            severity_kind=kind,
            cells=2**19,
            keep_pmf=sc == "base",
        )
        iv = pipeline.intervals(f["reps"])
        out["scenarios"][sc] = {
            "account_months": float(fut.sum()),
            "point": f["point"],
            "interval": iv,
        }
        if sc == "base":
            plot["base_point_dist"] = lossmodel.annual_distribution(f["fit"], fut, cells=2**19)
            plot["base_predictive"] = f["predictive"]
            plot["base_reps"] = f["reps"]
            out["fit"] = {
                "frequency": {
                    k: {"claims_per_1000_account_months": 1000 * v.rate, "alpha": v.alpha}
                    for k, v in f["fit"].nb.items()
                },
                "severity": {k: {"mu": v.mu, "sigma": v.sigma} for k, v in f["fit"].sev.items()},
            }
    fut = lossmodel.future_exposure(expo, "base")
    for g in GROSS:
        f = pipeline.forecast(
            claims,
            expo,
            END,
            fut,
            reps=min(reps, 300),
            seed=seed,
            severity_kind=kind,
            gross=g,
            cells=2**19,
        )
        out["gross_up"][str(g)] = {"point": f["point"], "interval": pipeline.intervals(f["reps"])}
    alt = "lognormal" if kind != "lognormal" else "splice_nonneg"
    f = pipeline.forecast(
        claims, expo, END, fut, reps=min(reps, 200), seed=seed, severity_kind=alt, cells=2**19
    )
    out["other_severity"] = {
        "kind": alt,
        "point": f["point"],
        "interval": pipeline.intervals(f["reps"]),
    }
    return out, plot


def _out_of_time(claims: pl.DataFrame, expo: pl.DataFrame, kind: str, seed: int) -> dict:
    """Fit at 2024-12-31, forecast January to June 2025, compare with what this bank then lost."""
    months = expo.sort("month")
    future = months.filter(pl.col("month") >= simcheck.WINDOW[0])["accounts"].to_numpy()
    f = pipeline.forecast(
        claims,
        months.filter(pl.col("month") < simcheck.WINDOW[0]),
        simcheck.AS_OF,
        future,
        reps=300,
        seed=seed,
        severity_kind=kind,
        cells=2**19,
        keep_pmf=True,
    )
    realised = pipeline.realised_loss(claims, *simcheck.WINDOW)
    pred = f["predictive"]
    return {
        "realised": realised,
        "predicted": f["point"]["expected_loss"],
        "interval90": [pred.var(0.05), pred.var(0.95)],
        "pit": pred.prob_at_most(realised),
        "relative_error": f["point"]["expected_loss"] / realised - 1,
    }


# --------------------------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------------------------
def figures(res: dict, plot: dict, img: Path) -> None:
    img.mkdir(parents=True, exist_ok=True)
    _fig_maturity(res, plot, img)
    _fig_backtest(res, plot, img)
    _fig_frequency_severity(res, plot, img)
    _fig_aggregate(res, plot, img)
    if "worlds" in plot:
        _fig_calibration(res, plot, img)


def _fig_maturity(res: dict, plot: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    day = np.arange(1, 241, dtype=float)
    for k, color in (("ato", fg.BLUE), ("card_testing", fg.ORANGE)):
        c = plot["delay"][k]
        emp = np.searchsorted(c["delays"], day, side="right") / len(c["delays"])
        ax.step(day, emp, color=color, linewidth=1.2, alpha=0.9, label=f"{TYPE_NAME[k]}: observed")
        ax.plot(
            day,
            c["fit"].cdf(day),
            "--",
            color=color,
            linewidth=1.8,
            label=f"{TYPE_NAME[k]}: fitted",
        )
    ax.axvline(120, color=fg.MUTED, linewidth=1)
    ax.text(123, 0.04, "120 days:\nstep 6 purge gap", fontsize=8, color=fg.MUTED)
    ax.set_xlabel("Days from the loss to the report", color=fg.INK2)
    ax.set_ylabel("Share of eventual reports arrived", color=fg.INK2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title("How long the labels take", loc="left", fontsize=10, color=fg.INK2)

    ax = axes[1]
    rows = plot["maturity"]["rows"][3:]
    x = np.arange(len(rows))
    ax.bar(
        x, [r["observed"] for r in rows], color=fg.BLUE, alpha=0.85, label="Reported by 30 Jun 2025"
    )
    ax.errorbar(x, [r["completed"] for r in rows], yerr=[[max(r["completed"] - r["lo"], 0.0) for r in rows], [max(r["hi"] - r["completed"], 0.0) for r in rows]], fmt="o", color=fg.ORANGE, capsize=2.5, markersize=4, label="Completed with the delay model (95%)")  # fmt: skip
    ax.plot(
        x,
        [r["truth"] for r in rows],
        "_",
        color=fg.INK,
        markersize=11,
        markeredgewidth=2,
        label="What the simulator later reports",
    )
    ax.set_xticks(x[::2], [r["month"][2:7] for r in rows][::2], rotation=0)
    ax.set_ylabel("Claims lost in the month", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title(
        "The newest months look cleaner than they are", loc="left", fontsize=10, color=fg.INK2
    )
    fg._finish(fig, img / "risk_maturity.png", "Chargeback maturation: delay, and what it hides", "Left: months to report a loss, by fraud type. Right: claims per loss month as the bank sees them at the end of the data.", left=0.07, wspace=0.22)  # fmt: skip


def _fig_backtest(res: dict, plot: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    bt = plot["backtest"].filter(
        (pl.col("target") == "count") & pl.col("method").is_in(["M1", "M2", "M3", "M4"])
    )
    dates = sorted(bt["as_of"].unique().to_list())
    for m in ("M1", "M2", "M3", "M4"):
        s = bt.filter(pl.col("method") == m).sort("as_of")
        ax.plot(
            range(len(dates)),
            100 * s["rel_error"].to_numpy(),
            "o-",
            color=COLORS[m],
            label=m,
            markersize=4,
        )
    ax.axhline(0, color=fg.INK2, linewidth=1)
    ax.axvline(6.5, color=fg.MUTED, linewidth=1, linestyle=":")
    ax.text(0.2, ax.get_ylim()[1] * 0.9, "selection", fontsize=8, color=fg.MUTED)
    ax.text(6.7, ax.get_ylim()[1] * 0.9, "confirmation", fontsize=8, color=fg.MUTED)
    ax.set_xticks(range(0, len(dates), 2), [d[2:7] for d in dates][::2])
    ax.set_ylabel("Error of the estimated unreported claims (%)", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="lower center")
    ax.set_title("One bank, 13 month-ends", loc="left", fontsize=10, color=fg.INK2)

    ax = axes[1]
    if "worlds" in res:
        rows = [r for r in res["worlds"]["ibnr"] if r["method"] in ("M1", "M2", "M4")]
        w = 0.36
        for j, (target, color) in enumerate((("count", fg.BLUE), ("amount", fg.ORANGE))):
            vals = [
                next(r["coverage"] for r in rows if r["method"] == m and r["target"] == target)
                for m in ("M1", "M2", "M4")
            ]
            ax.bar(
                np.arange(3) + (j - 0.5) * w,
                vals,
                w,
                color=color,
                label=f"{'claims' if target == 'count' else 'pesos'}",
            )
        ax.axhline(0.95, color=fg.INK2, linewidth=1, linestyle="--")
        ax.text(2.5, 0.955, "nominal 95%", fontsize=8, color=fg.INK2, ha="right", va="bottom")
        ax.set_xticks(
            range(3), ["M1 pooled\nchain ladder", "M2 chain ladder\nby type", "M4 delay\nmodel"]
        )
        ax.set_ylim(0, 1.05)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_ylabel("Share of worlds where the 95% interval covers the truth", color=fg.INK2)
        ax.legend(frameon=False, fontsize=8, loc="lower right")
        ax.set_title(
            f"{res['worlds']['worlds']} independent banks, as of 31 Dec 2024",
            loc="left",
            fontsize=10,
            color=fg.INK2,
        )
    fg._finish(fig, img / "risk_backtest.png", "Do the IBNR methods work, and do their intervals?", "Error of each method against what the simulator later reveals; interval coverage across independent worlds.", left=0.07, wspace=0.22)  # fmt: skip


def _fig_frequency_severity(res: dict, plot: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    for k, color in (("ato", fg.BLUE), ("card_testing", fg.ORANGE)):
        t = res["frequency"]["types"][k]
        y = t["monthly_rate_per_1000"]
        ax.plot(
            range(len(y)), y, "o-", color=color, markersize=3.5, linewidth=1, label=TYPE_NAME[k]
        )
        ax.axhline(t["claims_per_1000_account_months"], color=color, linewidth=1.6, linestyle="--")
    ax.set_xlabel("Months from January 2024 (complete months only)", color=fg.INK2)
    ax.set_ylabel("Claims per 1,000 account-months", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_ylim(bottom=0)
    ax.set_title("Frequency: flat, and close to Poisson", loc="left", fontsize=10, color=fg.INK2)

    ax = axes[1]
    for k, color in (("ato", fg.BLUE), ("card_testing", fg.ORANGE)):
        emp, model = plot["severity"]["qq"][k]
        ax.loglog(model, emp, ".", color=color, markersize=3, alpha=0.6, label=TYPE_NAME[k])
    lim = [50, 2e5]
    ax.plot(lim, lim, color=fg.INK2, linewidth=1)
    ax.set_xlabel("Lognormal quantile (pesos)", color=fg.INK2)
    ax.set_ylabel("Observed claim (pesos)", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title(
        "Severity: lognormal fit, quantile against quantile", loc="left", fontsize=10, color=fg.INK2
    )
    fg._finish(fig, img / "risk_frequency_severity.png", "Frequency and severity of fraud claims", "Per 1,000 open accounts and month (dashed: fitted rate); claim sizes against the fitted lognormal. Both on the reported basis.", left=0.07, wspace=0.22)  # fmt: skip


def _fig_aggregate(res: dict, plot: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    d: lossmodel.Aggregate = plot["loss"]["base_point_dist"]
    lo, hi = d.var(0.0005), d.var(0.9995)
    sel = (d.x >= lo) & (d.x <= hi)
    x = d.x[sel] / 1e6
    smooth = np.convolve(d.pmf[sel], np.ones(15) / 15, mode="same") / d.h * 1e6
    ax.fill_between(x, smooth, color=fg.BLUE, alpha=0.25)
    ax.plot(x, smooth, color=fg.BLUE, linewidth=1.6)
    base = res["loss"]["scenarios"]["base"]["point"]
    for key, label, color in (
        ("expected_loss", "expected loss", fg.INK2),
        ("var_995", "99.5% VaR", fg.ORANGE),
        ("tvar_99", "99% TVaR", fg.AQUA),
    ):
        ax.axvline(base[key] / 1e6, color=color, linewidth=1.4, linestyle="--")
        ax.text(
            base[key] / 1e6,
            ax.get_ylim()[1] * (0.97 if key != "tvar_99" else 0.86),
            f" {label}\n {base[key] / 1e6:.2f}M",
            fontsize=8,
            color=color,
            va="top",
        )
    ax.set_xlabel("Next 12 months' fraud loss, million pesos", color=fg.INK2)
    ax.set_yticks([])
    ax.set_title("Loss distribution, base exposure", loc="left", fontsize=10, color=fg.INK2)

    ax = axes[1]
    labels, el, cap, el_err, cap_err = [], [], [], [[], []], [[], []]
    for sc, name in (
        ("flat", "No new\naccounts"),
        ("base", "Onboarding\ncontinues"),
        ("high", "Onboarding\n+50%"),
    ):
        s = res["loss"]["scenarios"][sc]
        labels.append(name)
        el.append(s["point"]["expected_loss"] / 1e6)
        cap.append(s["point"]["capital"] / 1e6)
        for err, key in ((el_err, "expected_loss"), (cap_err, "capital")):
            lo_, hi_ = s["interval"][key]
            err[0].append((s["point"][key] - lo_) / 1e6)
            err[1].append((hi_ - s["point"][key]) / 1e6)
    x = np.arange(3)
    ax.bar(x - 0.2, el, 0.4, yerr=el_err, color=fg.BLUE, capsize=3, label="Expected loss")
    ax.bar(
        x + 0.2,
        cap,
        0.4,
        yerr=cap_err,
        color=fg.ORANGE,
        capsize=3,
        label="Capital: 99.5% VaR minus expected loss",
    )
    ax.set_xticks(x, labels)
    ax.set_ylabel("Million pesos (95% interval)", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title("By exposure scenario", loc="left", fontsize=10, color=fg.INK2)
    fg._finish(fig, img / "risk_aggregate.png", "Fraud loss over the next twelve months, and the capital behind it", "Reported basis: claims the bank will learn of. Compound distribution by FFT; 95% bars are a bootstrap of counts and claim sizes.", left=0.07, wspace=0.22)  # fmt: skip


def _fig_calibration(res: dict, plot: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    w = res["worlds"]
    ax = axes[0]
    h = w["loss"]["pit_hist_10"]
    ax.bar(np.arange(10) / 10 + 0.05, np.array(h) / sum(h), 0.09, color=fg.BLUE)
    ax.axhline(0.1, color=fg.INK2, linewidth=1, linestyle="--")
    ax.set_xlabel("Where the realised loss falls in the forecast distribution (PIT)", color=fg.INK2)
    ax.set_ylabel("Share of worlds", color=fg.INK2)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_title(f"Flat means calibrated: 90% interval covered {100 * w['loss']['coverage_90']:.0f}% of {w['worlds']} worlds", loc="left", fontsize=10, color=fg.INK2)  # fmt: skip

    ax = axes[1]
    worlds = plot["worlds"]
    pt = np.array([x["loss"]["point"] for x in worlds]) / 1e6
    re = np.array([x["loss"]["realised"] for x in worlds]) / 1e6
    lo = np.array([x["loss"]["lo90"] for x in worlds]) / 1e6
    hi = np.array([x["loss"]["hi90"] for x in worlds]) / 1e6
    ok = (lo <= re) & (re <= hi)
    ax.errorbar(
        pt[ok],
        re[ok],
        xerr=None,
        fmt=".",
        color=fg.BLUE,
        alpha=0.7,
        label="inside the 90% interval",
    )
    ax.errorbar(pt[~ok], re[~ok], fmt="o", color=fg.ORANGE, markersize=4, label="outside")
    lim = [min(pt.min(), re.min()) * 0.95, max(pt.max(), re.max()) * 1.05]
    ax.plot(lim, lim, color=fg.INK2, linewidth=1)
    ax.set_xlabel("Forecast made on 31 Dec 2024 (million pesos)", color=fg.INK2)
    ax.set_ylabel("Loss the world went on to have, Jan-Jun 2025", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title("Forecast against outcome", loc="left", fontsize=10, color=fg.INK2)
    fg._finish(fig, img / "risk_calibration.png", f"Calibration across {w['worlds']} independent simulated banks", "Each bank is a different seed with the same assumptions; the whole pipeline is rerun in each and scored against what it later did.", left=0.07, wspace=0.22)  # fmt: skip


def write(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")
