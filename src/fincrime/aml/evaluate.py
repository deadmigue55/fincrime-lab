"""Score every detector of docs/methodology.md section 10 against the planted laundering.

Cases are built week by week from out-of-fold scores (models) or from rule alerts (rules). An episode
counts as detected when any account that takes part in it is in an opened case during a week the
episode is active or the week after. Intervals are a cluster bootstrap over rings: a ring is the
unit that was planted, so its episodes are never split across a resample.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fincrime.aml import build, detect
from fincrime.aml import snapshots as sn

BUDGETS = (5, 10, 20, 50, 100)
PRIMARY = 20
HOURS_PER_CASE = 4.0  # named assumption: investigator time to work one case (docs/assumptions.md)
HOURS_PER_FTE_WEEK = 35.0
TYPOLOGIES = ("scatter_gather", "structuring")
RULES = ("r1", "r2", "r3", "r4")


def episodes(t: sn.Tables) -> pl.DataFrame:
    """One row per laundering episode: ring, type, active weeks and the accounts that took part."""
    e = t.truth.filter(pl.col("fraud_type").is_in(TYPOLOGIES))
    return (
        e.group_by("episode_id")
        .agg(
            pl.col("fraud_type").first(),
            pl.col("ring_id").first(),
            pl.col("ts").min().alias("t0"),
            pl.col("w").min().alias("w0"),
            pl.col("w").max().alias("w1"),
            pl.col("account_id").unique().alias("accounts"),
        )
        .sort("episode_id")
    )


Week = list[tuple[np.ndarray, np.ndarray]]


def weekly_cases(t: sn.Tables, df: pl.DataFrame, k: int) -> dict[str, list[Week]]:
    """Cases per detector and week, best first.

    Models: top ``CANDIDATES`` accounts by score, grouped. Rules: every alerted account (natural
    volume), ordered by pesos moved in the lookback. ``M0`` is the union of the four rules.
    """
    models = [c[2:] for c in df.columns if c.startswith("s_")]  # M1_0, M1_1, ... one per refit
    out: dict[str, list[Week]] = {m: [] for m in [*models, "M0", *RULES]}
    for w in range(t.weeks):
        sub = df.filter(pl.col("w") == w)
        ids = sub["account_id"].to_numpy()
        for m in models:
            s = sub[f"s_{m}"].to_numpy()
            top = np.argsort(-s)[: detect.CANDIDATES]
            out[m].append(detect.cases(t, w, k, ids[top]))
        pesos = (sub["in_amt"] + sub["out_amt"]).to_numpy()
        flags = {r: sub[r].to_numpy() for r in RULES}
        flags["M0"] = np.any([flags[r] for r in RULES], axis=0)
        for name, f in flags.items():
            by_pesos = np.argsort(-pesos[f])
            out[name].append(detect.cases(t, w, k, ids[f][by_pesos]))
    return out


def members(wk: Week, budget: int | None) -> list[np.ndarray]:
    """Accounts worked by a team that opens ``budget`` cases this week (all of them if ``None``).

    The first ``budget`` cases, limited to accounts ranked above the one that would open the next
    case, so a case does not collect low-ranked accounts the team never reached.
    """
    if budget is None:
        return [a for a, _ in wk]
    cut = wk[budget][1][0] if len(wk) > budget else None
    return [a if cut is None else a[r < cut] for a, r in wk[:budget]]


def _alerted(cases: list[Week], budget: int | None) -> list[set[int]]:
    return [{int(a) for c in members(wk, budget) for a in c} for wk in cases]


def detection(eps: pl.DataFrame, cases: list[Week], budget: int | None, weeks: int) -> dict:
    """Detected flag and delay (days from the episode's first event to the end of the alert week)."""
    alerted = _alerted(cases, budget)
    flag, delay = [], []
    start = np.datetime64(build.START)
    for accounts, w0, w1, t0 in zip(eps["accounts"], eps["w0"], eps["w1"], eps["t0"], strict=True):
        acc = {int(a) for a in accounts}
        first = next(
            (w for w in range(w0, min(w1 + 1, weeks - 1) + 1) if acc & alerted[w]),
            None,
        )
        flag.append(first is not None)
        if first is None:
            delay.append(None)
        else:
            sunday = start + np.timedelta64(7 * first + 7, "D")
            delay.append(float((sunday - np.datetime64(t0)) / np.timedelta64(1, "D")))
    return {"flag": np.array(flag), "delay": delay}


def precision(t: sn.Tables, df: pl.DataFrame, cases: list[Week], budget: int | None) -> dict:
    """Cases opened per week and how many of them have laundering in them.

    ``precision``: a member had laundering activity that week or the week before (the window in which
    ``detection`` credits an alert). ``ring_share``: a member is an account of a ring at all, which
    a dormant mule is, even in a week with nothing to see.
    """
    pos = df.filter(pl.col("label") == 1).select("account_id", "w")
    by_week: dict[int, set[int]] = {}
    for a, w in zip(pos["account_id"], pos["w"], strict=True):
        by_week.setdefault(int(w), set()).add(int(a))
    ring = {int(a) for a in t.truth["account_id"]}
    opened = hit = in_ring = 0
    for w, wk in enumerate(cases):
        for c in members(wk, budget):
            opened += 1
            acc = {int(a) for a in c}
            hit += bool((by_week.get(w, set()) | by_week.get(w - 1, set())) & acc)
            in_ring += bool(ring & acc)
    return {
        "cases_per_week": opened / t.weeks,
        "precision": hit / opened if opened else None,
        "ring_share": in_ring / opened if opened else None,
    }


def bootstrap(
    eps: pl.DataFrame, flags: dict[str, np.ndarray], reps: int = 2000, seed: int = 5
) -> dict:
    """95% intervals of recall per detector and of selected differences, resampling rings."""
    rings = eps["ring_id"].to_numpy()
    uniq = np.unique(rings)
    n = np.array([(rings == r).sum() for r in uniq], dtype=float)
    hits = {k: np.array([f[rings == r].sum() for r in uniq], dtype=float) for k, f in flags.items()}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq), (reps, len(uniq)))
    den = n[idx].sum(axis=1)
    rec = {k: hits[k][idx].sum(axis=1) / den for k in hits}
    res = {
        k: {
            "recall": float(f.mean()),
            "lo": float(np.percentile(rec[k], 2.5)),
            "hi": float(np.percentile(rec[k], 97.5)),
        }
        for k, f in flags.items()
    }
    res["_diffs"] = {}
    for a, b in (("M2", "M1"), ("M2", "M0cut"), ("M1", "M0cut")):
        if a in rec and b in rec:
            d = rec[a] - rec[b]
            res["_diffs"][f"{a}-{b}"] = {
                "diff": float(flags[a].mean() - flags[b].mean()),
                "lo": float(np.percentile(d, 2.5)),
                "hi": float(np.percentile(d, 97.5)),
            }
    return res


def forwarding(t: sn.Tables, cases: list[Week], budget: int | None) -> float:
    """Share of the mule-forwarding account-weeks (fraud proceeds) alerted that week or the next."""
    fw = t.truth.filter(pl.col("fraud_type") == "mule_forward").select("account_id", "w").unique()
    alerted = _alerted(cases, budget)
    hit = [
        int(a) in alerted[w] or (w + 1 < len(alerted) and int(a) in alerted[w + 1])
        for a, w in zip(fw["account_id"], fw["w"], strict=True)
    ]
    return float(np.mean(hit)) if hit else float("nan")


def evaluate(t: sn.Tables, df: pl.DataFrame, k: int) -> tuple[dict, dict]:
    """Everything of the section 10 tables for one lookback, and the cases behind them."""
    eps = episodes(t)
    cases = weekly_cases(t, df, k)
    # the rule bundle cut to the budget by pesos: same ordering as its natural volume
    cases["M0cut"] = cases["M0"]
    res: dict = {
        "k_weeks": k,
        "episodes": eps.height,
        "rings": int(eps["ring_id"].n_unique()),
        "by_type": {x: int((eps["fraud_type"] == x).sum()) for x in TYPOLOGIES},
        "budgets": {},
        "natural": {},
    }
    natural = {}
    for name in ("M0", *RULES):
        d = detection(eps, cases[name], None, t.weeks)
        p = precision(t, df, cases[name], None)
        natural[name] = d["flag"]
        res["natural"][name] = {
            "recall": float(d["flag"].mean()),
            "by_type": {
                x: float(d["flag"][(eps["fraud_type"] == x).to_numpy()].mean()) for x in TYPOLOGIES
            },
            "forwarding": forwarding(t, cases[name], None),
            **p,
            "hours_per_week": p["cases_per_week"] * HOURS_PER_CASE,
            "fte": p["cases_per_week"] * HOURS_PER_CASE / HOURS_PER_FTE_WEEK,
        }
    # a model is several refits (``M2_0`` .. ``M2_4``): its numbers are the mean over refits
    refits: dict[str, list[str]] = {}
    for m in cases:
        if m not in ("M0", "M0cut", *RULES):
            refits.setdefault(m.rsplit("_", 1)[0], []).append(m)
    refits["M0cut"] = ["M0cut"]
    kind = (eps["fraud_type"]).to_numpy()
    for b in BUDGETS:
        row: dict = {}
        flags = {}
        for m, runs in refits.items():
            ds = [detection(eps, cases[r], b, t.weeks) for r in runs]
            flag = np.mean([d["flag"] for d in ds], axis=0)  # share of refits detecting it
            flags[m] = flag
            recalls = [float(d["flag"].mean()) for d in ds]
            delays = [x for d in ds for x in d["delay"] if x is not None]
            ps = [precision(t, df, cases[r], b) for r in runs]
            row[m] = {
                "recall": float(flag.mean()),
                "refit_min": min(recalls),
                "refit_max": max(recalls),
                "by_type": {x: float(flag[kind == x].mean()) for x in TYPOLOGIES},
                "forwarding": float(np.mean([forwarding(t, cases[r], b) for r in runs])),
                "median_delay_days": float(np.median(delays)) if delays else None,
                "cases_per_week": float(np.mean([p["cases_per_week"] for p in ps])),
                "precision": float(
                    np.mean([p["precision"] for p in ps if p["precision"] is not None])
                ),
                "ring_share": float(
                    np.mean([p["ring_share"] for p in ps if p["ring_share"] is not None])
                ),
            }
        row["_ci"] = bootstrap(eps, flags)
        res["budgets"][b] = row
    res["natural_ci"] = bootstrap(eps, natural)
    return res, cases
