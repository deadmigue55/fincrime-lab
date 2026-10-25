"""Does a delayed-label readout of an experiment get the right answer, across many worlds?

In one bank the report delays and the 8% to 20% of fraud that is never reported are fixed draws, so
a readout that is right *on average* can still be 30% off in that bank. The only way to say how far
off a readout typically is is to rerun the bank with different seeds. Fraud needs only the attack
half of the simulator, which takes seconds per bank, so this study uses many.

For each seed it builds the baseline bank and the bank under a rule, takes both worlds' approved
fraud events, and compares the effect on fraud loss at four weeks of the test read three ways
(true losses, reported losses, reported losses scaled by the delay model fitted on the bank's own
2024 claims). The contrast is over every customer, so no random split adds noise: what is left is
the world's own luck.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.attacks import simulate_attacks
from fincrime.bank.customers import make_customers
from fincrime.bank.policy import RulePolicy
from fincrime.bank.profiles import make_profiles
from fincrime.bank.rings import make_rings
from fincrime.experiments import outcomes, worlds
from fincrime.risk import claims as cl
from fincrime.risk import delay

LOOK_WEEKS = tuple(range(1, outcomes.WEEKS + 1))
AS_OF = outcomes.AS_OF


def _events(at, policy_name: str) -> pl.DataFrame:
    """Approved outgoing fraud events from the start of the experiment, with their report date."""
    reps = at.reports.group_by("episode_id").agg(pl.col("reported_ts").min())
    events = at.attempts.filter(
        pl.col("is_fraud")
        & (pl.col("crime_class") == "fraud")
        & (pl.col("status") == "approved")
        & (pl.col("direction") == "out")
        & (pl.col("ts") >= outcomes.T0)
    )
    # the delay clock starts at the first event inside the experiment, as in outcomes.weighted_weekly
    first = events.group_by("episode_id").agg(pl.col("ts").min().alias("first_ts"))
    return (
        events.join(reps, on="episode_id", how="left")
        .join(first, on="episode_id")
        .select("ts", "amount", "fraud_type", "reported_ts", "first_ts")
        .with_columns(arm=pl.lit(policy_name))
    )


def readout_one(args: tuple[int, str, int]) -> dict:
    """Effect of ``treatment`` on fraud loss read three ways, in the world of one seed."""
    seed, treatment, customers = args
    a = Assumptions(n_customers=customers, seed=seed)
    cust, acc = make_customers(a)
    prof = make_profiles(a, cust)
    cust_all, _, rings = make_rings(a, cust, acc)
    n = cust.height  # make_customers holds the genuine customers; mules come with the rings
    arms = {}
    base_claims = None
    for name, policy in (("baseline", RulePolicy()), (treatment, worlds.TREATMENTS[treatment])):
        at = simulate_attacks(a, cust_all, prof, rings, policy)
        arms[name] = _events(at, name)
        if name == "baseline":
            base_claims = cl.build_claims(at.attempts, at.episodes, at.reports)
    fits = delay.fit_by_type(base_claims, AS_OF)
    out = {
        "seed": seed,
        "treatment": treatment,
        "customers": customers,
        "n": n,
        "weeks": list(LOOK_WEEKS),
    }
    for label in ("true", "reported", "weighted"):
        out[label] = []
    for wk in LOOK_WEEKS:
        end = np.datetime64(outcomes.T0) + np.timedelta64(7 * wk, "D")
        tot = {}
        for name, ev in arms.items():
            ts, amt = ev["ts"].to_numpy(), ev["amount"].to_numpy()
            rep, first = ev["reported_ts"].to_numpy(), ev["first_ts"].to_numpy()
            kind = ev["fraud_type"].to_numpy()
            seen = (~np.isnat(rep)) & (rep <= end)
            age = (end - first[seen]) / np.timedelta64(1, "D")
            c = outcomes.completion(fits, kind[seen], age)
            tot[name] = {
                "true": amt[ts <= end].sum(),
                "ultimate": amt[(ts <= end) & ~np.isnat(rep)].sum(),
                "reported": amt[seen].sum(),
                "weighted": (amt[seen] / c).sum(),
            }
        truth = (tot[treatment]["true"] - tot["baseline"]["true"]) / n
        out["truth_" + str(wk)] = float(truth)
        out.setdefault("truth_ult", []).append(
            float((tot[treatment]["ultimate"] - tot["baseline"]["ultimate"]) / n)
        )
        for label in ("true", "reported", "weighted"):
            out[label].append(float((tot[treatment][label] - tot["baseline"][label]) / n))
    return out


def run(
    out: Path,
    treatment: str = "tight_all",
    seeds: range = range(1, 61),
    customers: int = 200_000,
    workers: int = 5,
) -> list[dict]:
    """Run ``readout_one`` for every seed (resumable JSON lines) and return all rows."""
    done: dict[int, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:  # a run killed mid-write leaves a cut-off last line
                continue
            if row["treatment"] == treatment and row["customers"] == customers:
                done[row["seed"]] = row
    todo = [(s, treatment, customers) for s in seeds if s not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=workers) as pool, out.open("a", encoding="utf-8") as fh:
        for row in pool.map(readout_one, todo):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            done[row["seed"]] = row
    return [done[s] for s in seeds if s in done]


def summarise(rows: list[dict]) -> dict:
    """Bias and spread of each readout, as a share of the true effect, by week of the test."""
    weeks = rows[0]["weeks"]
    res = {"worlds": len(rows), "weeks": weeks, "readouts": {}}
    truth = np.array([[r[f"truth_{w}"] for w in weeks] for r in rows])
    res["truth_mean"] = truth.mean(axis=0).tolist()
    truth_ult = np.array([r["truth_ult"] for r in rows])
    res["truth_ult_mean"] = truth_ult.mean(axis=0).tolist()
    for label in ("true", "reported", "weighted"):
        est = np.array([r[label] for r in rows])
        target = truth_ult if label == "weighted" else truth
        err = est - target
        res["readouts"][label] = {
            "mean_estimate": est.mean(axis=0).tolist(),
            "target": "effect on losses that will eventually be reported"
            if label == "weighted"
            else "effect on all losses",
            "bias_share": (err.mean(axis=0) / np.abs(target.mean(axis=0))).tolist(),
            "rmse_share": (np.sqrt((err**2).mean(axis=0)) / np.abs(target.mean(axis=0))).tolist(),
            "bias_share_vs_all_losses": (
                (est - truth).mean(axis=0) / np.abs(truth.mean(axis=0))
            ).tolist(),
            "p10": np.percentile(est, 10, axis=0).tolist(),
            "p90": np.percentile(est, 90, axis=0).tolist(),
        }
    return res
