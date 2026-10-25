"""Calibration across independent simulated banks: do the intervals mean what they say?

One bank can show that a method is unbiased *in that world*; it cannot show that a 95% interval
covers 95% of the time, because one world gives one yes or no. The simulator makes the real test
possible: build 200 banks (seeds 1 to 200) from the same assumptions, run the whole pipeline in each
at the same as-of date, and score it against what that world went on to do.

Only the attacks are built (the genuine events never influence them), which reproduces the main bank
exactly for its seed (tested) and takes about nine seconds a world instead of two minutes.

At the as-of date 2024-12-31 each world yields:

* the IBNR estimate and 95% interval of every method, against the true IBNR;
* the fitted delay parameters, against the planted ones;
* a forecast of the loss of January to June 2025 (the exposure of those months is known), with its
  predictive distribution, scored by the probability integral transform (PIT) and by whether the
  90% interval covered the realised loss.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.risk import backtest, delay, pipeline
from fincrime.risk import claims as cl

AS_OF = date(2024, 12, 31)
WINDOW = (date(2025, 1, 1), date(2025, 6, 1))
PLANTED = {"ato": (2.0, 0.8), "card_testing": (30.0, 0.8)}
# the severity model the main bank selected on held-out claims (docs/fraud-loss.md); fixed for all worlds
SEVERITY = "splice_nonneg"


def build_world(seed: int, customers: int = 200_000) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Claims and monthly exposure of one simulated bank (attacks only)."""
    from fincrime.bank.assumptions import Assumptions
    from fincrime.bank.attacks import simulate_attacks
    from fincrime.bank.customers import make_customers
    from fincrime.bank.policy import RulePolicy
    from fincrime.bank.profiles import make_profiles
    from fincrime.bank.rings import make_rings

    a = Assumptions(n_customers=customers, seed=seed)
    cust, acc = make_customers(a)
    prof = make_profiles(a, cust)
    cust_all, acc_all, rings = make_rings(a, cust, acc)
    at = simulate_attacks(a, cust_all, prof, rings, RulePolicy())
    claims = cl.build_claims(at.attempts, at.episodes, at.reports)
    expo = cl.exposure(acc_all, cust_all, date(2024, 1, 1), WINDOW[1])
    return claims, expo


def one_world(args: tuple[int, int, int, int]) -> dict:
    """Everything recorded for one seed (top-level so a process pool can call it)."""
    seed, customers, reps_ibnr, reps_loss = args
    claims, expo = build_world(seed, customers)
    out: dict = {"seed": seed, "claims": claims.height}

    rows = backtest.score(claims, expo, AS_OF, reps=reps_ibnr, seed=seed)
    out["ibnr"] = [
        {k: r[k] for k in ("method", "target", "estimate", "truth", "lo", "hi", "covered")}
        for r in rows
    ]

    fits = delay.fit_by_type(claims, AS_OF)
    out["delay"] = {
        k: {
            "median": f.median_days,
            "sigma": f.sigma,
            "se_mu": float(np.sqrt(f.cov[0, 0])),
            "mu": f.mu,
        }
        for k, f in fits.items()
    }
    out["reported_share"] = {
        k: float(claims.filter(pl.col("fraud_type") == k)["reported_ts"].is_not_null().mean())
        for k in cl.TYPES
    }

    months = expo.sort("month")
    future = months.filter(pl.col("month") >= WINDOW[0])["accounts"].to_numpy()
    history = months.filter(pl.col("month") < WINDOW[0])
    f = pipeline.forecast(
        claims,
        history,
        AS_OF,
        future,
        reps=reps_loss,
        seed=seed,
        severity_kind=SEVERITY,
        cells=2**19,
        keep_pmf=True,
    )
    realised = pipeline.realised_loss(claims, *WINDOW)
    pred = f["predictive"]
    iv = pipeline.intervals(f["reps"])["expected_loss"]
    out["trend_p"] = f["trend_p"]
    out["loss"] = {
        "realised": realised,
        "point": f["point"]["expected_loss"],
        "pit": pred.prob_at_most(realised),
        "lo90": pred.var(0.05),
        "hi90": pred.var(0.95),
        "covered90": bool(pred.var(0.05) <= realised <= pred.var(0.95)),
        "el_lo": iv[0],
        "el_hi": iv[1],
    }
    return out


def run(
    out_file: Path,
    seeds: range,
    customers: int = 200_000,
    reps_ibnr: int = 200,
    reps_loss: int = 100,
    workers: int = 5,
) -> list[dict]:
    """Run the worlds in parallel, appending one JSON line per world (resumable)."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_file.exists():
        done = {
            json.loads(line)["seed"] for line in out_file.read_text().splitlines() if line.strip()
        }
    todo = [(s, customers, reps_ibnr, reps_loss) for s in seeds if s not in done]
    with ProcessPoolExecutor(max_workers=workers) as pool, out_file.open("a") as fh:
        for res in pool.map(one_world, todo):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
    return load(out_file)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarise(worlds: list[dict]) -> dict:
    """Coverage, bias and calibration over the worlds."""
    ibnr = pl.DataFrame(
        [{**r, "seed": w["seed"]} for w in worlds for r in w["ibnr"]], infer_schema_length=None
    ).with_columns(((pl.col("estimate") - pl.col("truth")) / pl.col("truth")).alias("rel_error"))
    by_method = (
        ibnr.group_by("method", "target")
        .agg(
            pl.col("rel_error").mean().alias("bias"),
            pl.col("rel_error").abs().mean().alias("mae"),
            pl.col("rel_error").median().alias("median_error"),
            pl.col("covered").mean().alias("coverage"),
            ((pl.col("hi") - pl.col("lo")) / pl.col("truth")).median().alias("rel_width"),
            pl.len().alias("worlds"),
        )
        .sort("target", "method")
    )
    d = {}
    for k, (med, sig) in PLANTED.items():
        rows = [w["delay"][k] for w in worlds if k in w["delay"]]
        mu = np.array([r["mu"] for r in rows])
        se = np.array([r["se_mu"] for r in rows])
        d[k] = {
            "median_mean": float(np.mean([r["median"] for r in rows])),
            "median_planted": med,
            "sigma_mean": float(np.mean([r["sigma"] for r in rows])),
            "sigma_planted": sig,
            "mu_covered": float(np.mean(np.abs(mu - np.log(med)) <= 1.96 * se)),
            "worlds": len(rows),
        }
    loss = pl.DataFrame([w["loss"] | {"seed": w["seed"]} for w in worlds])
    pit = loss["pit"].to_numpy()
    out = {
        "worlds": len(worlds),
        "ibnr": by_method.to_dicts(),
        "delay": d,
        "reported_share": {
            k: float(np.mean([w["reported_share"][k] for w in worlds])) for k in cl.TYPES
        },
        "loss": {
            "coverage_90": float(loss["covered90"].mean()),
            "mean_rel_bias": float((loss["point"] / loss["realised"] - 1).mean()),
            "median_rel_bias": float((loss["point"] / loss["realised"] - 1).median()),
            "pit_hist_10": np.histogram(pit, bins=10, range=(0, 1))[0].tolist(),
            "pit_mean": float(pit.mean()),
            "el_interval_covers_realised": float(
                ((loss["el_lo"] <= loss["realised"]) & (loss["realised"] <= loss["el_hi"])).mean()
            ),
            "mean_realised": float(loss["realised"].mean()),
            "mean_point": float(loss["point"].mean()),
        },
    }
    return out


def best_method(summary: dict) -> str:
    """Method with the lowest mean absolute relative error of the IBNR in pesos across worlds."""
    rows = [
        r for r in summary["ibnr"] if r["target"] == "amount" and r["method"] in backtest.METHODS
    ]
    return min(rows, key=lambda r: r["mae"])["method"]
