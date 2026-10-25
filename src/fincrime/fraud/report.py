"""Turn the saved scores of ``fraud evaluate`` into results: headline metrics with account-level
intervals, the incumbent comparison, cost, fairness, drift, and the figures.

Everything here reads ``scored.parquet`` (January to June 2025) and ``validation.parquet``; the
test block is April to June. Run it as often as needed: nothing is refitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import numpy as np
import polars as pl

from fincrime import figures as fg
from fincrime.fraud import metrics
from fincrime.fraud.evaluate import TEST_START

REVIEW, FRICTION = 15.0, 40.0  # MXN per flagged event; per flagged genuine event (assumptions)
FAIR_SHARE = 0.005
GROUPS = ("grp_age_band", "grp_income_band", "grp_sex", "grp_state")
PSI_FEATURES = ("amount", "n_att_24h", "out_amt_24h", "n_prior", "account_age_days")
GRID = (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.04)


def _codes(x) -> tuple[np.ndarray, int]:
    _, inv = np.unique(np.asarray(x), return_inverse=True)
    return inv, int(inv.max()) + 1


def cost_curve(y, amount, score, review=REVIEW, friction=FRICTION):
    """Expected cost of flagging the top ``k`` events, for every k (pesos)."""
    order = np.argsort(-score, kind="stable")
    y, amount = np.asarray(y, dtype=bool)[order], np.asarray(amount)[order]
    k = np.arange(1, len(y) + 1)
    caught = np.cumsum(amount * y)
    loss = float((amount * y).sum())
    return review * k + friction * (k - np.cumsum(y)) + (loss - caught), loss


def best_share(y, amount, score, scale=1.0, max_share=0.05):
    """Flag share that minimises expected cost, with the unit costs multiplied by ``scale``."""
    cost, _ = cost_curve(y, amount, score, REVIEW * scale, FRICTION * scale)
    top = int(max_share * len(y))
    return (int(np.argmin(cost[:top])) + 1) / len(y)


def account_costs(acc, y, amount, flagged, review=REVIEW, friction=FRICTION):
    """Cost of each account's events under a flag decision: bootstrap-ready."""
    event = np.where(flagged, review + friction * (~y), np.where(y, amount, 0.0))
    return np.bincount(acc, weights=event, minlength=acc.max() + 1)


def boot_total(per_account: np.ndarray, reps=300, seed=0):
    rng = np.random.default_rng(seed)
    n = len(per_account)
    sums = [per_account[rng.integers(0, n, n)].sum() for _ in range(reps)]
    return float(per_account.sum()), *np.percentile(sums, [2.5, 97.5])


def group_table(df, flagged, y, reps=300, seed=0, groups=GROUPS):
    """False-alarm rate among genuine events and recall, by group, with account-level intervals."""
    acc, n_acc = _codes(df["account_id"].to_numpy())
    rows, cols = [], []
    for g in groups:
        vals = df[g].to_numpy()
        for v in sorted({x for x in vals if x is not None}):
            m = vals == v
            cols.append(
                (
                    g,
                    v,
                    np.bincount(acc, weights=m & ~y, minlength=n_acc),
                    np.bincount(acc, weights=m & ~y & flagged, minlength=n_acc),
                    np.bincount(acc, weights=m & y, minlength=n_acc),
                    np.bincount(acc, weights=m & y & flagged, minlength=n_acc),
                )
            )
    mat = np.array([c[2:] for c in cols])  # groups x 4 x accounts
    rng = np.random.default_rng(seed)
    draws = np.empty((reps, len(cols), 2))
    for r in range(reps):
        w = np.bincount(rng.integers(0, n_acc, n_acc), minlength=n_acc).astype(float)
        s = mat @ w  # groups x 4
        with np.errstate(invalid="ignore", divide="ignore"):
            draws[r, :, 0], draws[r, :, 1] = s[:, 1] / s[:, 0], s[:, 3] / s[:, 2]
    for i, (g, v, a, b, c, d) in enumerate(cols):
        fpr = b.sum() / a.sum() if a.sum() else np.nan
        rec = d.sum() / c.sum() if c.sum() else np.nan
        lo_f, hi_f = np.nanpercentile(draws[:, i, 0], [2.5, 97.5]) if a.sum() else (np.nan,) * 2
        lo_r, hi_r = np.nanpercentile(draws[:, i, 1], [2.5, 97.5]) if c.sum() else (np.nan,) * 2
        rows.append(
            {
                "group": g.removeprefix("grp_"),
                "value": str(v),
                "genuine": int(a.sum()),
                "fraud": int(c.sum()),
                "fraud_accounts": int((c > 0).sum()),
                "false_alarm_rate": fpr,
                "fa_lo": lo_f,
                "fa_hi": hi_f,
                "recall": rec,
                "rec_lo": lo_r,
                "rec_hi": hi_r,
            }
        )
    return rows


def psi(reference, weights, current, bins=10):
    """Population stability index of ``current`` against a weighted reference sample."""
    ref, cur = np.asarray(reference, float), np.asarray(current, float)
    ref_ok, cur = ~np.isnan(ref), cur[~np.isnan(cur)]
    ref, weights = ref[ref_ok], np.asarray(weights)[ref_ok]
    order = np.argsort(ref)
    cum = np.cumsum(weights[order]) / weights.sum()
    edges = np.unique(ref[order][np.searchsorted(cum, np.linspace(0, 1, bins + 1)[1:-1])])
    p = np.bincount(np.searchsorted(edges, ref), weights=weights, minlength=len(edges) + 1)
    q = np.bincount(np.searchsorted(edges, cur), minlength=len(edges) + 1).astype(float)
    p, q = np.clip(p / p.sum(), 1e-6, None), np.clip(q / q.sum(), 1e-6, None)
    return float(np.sum((q - p) * np.log(q / p)))


def build(results: Path, img: Path) -> dict:
    """Compute every result, draw the figures and save ``results.json``."""
    scored = pl.read_parquet(results / "scored.parquet")
    val = pl.read_parquet(results / "validation.parquet")
    ref = pl.read_parquet(results / "train_reference.parquet")
    summary = json.loads((results / "selection.json").read_text(encoding="utf-8"))
    test = scored.filter(pl.col("event_day") >= TEST_START)
    y, amount = test["y_true"].to_numpy(), test["amount"].to_numpy()
    acc, _ = _codes(test["account_id"].to_numpy())
    flags = (test["decision"] != "approve").to_numpy()
    out: dict = {"summary": summary, "test_rows": len(test), "test_fraud": int(y.sum())}
    out["test_fraud_accounts"] = len(set(test.filter(pl.col("y_true"))["account_id"].to_list()))

    # headline: the chosen model with observed labels, and the all-knowing diagnostic
    head = {}
    for name in ("final", "oracle"):
        s = test[f"score_{name}"].to_numpy()
        ci = metrics.cluster_bootstrap(y, s, acc, shares=GRID)
        head[name] = {
            "share": {q: (metrics.recall_at_share(y, s, (q,))[q], *ci[("share", q)]) for q in GRID},
            "fpr": {f: (r, *ci[("fpr", f)]) for f, r in metrics.recall_at_fpr(y, s).items()},
            "ap": metrics.average_precision(y, s),
            "auc": metrics.roc_auc(y, s),
            "brier": metrics.brier(y, s),
            "clusters": ci["clusters"],
        }
    out["headline"] = head
    out["incumbent"] = metrics.compare_with_flags(y, test["score_final"].to_numpy(), flags, acc)
    out["incumbent_oracle"] = metrics.compare_with_flags(
        y, test["score_oracle"].to_numpy(), flags, acc
    )
    # the same comparison by kind of fraud, and in pesos rather than events
    sc = test["score_final"].to_numpy()
    at_share = np.zeros(len(sc), dtype=bool)
    at_share[np.argsort(-sc, kind="stable")[: int(flags.mean() * len(sc))]] = True
    stopped = (test["status"] != "approved").to_numpy()
    et = test["event_type"].to_numpy()
    by_type = {}
    for label, m in (
        ("all", np.ones(len(y), dtype=bool)),
        ("spei_out", et == "spei_out"),
        ("card_online", et == "card_online"),
    ):
        f = y & m
        by_type[label] = {
            "events": int(f.sum()),
            "median_amount": float(np.median(amount[f])),
            "pesos": float(amount[f].sum()),
            "model_events": float(at_share[f].mean()),
            "model_pesos": float(amount[f & at_share].sum() / amount[f].sum()),
            "incumbent_flagged_events": float(flags[f].mean()),
            "incumbent_flagged_pesos": float(amount[f & flags].sum() / amount[f].sum()),
            "incumbent_stopped_events": float(stopped[f].mean()),
            "incumbent_stopped_pesos": float(amount[f & stopped].sum() / amount[f].sum()),
        }
    out["by_type"] = by_type
    out["pesos_curve"] = {}
    for name in ("final", "oracle"):
        order = np.argsort(-test[f"score_{name}"].to_numpy(), kind="stable")
        got = np.cumsum((amount * y)[order]) / (amount * y).sum()
        out["pesos_curve"][name] = {q: float(got[max(int(q * len(y)), 1) - 1]) for q in GRID}

    # cost: the flag share comes from validation and is then held fixed on the test block
    yv, av, sv = val["y_true"].to_numpy(), val["amount"].to_numpy(), val["score"].to_numpy()
    cost = {"units": {"review": REVIEW, "friction": FRICTION}}
    s = test["score_final"].to_numpy()
    for scale in (0.5, 1.0, 2.0):
        q = best_share(yv, av, sv, scale)
        k = max(round(q * len(s)), 1)
        flagged = np.zeros(len(s), dtype=bool)
        flagged[np.argsort(-s, kind="stable")[:k]] = True
        per_model = account_costs(acc, y, amount, flagged, REVIEW * scale, FRICTION * scale)
        per_inc = account_costs(acc, y, amount, flags, REVIEW * scale, FRICTION * scale)
        per_none = account_costs(
            acc, y, amount, np.zeros(len(s), dtype=bool), REVIEW * scale, FRICTION * scale
        )
        cost[f"x{scale}"] = {
            "share": q,
            "model": boot_total(per_model),
            "incumbent": boot_total(per_inc),
            "no_flags": boot_total(per_none),
            "saving_vs_incumbent": boot_total(per_inc - per_model),
        }
    out["cost"] = cost
    curve, loss = cost_curve(y, amount, s)
    out["_curve"] = None  # kept out of the JSON; used for the figure below

    # fairness at a shared threshold: the top 0.5% of test scores
    k = int(FAIR_SHARE * len(s))
    shared = np.zeros(len(s), dtype=bool)
    shared[np.argsort(-s, kind="stable")[:k]] = True
    out["fairness"] = group_table(test, shared, y)

    # drift: January to June, threshold fixed at the January 0.5% score quantile
    t0 = np.quantile(
        scored.filter(pl.col("event_day") < pl.date(2025, 2, 1))["score_final"].to_numpy(),
        1 - FAIR_SHARE,
    )
    months = []
    jan = scored.filter(pl.col("event_day") < pl.date(2025, 2, 1))["score_final"].to_numpy()
    for m in range(1, 7):
        d = scored.filter(pl.col("event_day").dt.month() == m)
        ym, sm = d["y_true"].to_numpy(), d["score_final"].to_numpy()
        row = {
            "month": f"2025-{m:02d}",
            "events": len(d),
            "fraud": int(ym.sum()),
            "flag_rate_fixed": float((sm >= t0).mean()),
            "recall_fixed": float((sm[ym] >= t0).mean()) if ym.any() else None,
            "recall_own": metrics.recall_at_share(ym, sm, (FAIR_SHARE,))[FAIR_SHARE],
            "ap": metrics.average_precision(ym, sm),
            "psi_score": psi(jan, np.ones(len(jan)), sm),
        }
        for f in PSI_FEATURES:
            row[f"psi_{f}"] = psi(ref[f].to_numpy(), ref["weight"].to_numpy(), d[f].to_numpy())
        months.append(row)
    out["drift"] = months

    out["_series"] = None
    _figures(out, test, y, amount, s, flags, curve, loss, img)
    out.pop("_curve"), out.pop("_series")
    (results / "results.json").write_text(
        json.dumps(out, indent=2, default=float), encoding="utf-8"
    )
    return out


def _figures(out, test, y, amount, s, flags, curve, loss, img: Path) -> None:
    img.mkdir(parents=True, exist_ok=True)
    # 1. recall against review workload, in events and in pesos
    fig, axes = fg._frame(11, 4.8, 2)
    inc = out["incumbent"]
    for ax, unit in zip(axes, ("events", "pesos"), strict=True):
        for name, color, style, label in (
            ("final", fg.BLUE, "-", "Model, trained on reported labels"),
            ("oracle", fg.AQUA, "--", "Same model, true labels (diagnostic)"),
        ):
            qs = list(out["headline"][name]["share"])
            if unit == "events":
                h = out["headline"][name]["share"]
                ys = [h[q][0] for q in qs]
                if name == "final":
                    ax.fill_between(
                        qs,
                        [h[q][1] for q in qs],
                        [h[q][2] for q in qs],
                        color=color,
                        alpha=0.18,
                        linewidth=0,
                    )
            else:
                ys = [out["pesos_curve"][name][q] for q in qs]
            ax.plot(qs, ys, style, color=color, linewidth=2, label=label)
        t = out["by_type"]["all"]
        if unit == "events":
            ax.errorbar(inc["flag_share"][0], inc["rule_recall"][0], yerr=[[inc["rule_recall"][0] - inc["rule_recall"][1]], [inc["rule_recall"][2] - inc["rule_recall"][0]]], fmt="o", color=fg.ORANGE, markersize=8, capsize=3, label="Incumbent rules (one point)")  # fmt: skip
        else:
            ax.plot(
                [inc["flag_share"][0]],
                [t["incumbent_flagged_pesos"]],
                "o",
                color=fg.ORANGE,
                markersize=8,
                label="Incumbent rules (one point)",
            )
        ax.set_xscale("log")
        ax.set_ylim(0.3, 1.005)
        ax.set_xlabel("Share of all attempts sent to review", color=fg.INK2)
        ax.set_title(
            "Fraud events found" if unit == "events" else "Fraud pesos found",
            loc="left",
            fontsize=10,
            color=fg.INK2,
        )
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=2))
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    axes[1].legend(frameon=False, fontsize=8.5, loc="lower left")
    fg._finish(
        fig, img / "fraud_recall.png", "Recall at a fixed review budget",
        f"Test, Apr-Jun 2025: {out['test_rows']:,} attempts, {out['test_fraud']:,} fraud events, {out['test_fraud_accounts']} accounts. Band: 95% by account.",
        left=0.08, wspace=0.22,
    )  # fmt: skip

    # 2. expected cost
    fig, (ax,) = fg._frame(8.4, 4.6)
    k = np.arange(1, len(curve) + 1)
    sel = k <= int(0.05 * len(k))
    ax.plot(
        k[sel] / len(k),
        curve[sel] / 1e6,
        color=fg.BLUE,
        linewidth=2,
        label="Model, flagging the top share",
    )
    ax.axhline(
        loss / 1e6,
        color=fg.MUTED,
        linewidth=1.2,
        linestyle=":",
        label="No flags (all fraud succeeds)",
    )
    c = out["cost"]["x1.0"]
    ax.scatter(
        [c["share"]],
        [c["model"][0] / 1e6],
        color=fg.BLUE,
        zorder=5,
        s=50,
        label="Chosen on validation, held fixed",
    )
    ax.scatter([out["incumbent"]["flag_share"][0]], [c["incumbent"][0] / 1e6], color=fg.ORANGE, zorder=5, s=50, label="Incumbent rules")  # fmt: skip
    ax.set_xscale("log")
    ax.set_xlim(2e-5, 0.05)
    ax.set_ylim(0, 8)
    ax.set_xlabel("Share of all attempts sent to review", color=fg.INK2)
    ax.set_ylabel("Expected cost, million pesos", color=fg.INK2)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{100 * v:g}%"))
    ax.legend(frameon=False, fontsize=9)
    fg._finish(
        fig, img / "fraud_cost.png", "What each review budget costs",
        f"Fraud pesos missed + MXN {REVIEW:.0f} per review + MXN {FRICTION:.0f} per genuine customer disturbed (assumptions). Test block.",
        left=0.1,
    )  # fmt: skip

    # 3. drift
    fig, axes = fg._frame(11, 4.4, 2)
    mo = [d["month"][5:] for d in out["drift"]]
    axes[0].plot(
        mo,
        [d["recall_fixed"] for d in out["drift"]],
        "o-",
        color=fg.BLUE,
        label="threshold fixed in January",
    )
    axes[0].plot(
        mo,
        [d["recall_own"] for d in out["drift"]],
        "o--",
        color=fg.AQUA,
        label="each month at 0.5% flagged",
    )
    axes[0].set_ylim(0.95, 1.003)
    axes[0].set_title("Share of fraud found", loc="left", fontsize=10, color=fg.INK2)
    axes[0].legend(frameon=False, fontsize=8.5, loc="lower left")
    axes[1].plot(mo, [100 * d["flag_rate_fixed"] for d in out["drift"]], "o-", color=fg.ORANGE)
    axes[1].set_title(
        "Share of attempts flagged by the January threshold, %",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )
    axes[1].set_ylim(0.3, 0.55)
    for ax in axes:
        ax.set_xlabel("Month of 2025", color=fg.INK2)
    fg._finish(fig, img / "fraud_drift.png", "Does the model hold up as months pass?", "Trained on events to November 2024; the test block starts in April.", left=0.06)  # fmt: skip

    # 4. fairness
    rows = [
        r
        for r in out["fairness"]
        if r["group"] in ("age_band", "income_band") and r["genuine"] > 1000
    ]
    fig, axes = fg._frame(11, 4.6, 2)
    for ax, key, title in (
        (axes[0], "false_alarm_rate", "Genuine events flagged (false-alarm rate)"),
        (axes[1], "recall", "Fraud events found"),
    ):
        lo, hi = ("fa_lo", "fa_hi") if key == "false_alarm_rate" else ("rec_lo", "rec_hi")
        ys = np.arange(len(rows))[::-1]
        ok = [r for r in rows if not np.isnan(r[key])]
        for yy, r in zip(ys, rows, strict=True):
            if np.isnan(r[key]):
                continue
            pct = 100 if key == "false_alarm_rate" else 1
            ax.errorbar(r[key] * pct, yy, xerr=[[(r[key] - r[lo]) * pct], [(r[hi] - r[key]) * pct]], fmt="o", color=fg.BLUE, capsize=3)  # fmt: skip
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
        ax.set_yticks(ys, [f"{r['group'].split('_')[0]}: {r['value']}" for r in rows], fontsize=8.5)
        ax.set_title(
            title + (", %" if key == "false_alarm_rate" else ""),
            loc="left",
            fontsize=10,
            color=fg.INK2,
        )
        del ok
    fg._finish(fig, img / "fraud_fairness.png", "Who bears the false alarms?", "Top 0.5% of test scores flagged. Bars: 95% interval by account. Wide bars mean too few frauds to conclude.", left=0.14, wspace=0.55)  # fmt: skip
