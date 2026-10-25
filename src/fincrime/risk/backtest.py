"""The four IBNR methods side by side, scored against what the simulator later reveals.

At each month-end the bank estimates how many claims (and how many pesos) have happened but are not
yet reported. Because the simulator keeps every report, including those after the end of the data,
the true IBNR of that date is known and every method can be scored: bias, error, the width of the
95% interval and whether it covered the truth. At a real bank this would be a retrospective study
that takes a year; here it takes a minute.

Method ids and the rule for choosing one are fixed in docs/methodology.md section 8, before any of
this was run: choose on the *selection* dates, report the *confirmation* dates once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import polars as pl

from fincrime.risk import delay, triangle
from fincrime.risk.claims import TYPES

METHODS = ("M1", "M2", "M3", "M4")
# Added after the selection block had been read, so it cannot be chosen: M4 with the pesos of the
# unreported claims = their expected count x the mean size of the reported claims of the type,
# instead of scaling the cohort's own (lumpy) reported pesos. Counted in "variants tried".
EXPLORATORY = ("M4s",)
FIRST_SCORED = date(2024, 4, 1)  # earlier cohorts have under 15 claims
SELECTION = [
    date(2024, m, d) for m, d in ((6, 30), (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31))
]
CONFIRMATION = [date(2025, m, d) for m, d in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30))]
COMPLETE = 1.02  # a cohort counts as complete for the BF prior when its CDF is at most this
PRIOR_COHORTS = 6


@dataclass
class Estimate:
    """One method at one as-of date: IBNR by cohort (pooled over types) and predictive draws."""

    ibnr_n: np.ndarray
    ibnr_amt: np.ndarray
    draws_n: np.ndarray | None  # (reps, cohorts)
    draws_amt: np.ndarray | None


def _n_cohorts(as_of: date, first: date = date(2024, 1, 1)) -> int:
    return (as_of.year - first.year) * 12 + as_of.month - first.month + 1


def true_ibnr(
    claims: pl.DataFrame, as_of: date, first: date = date(2024, 1, 1)
) -> dict[str, np.ndarray]:
    """What the file later reveals: per cohort, claims lost by ``as_of`` and reported after it."""
    end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, 999999)
    n = _n_cohorts(as_of, first)
    idx = (
        (pl.col("loss_ts").dt.year() - first.year) * 12 + pl.col("loss_ts").dt.month() - first.month
    )
    late = claims.filter((pl.col("reported_ts") > end) & (pl.col("loss_ts") <= end)).with_columns(
        idx.alias("i")
    )
    ever = claims.filter(
        pl.col("reported_ts").is_not_null() & (pl.col("loss_ts") <= end)
    ).with_columns(idx.alias("i"))
    out = {"n": np.zeros(n), "amt": np.zeros(n), "ult_n": np.zeros(n), "ult_amt": np.zeros(n)}
    for key, df in (("", late), ("ult_", ever)):
        g = df.group_by("i").agg(pl.len().alias("n"), pl.col("amount").sum().alias("a"))
        for i, c, a in g.iter_rows():
            if 0 <= i < n:
                out[key + "n"][i], out[key + "amt"][i] = c, a
    return out


def _chain(claims: pl.DataFrame, as_of: date, fraud_type: str | None, reps: int, seed: int) -> dict:
    out = {}
    for value in ("count", "amount"):
        t = triangle.build_triangle(claims, as_of, value, fraud_type)
        cl = triangle.chain_ladder(t.cum)
        draws = triangle.odp_bootstrap(t.incr, reps, seed, tail=cl.tail) if reps else None
        out[value] = (cl, draws, t)
    return out


def estimate(
    claims: pl.DataFrame, exposure: pl.DataFrame, as_of: date, reps: int = 1000, seed: int = 0
) -> dict[str, Estimate]:
    """All four methods at ``as_of``. ``exposure`` has ``month`` and ``accounts``."""
    n = _n_cohorts(as_of)
    out: dict[str, Estimate] = {}

    pooled = _chain(claims, as_of, None, reps, seed)
    out["M1"] = Estimate(
        pooled["count"][0].ibnr, pooled["amount"][0].ibnr, pooled["count"][1], pooled["amount"][1]
    )

    by_type = {k: _chain(claims, as_of, k, reps, seed + 1 + j) for j, k in enumerate(TYPES)}
    sum_draws = lambda v: None if not reps else sum(by_type[k][v][1] for k in TYPES)  # noqa: E731
    out["M2"] = Estimate(
        sum(by_type[k]["count"][0].ibnr for k in TYPES),
        sum(by_type[k]["amount"][0].ibnr for k in TYPES),
        sum_draws("count"),
        sum_draws("amount"),
    )

    # M3: Bornhuetter-Ferguson with a prior from the claim rate of the last complete cohorts (M2)
    expo = exposure.sort("month")["accounts"].to_numpy()[:n]
    bf_n, bf_a = np.zeros(n), np.zeros(n)
    for k in TYPES:
        for value, acc in (("count", bf_n), ("amount", bf_a)):
            cl = by_type[k][value][0]
            done = np.flatnonzero(cl.cdf <= COMPLETE)[-PRIOR_COHORTS:]
            if len(done) < 3:
                done = np.arange(min(3, n))  # young book: fall back on the three oldest cohorts
            rate = cl.ultimate[done].sum() / expo[done].sum()
            bf = triangle.bornhuetter_ferguson(cl.latest, cl.cdf, rate * expo)
            acc += bf - cl.latest
    out["M3"] = Estimate(bf_n, bf_a, None, None)

    pred = delay.predictive(claims, as_of, reps=max(reps, 1), seed=seed + 7) if reps else None
    mat, _ = delay.maturity(claims, as_of)
    d_n = pred["counts"].sum(axis=2) if pred else None
    d_a = pred["amounts"].sum(axis=2) if pred else None
    out["M4"] = Estimate(mat.ibnr_n.sum(axis=1), mat.ibnr_amt.sum(axis=1), d_n, d_a)
    seen = delay.visible(claims, as_of)
    mean_size = np.array(
        [seen.filter(pl.col("fraud_type") == k)["amount"].mean() for k in mat.types]
    )
    out["M4s"] = Estimate(mat.ibnr_n.sum(axis=1), (mat.ibnr_n * mean_size).sum(axis=1), d_n, d_a)
    return out


def score(
    claims: pl.DataFrame, exposure: pl.DataFrame, as_of: date, reps: int = 1000, seed: int = 0
) -> list[dict]:
    """One row per method and target: the estimate, its interval, the truth and the cohort errors."""
    n = _n_cohorts(as_of)
    i0 = (FIRST_SCORED.year - 2024) * 12 + FIRST_SCORED.month - 1
    truth = true_ibnr(claims, as_of)
    est = estimate(claims, exposure, as_of, reps, seed)
    rows = []
    for m, e in est.items():
        for target, hat, draws, true, ult in (
            ("count", e.ibnr_n, e.draws_n, truth["n"], truth["ult_n"]),
            ("amount", e.ibnr_amt, e.draws_amt, truth["amt"], truth["ult_amt"]),
        ):
            tot_hat, tot_true = float(hat[i0:n].sum()), float(true[i0:n].sum())
            row = {
                "as_of": as_of.isoformat(),
                "method": m,
                "target": target,
                "estimate": tot_hat,
                "truth": tot_true,
                "rel_error": (tot_hat - tot_true) / tot_true if tot_true > 0 else float("nan"),
                "lo": None,
                "hi": None,
                "covered": None,
            }
            if draws is not None:
                lo, hi = np.percentile(draws[:, i0:n].sum(axis=1), [2.5, 97.5])
                row |= {"lo": float(lo), "hi": float(hi), "covered": bool(lo <= tot_true <= hi)}
            # cohort level: error of the completed (reported + IBNR) cohort against its true ultimate
            reported = ult - true
            done = (reported + hat)[i0:n]
            row["cohort_mape"] = float(
                np.mean(np.abs(done - ult[i0:n]) / np.maximum(ult[i0:n], 1e-9))
            )
            rows.append(row)
    return rows


def run(
    claims: pl.DataFrame, exposure: pl.DataFrame, reps: int = 1000, seed: int = 0
) -> pl.DataFrame:
    """Every as-of date of both blocks."""
    rows = []
    for block, dates in (("selection", SELECTION), ("confirmation", CONFIRMATION)):
        for d in dates:
            rows += [r | {"block": block} for r in score(claims, exposure, d, reps, seed)]
    return pl.DataFrame(rows, infer_schema_length=None)


def summarise(res: pl.DataFrame) -> pl.DataFrame:
    """Per block, method and target: bias, mean absolute error, interval width and coverage."""
    return (
        res.group_by("block", "method", "target")
        .agg(
            pl.col("rel_error").mean().alias("bias"),
            pl.col("rel_error").abs().mean().alias("mae"),
            pl.col("cohort_mape").mean().alias("cohort_mape"),
            ((pl.col("hi") - pl.col("lo")) / pl.col("truth")).mean().alias("rel_width"),
            pl.col("covered").mean().alias("coverage"),
            pl.len().alias("dates"),
        )
        .sort("block", "target", "method")
    )


def choose(summary: pl.DataFrame) -> str:
    """The pre-declared rule: lowest MAE of the IBNR in pesos on the selection block.

    A method within one point of the best goes to the simpler one (M1 < M2 < M4 < M3).
    """
    sel = summary.filter((pl.col("block") == "selection") & (pl.col("target") == "amount"))
    sel = sel.filter(pl.col("method").is_in(METHODS))
    best = sel["mae"].min()
    order = {"M1": 0, "M2": 1, "M4": 2, "M3": 3}
    close = [
        r["method"]
        for r in sel.iter_rows(named=True)
        if r["method"] in order and r["mae"] <= best + 0.01
    ]
    return min(close, key=order.__getitem__)
