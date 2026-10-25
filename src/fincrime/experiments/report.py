"""Turn the raw draws of ``runner`` into the numbers and figures of ``docs/experiment-lab.md``.

Nothing here simulates: it reads ``raw.npz`` and ``summary.json`` and reports every criterion of
``docs/methodology.md`` section 9 as passed or failed, next to the number behind it.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np

from fincrime import figures as fg
from fincrime.experiments import outcomes, sequential, study, worlds

matplotlib.use("Agg")
import matplotlib.ticker

TREATMENTS = tuple(worlds.TREATMENTS)
NICE = {
    "velocity3": "Velocity limit 3",
    "device500": "New-device step-up at 500",
    "tight_all": "All four rules tightened",
}
METRIC_NAME = {
    "loss": "Fraud loss",
    "claims": "Claims",
    "ato_loss": "Takeover loss",
    "ct_loss": "Card-testing loss",
    "friction": "Customers hurt",
    "stepups": "Step-ups",
}
PROCEDURES = ("naive", "pocock", "obf", "msprt")
PROCEDURE_NAME = {
    "naive": "Plain test at every look",
    "pocock": "Pocock spending",
    "obf": "O'Brien-Fleming spending",
    "msprt": "Always-valid (mixture SPRT)",
}


def load(results_dir: Path) -> tuple[dict, dict]:
    summary = json.loads((results_dir / "summary.json").read_text(encoding="utf-8"))
    raw = dict(np.load(results_dir / "raw.npz"))
    return summary, raw


def _first(raw: dict, key: str, proc: str) -> np.ndarray:
    return raw[f"{key}|first_{proc}"]


def analyse(summary: dict, raw: dict, bench: outcomes.Bench) -> dict:
    """Every number of the write-up, with the protocol's pass or fail next to it."""
    res: dict = {"n_customers": bench.n, "treatments": {}}
    base_mean = bench.arms["baseline"].y.mean(axis=0)
    for t in TREATMENTS:
        truth = bench.truth(t)
        res["treatments"][t] = {
            "truth": dict(zip(outcomes.METRICS, truth.tolist(), strict=True)),
            "relative": dict(
                zip(
                    outcomes.METRICS,
                    (truth / np.where(base_mean > 0, base_mean, 1)).tolist(),
                    strict=True,
                )
            ),
        }
    res["baseline_mean"] = dict(zip(outcomes.METRICS, base_mean.tolist(), strict=True))
    res["cum_mean"] = {
        m: bench.arms["baseline"].weekly[m].mean(axis=0).tolist() for m in ("loss", "friction")
    }

    # V1: A/A
    aa = summary["replicate:aa"]["summary"]
    reps = aa[0]["reps"]
    lo, hi = study.band(reps)
    res["v1"] = {
        "reps": reps,
        "band": [lo, hi],
        "rows": [{**r, "inside_band": lo <= r["reject"] <= hi} for r in aa],
    }
    res["v1"]["passed"] = all(r["inside_band"] for r in res["v1"]["rows"])
    res["v1"]["failures"] = [
        f"{r['estimator']} {r['metric']} {r['reject']:.3f}"
        for r in res["v1"]["rows"]
        if not r["inside_band"]
    ]
    res["v1"]["permutation"] = summary["permutation_aa"]["summary"]

    # V2: planted effects
    planted = {}
    for t in TREATMENTS:
        rows = summary[f"replicate:{t}"]["summary"]
        planted[t] = [
            {
                **r,
                "bias_ok": abs(r["bias"]) <= 3 * r["bias_mc_se"] + 1e-12,
                "coverage_ok": r["coverage"] >= 0.93,
            }
            for r in rows
        ]
    res["v2"] = planted
    res["v2_passed"] = all(
        r["bias_ok"] and r["coverage_ok"] for rows in planted.values() for r in rows
    )
    res["v2_failures"] = [
        f"{t} {r['estimator']} {r['metric']} coverage {r['coverage']:.3f} bias z {r['bias'] / r['bias_mc_se']:.1f}"
        for t, rows in planted.items()
        for r in rows
        if not (r["bias_ok"] and r["coverage_ok"])
    ]

    # variance reduction: standard error against E1, per metric (A/A draws)
    se = raw["replicate:aa|se"].mean(axis=0)  # (3, metrics)
    res["variance_reduction"] = {
        e: dict(zip(outcomes.METRICS, (1 - (se[i] / se[0]) ** 2).tolist(), strict=True))
        for i, e in enumerate(study.ESTIMATORS)
    }

    # V3: SRM
    res["v3"] = summary["srm"]["summary"]

    # V4: sequential
    seq: dict = {"looks": outcomes.WEEKS}
    for metric in ("loss", "friction"):
        seq[metric] = {}
        aa_rows = summary[f"sequential:{metric}:aa"]["summary"]
        seq[metric]["aa"] = aa_rows
        seq[metric]["aa_reps"] = len(raw[f"sequential:{metric}:aa|effect"])
        for t in TREATMENTS:
            seq[metric][t] = summary[f"sequential:{metric}:{t}"]["summary"]
            seq[metric][t + "_power_fixed"] = raw[f"sequential:{metric}:{t}|power_fixed"].tolist()
            seq[metric][t + "_mde"] = raw[f"sequential:{metric}:{t}|mde"].tolist()
        seq[metric]["aa_power_fixed"] = raw[f"sequential:{metric}:aa|power_fixed"].tolist()
    r = seq["loss"]["aa_reps"]
    lo, hi = study.band(r)
    seq["band"] = [lo, hi]
    seq["passed"] = all(
        seq[m]["aa"][i]["any_reject"] <= hi
        for m in ("loss", "friction")
        for i, row in enumerate(seq[m]["aa"])
        if row["procedure"] != "naive"
    )
    seq["naive_inflated"] = {
        m: next(row["any_reject"] for row in seq[m]["aa"] if row["procedure"] == "naive")
        for m in ("loss", "friction")
    }
    res["v4"] = seq
    res["weeks_to_80"] = {
        t: {m: _weeks_to(raw[f"sequential:{m}:{t}|power_fixed"], 0.8) for m in ("loss", "friction")}
        for t in TREATMENTS
    }

    # V5: delayed labels
    delay = {}
    for t in TREATMENTS:
        d = {k: raw[f"delay:{t}|{k}"] for k in ("true", "reported", "weighted", "truth")}
        delay[t] = {
            "weeks": [4, 8, 13, 25],
            "truth_cum": [float(d["truth"][w - 1]) for w in (4, 8, 13, 25)],
            "true": [float(d["true"][:, w - 1].mean()) for w in (4, 8, 13, 25)],
            "reported": [float(d["reported"][:, w - 1].mean()) for w in (4, 8, 13, 25)],
            "weighted": [float(d["weighted"][:, w - 1].mean()) for w in (4, 8, 13, 25)],
            "weighted_sd": [float(d["weighted"][:, w - 1].std()) for w in (4, 8, 13, 25)],
            "reported_sd": [float(d["reported"][:, w - 1].std()) for w in (4, 8, 13, 25)],
            "rates": summary[f"delay:{t}"]["rates"],
            "median_days": summary[f"delay:{t}"]["median_days"],
        }
    res["v5"] = delay

    # V6: selection and the rate trap
    res["v6"] = {t: summary[f"selection:{t}:loss"]["summary"] for t in TREATMENTS}
    res["rate_trap"] = {t: summary[f"rate_trap:{t}"]["summary"] for t in TREATMENTS}
    return res


def _weeks_to(power: np.ndarray, target: float) -> float | None:
    """First week whose fixed-horizon power reaches ``target`` (None if never in the window)."""
    hit = np.nonzero(power >= target)[0]
    return int(hit[0]) + 1 if hit.size else None


def write(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")


# --- figures ------------------------------------------------------------------------------------


def figures(res: dict, raw: dict, img: Path) -> None:
    img.mkdir(parents=True, exist_ok=True)
    _fig_validation(res, img)
    _fig_power(res, raw, img)
    _fig_peeking(res, img)
    _fig_labels(res, raw, img)
    _fig_bias(res, img)


def _fig_validation(res: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    lo, hi = res["v1"]["band"]
    ax.axhspan(lo, hi, color=fg.AQUA, alpha=0.25, linewidth=0)
    ax.axhline(0.05, color=fg.INK2, linewidth=1, linestyle="--")
    cols = {"E1": fg.BLUE, "E2": fg.ORANGE, "E3": fg.AQUA}
    metrics = ["loss", "claims", "ato_loss", "ct_loss", "friction", "stepups"]
    for j, e in enumerate(study.ESTIMATORS):
        y = [
            r["reject"]
            for m in metrics
            for r in res["v1"]["rows"]
            if r["estimator"] == e and r["metric"] == m
        ]
        ax.scatter(
            np.arange(6) + (j - 1) * 0.18,
            y,
            color=cols[e],
            s=26,
            label={"E1": "difference in means", "E2": "CUPED", "E3": "regression adjusted"}[e],
            zorder=3,
        )
    ax.set_xticks(range(6), [METRIC_NAME[m].replace(" ", "\n", 1) for m in metrics], fontsize=8)
    ax.set_ylim(0, max(0.09, hi + 0.015))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("A/A tests that reject at 5%", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=1)
    ax.set_title(
        f"No effect planted: {res['v1']['reps']:,} random splits, shaded = 99% band",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )

    ax = axes[1]
    pos = 0
    ticks, labels = [], []
    for t in TREATMENTS:
        for m in ("loss", "friction", "stepups"):
            rows = [r for r in res["v2"][t] if r["metric"] == m]
            for j, r in enumerate(rows):
                ax.scatter(
                    pos + (j - 1) * 0.2, r["coverage"], color=cols[r["estimator"]], s=26, zorder=3
                )
            ticks.append(pos)
            labels.append(f"{METRIC_NAME[m]}\n{NICE[t].split()[0]}")
            pos += 1
        pos += 0.6
    ax.axhline(0.95, color=fg.INK2, linewidth=1, linestyle="--")
    ax.axhline(0.93, color=fg.MUTED, linewidth=0.8, linestyle=":")
    ax.set_xticks(ticks, labels, fontsize=6.5, rotation=0)
    ax.set_ylim(0.88, 1.0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("95% intervals that contain the true effect", color=fg.INK2)
    ax.set_title(
        "Real effects planted: the interval holds the truth", loc="left", fontsize=10, color=fg.INK2
    )
    fg._finish(
        fig,
        img / "exp_validation.png",
        "Does the test do what it promises?",
        "A split of the same bank into two random halves, repeated many times. The true effect of each rule is known from the bank built under it.",
        left=0.07,
        wspace=0.22,
    )


def _fig_power(res: dict, raw: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    wk = np.arange(1, outcomes.WEEKS + 1)
    cols = {"velocity3": fg.BLUE, "device500": fg.ORANGE, "tight_all": fg.AQUA}
    ax = axes[0]
    for t in TREATMENTS:
        ax.plot(
            wk,
            raw[f"sequential:loss:{t}|power_fixed"],
            color=cols[t],
            linewidth=2,
            label=f"{NICE[t]} ({100 * res['treatments'][t]['relative']['loss']:.0f}%)",
        )
    ax.axhline(0.8, color=fg.INK2, linewidth=1, linestyle="--")
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Weeks of the test", color=fg.INK2)
    ax.set_ylabel("Chance the test says the rule works", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title(
        "Fraud loss: power of a 5% test by length (100,000 customers per arm)",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )

    ax = axes[1]
    for m, col, lab in (("loss", fg.BLUE, "Fraud loss"), ("friction", fg.ORANGE, "Customers hurt")):
        mde = study.mde(np.asarray(raw[f"sequential:{m}:aa|se"]).mean(axis=0))
        ax.plot(wk, 100 * mde / np.asarray(res["cum_mean"][m]), color=col, linewidth=2, label=lab)
    for t in TREATMENTS:
        r = abs(res["treatments"][t]["relative"]["loss"]) * 100
        ax.axhline(r, color=cols[t], linewidth=1, linestyle=":")
    ax.set_yscale("log")
    ax.set_xlabel("Weeks of the test", color=fg.INK2)
    ax.set_ylabel("Smallest effect seen with 80% power (% of control)", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_title(
        "What a test of this length can see (dotted: the real effects)",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )
    fg._finish(
        fig,
        img / "exp_power.png",
        "Fraud is rare, so tests of fraud rules are short on power",
        "Power and minimum detectable effect from re-randomising the same 200,000 customers; the guardrail is easy, the fraud outcome is not.",
        left=0.07,
        wspace=0.26,
    )


def _fig_peeking(res: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    wk = np.arange(1, outcomes.WEEKS + 1)
    ax = axes[0]
    ax.axhline(1.96, color=fg.INK2, linewidth=1.5, linestyle="--", label="Plain 5% test (1.96)")
    ax.plot(
        wk,
        sequential.boundaries("pocock", outcomes.WEEKS),
        color=fg.ORANGE,
        linewidth=2,
        label="Pocock spending",
    )
    ax.plot(
        wk,
        sequential.boundaries("obf", outcomes.WEEKS),
        color=fg.BLUE,
        linewidth=2,
        label="O'Brien-Fleming spending",
    )
    ax.set_ylim(1.5, 6)
    ax.set_xlabel("Week of the look", color=fg.INK2)
    ax.set_ylabel("z needed to stop and declare an effect", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_title("The line to cross, week by week", loc="left", fontsize=10, color=fg.INK2)

    ax = axes[1]
    lo, hi = res["v4"]["band"]
    x = np.arange(len(PROCEDURES))
    for j, (m, col) in enumerate((("loss", fg.BLUE), ("friction", fg.ORANGE))):
        rate = [
            next(r["any_reject"] for r in res["v4"][m]["aa"] if r["procedure"] == p)
            for p in PROCEDURES
        ]
        ax.bar(
            x + (j - 0.5) * 0.36,
            rate,
            0.34,
            color=col,
            label={"loss": "Fraud loss", "friction": "Customers hurt"}[m],
        )
    ax.axhline(0.05, color=fg.INK2, linewidth=1, linestyle="--")
    ax.axhspan(lo, hi, color=fg.AQUA, alpha=0.25, linewidth=0)
    ax.set_xticks(
        x,
        ["Plain test\nat every look", "Pocock", "O'Brien-\nFleming", "Always-valid\n(mSPRT)"],
        fontsize=8,
    )
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("A/A tests that ever reject in 25 weekly looks", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_title(
        "Looking every week with the plain test, when nothing is going on",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )
    fg._finish(
        fig,
        img / "exp_peeking.png",
        "Peeking is not free",
        "Twenty-five weekly looks. The plain test is wrong far more than 5% of the time; the spending boundaries and the always-valid test keep their promise.",
        left=0.07,
        wspace=0.24,
    )


def _fig_labels(res: dict, raw: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    wk = np.arange(1, outcomes.WEEKS + 1)
    for ax, t in zip(axes, ("velocity3", "tight_all"), strict=True):
        truth = raw[f"delay:{t}|truth"]
        ax.plot(
            wk, truth, color=fg.INK, linewidth=2.5, label="True effect (not knowable by the bank)"
        )
        for key, col, lab in (
            ("reported", fg.ORANGE, "Reported claims only"),
            ("weighted", fg.BLUE, "Reported, scaled by the delay model"),
        ):
            arr = raw[f"delay:{t}|{key}"]
            m, sd = arr.mean(axis=0), arr.std(axis=0)
            ax.plot(wk, m, color=col, linewidth=2, label=lab)
            ax.fill_between(wk, m - 1.96 * sd, m + 1.96 * sd, color=col, alpha=0.12, linewidth=0)
        ax.axhline(0, color=fg.MUTED, linewidth=0.8)
        ax.axvline(17, color=fg.MUTED, linewidth=0.8, linestyle=":")
        ax.set_xlabel("Weeks of the test", color=fg.INK2)
        ax.set_ylabel("Effect on fraud loss so far (pesos per customer)", color=fg.INK2)
        ax.set_title(NICE[t], loc="left", fontsize=10, color=fg.INK2)
    axes[0].legend(frameon=False, fontsize=8, loc="lower left")
    fg._finish(
        fig,
        img / "exp_labels.png",
        "Fraud labels arrive late, so an early readout understates the rule",
        "Dotted line: 120 days, when 95% of reports have arrived (steps 6 and 7). Bands: 95% of random splits.",
        left=0.07,
        wspace=0.22,
    )


def _fig_bias(res: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    s = res["v6"]["velocity3"]
    truth = s["truth"]["effect"]
    items = [
        ("Rolled out to the\nnewest third:\nplain gap", s["deterministic"]["naive"], fg.ORANGE),
        ("...adjusted\nfor tenure", s["deterministic"]["tenure_regression"], fg.ORANGE),
        ("New customers\nfirst (80/20):\nplain gap", s["tilted_naive"]["mean"], fg.ORANGE),
        ("...stratified", s["tilted_stratified"]["mean"], fg.AQUA),
        ("...inverse\nprobability", s["tilted_ipw"]["mean"], fg.AQUA),
        ("Randomised", s["randomised"]["mean"], fg.BLUE),
    ]
    ax.bar(range(len(items)), [v for _, v, _ in items], color=[c for _, _, c in items])
    ax.axhline(truth, color=fg.INK, linewidth=1.5, linestyle="--")
    ax.text(
        len(items) - 0.5, truth, "true effect", ha="right", va="bottom", fontsize=8, color=fg.INK
    )
    ax.set_xticks(range(len(items)), [lab for lab, _, _ in items], fontsize=7)
    ax.set_ylabel("Estimated effect on fraud loss (pesos per customer)", color=fg.INK2)
    ax.set_title(
        "A rollout by customer age is not an experiment", loc="left", fontsize=10, color=fg.INK2
    )

    ax = axes[1]
    r = res["rate_trap"]["tight_all"]
    vals = [
        r["stepups_rule"] / r["stepups_baseline"],
        r["customers_hurt_rule"] / r["customers_hurt_baseline"],
        r["failed_per_stepup_rule"] / r["failed_per_stepup_baseline"],
    ]
    ax.bar(range(3), vals, color=[fg.BLUE, fg.ORANGE, fg.AQUA])
    ax.axhline(1, color=fg.INK2, linewidth=1, linestyle="--")
    for i, v in enumerate(vals):
        ax.text(i, v + 0.05, f"{v:.2f}x", ha="center", fontsize=9, color=fg.INK2)
    ax.set_xticks(
        range(3), ["Step-ups", "Customers\nwho failed", "Failure rate\nper step-up"], fontsize=8
    )
    ax.set_ylabel("All four rules tightened, relative to baseline", color=fg.INK2)
    ax.set_title(
        "A rate whose denominator the rule inflates hides the harm",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )
    fg._finish(
        fig,
        img / "exp_bias.png",
        "Two ways to read a comparison wrongly",
        "Left: the rule's effect on fraud loss, estimated five ways against the truth. Right: friction as a rate and as a count.",
        left=0.07,
        wspace=0.24,
    )
