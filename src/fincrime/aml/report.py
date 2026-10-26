"""Figures and the case-summary file for step 9, drawn from the results JSON."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import numpy as np

from fincrime import figures as fg
from fincrime.aml import pipeline

matplotlib.use("Agg")

DETECTORS = [
    ("M0cut", "Rule bundle", fg.INK2),
    ("M1", "Trees, account features", fg.ORANGE),
    ("M2", "Trees + graph", fg.BLUE),
    ("M2n", "Trees + graph, shared-phone count dropped", fg.AQUA),
]


def _budgets(res: dict) -> list[int]:
    return [int(b) for b in res["budgets"]]


def fig_workload(res: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    b = _budgets(res)
    for ax, key, title in (
        (axes[0], "recall", "Laundering episodes detected"),
        (axes[1], "forwarding", "Fraud-proceeds mules caught (a separate typology)"),
    ):
        for m, lab, col in DETECTORS:
            y = [100 * res["budgets"][str(x)][m][key] for x in b]
            ax.plot(b, y, color=col, linewidth=2, marker="o", markersize=4, label=lab)
            if key == "recall" and m in ("M1", "M2"):
                lo = [100 * res["budgets"][str(x)]["_ci"][m]["lo"] for x in b]
                hi = [100 * res["budgets"][str(x)]["_ci"][m]["hi"] for x in b]
                ax.fill_between(b, lo, hi, color=col, alpha=0.12, linewidth=0)
        ax.set_xscale("log")
        ax.set_xticks(b, [str(x) for x in b])
        ax.set_ylim(0, 102)
        ax.set_xlabel("Cases the team can open per week", color=fg.INK2)
        ax.set_ylabel("Share (%)", color=fg.INK2)
        ax.set_title(title, loc="left", fontsize=10, color=fg.INK2)
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")
    nat = res["natural"]["M0"]
    fg._finish(
        fig,
        img / "aml_workload.png",
        "How many cases a week does it take to find the laundering?",
        f"{res['episodes']} episodes in {res['rings']} rings. The rule bundle opens {nat['cases_per_week']:.0f} "
        f"cases a week ({nat['fte']:.1f} investigators at 4 hours a case) and finds {100 * nat['recall']:.0f}%.",
        left=0.07,
        wspace=0.24,
    )


def fig_coverage(res: dict, img: Path) -> None:
    fig, ax = fg._frame(11.5, 4.4, 1)
    ax = ax[0]
    ax.grid(False)
    cols = [("R1", "natural", "r1"), ("R2", "natural", "r2"), ("R3", "natural", "r3"),
            ("R4", "natural", "r4"), ("Rule bundle", "natural", "M0"),
            ("Trees", "budget", "M1"), ("Trees + graph", "budget", "M2"),
            ("No phone\ncount", "budget", "M2n")]  # fmt: skip
    rows = [("Scatter-gather", "scatter_gather"), ("Structuring", "structuring"),
            ("Fraud-proceeds mules", "forwarding")]  # fmt: skip
    grid = np.zeros((len(rows), len(cols)))
    for j, (_, kind, key) in enumerate(cols):
        src = res["natural"][key] if kind == "natural" else res["budgets"]["10"][key]
        for i, (_, field) in enumerate(rows):
            grid[i, j] = src["forwarding"] if field == "forwarding" else src["by_type"][field]
    ax.imshow(grid, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    for i in range(len(rows)):
        for j in range(len(cols)):
            ax.text(j, i, f"{100 * grid[i, j]:.0f}%", ha="center", va="center", fontsize=10,
                    color="white" if grid[i, j] > 0.55 else fg.INK)  # fmt: skip
    ax.set_xticks(range(len(cols)), [c[0] for c in cols], fontsize=9)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=9)
    ax.axvline(4.5, color=fg.SURFACE, linewidth=4)
    ax.tick_params(length=0)
    fg._finish(
        fig,
        img / "aml_coverage.png",
        "Which detector sees which typology",
        "Rules at the volume they produce; trees at 10 cases a week. Share of each typology detected.",
        left=0.15,
    )


def fig_amlworld(res: dict, img: Path) -> None:
    fig, axes = fg._frame(11.5, 4.8, 2)
    ax = axes[0]
    names = list(res["amlworld"])
    x = np.arange(len(names))
    for off, key, col, lab in (
        (-0.27, "T", fg.ORANGE, "Account features"),
        (0.0, "TR", fg.AQUA, "+ receiver history"),
        (0.27, "G", fg.BLUE, "+ who with (graph)"),
    ):
        v = [res["amlworld"][n]["models"][key]["pr_auc"] for n in names]
        ax.bar(x + off, v, 0.26, color=col, label=lab)
        for xi, vi in zip(x + off, v, strict=True):
            ax.text(xi, vi + 0.01, f"{vi:.2f}", ha="center", fontsize=9, color=fg.INK2)
    ax.set_xticks(x, names)
    ax.set_ylabel("Precision-recall AUC, last 20% of time", color=fg.INK2)
    ax.legend(frameon=False, fontsize=8)
    ax.set_title(
        "Who an account deals with adds to the ranking",
        loc="left",
        fontsize=10,
        color=fg.INK2,
    )
    ax = axes[1]
    first = names[0]
    fl = {
        m: res["amlworld"][first]["models"][m]["flag"]["0.005"]["by_type"] for m in ("T", "TR", "G")
    }
    types = [k for k in fl["G"] if fl["G"][k] is not None]
    y = np.arange(len(types))
    for off, m, col in ((-0.27, "T", fg.ORANGE), (0.0, "TR", fg.AQUA), (0.27, "G", fg.BLUE)):
        ax.barh(y + off, [100 * fl[m][k] for k in types], 0.26, color=col)
    ax.set_yticks(y, [k.title() if k.isupper() else k for k in types], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel(
        f"Share of the scheme found at a 0.5% flag rate, {first} (%)",
        color=fg.INK2,
    )
    ax.set_title("By pattern type", loc="left", fontsize=10, color=fg.INK2)
    fg._finish(
        fig,
        img / "aml_amlworld.png",
        "IBM AMLworld: what history and structure add",
        "Every feature is the state of an account before the transaction's minute. Time split 60 / 20 / 20.",
        left=0.07,
        wspace=0.42,
        footer="IBM AMLworld (synthetic, CDLA-Sharing-1.0): aggregate results only. github.com/deadmigue55/fincrime-lab",
    )


def write(results: Path, img: Path, cases_md: Path) -> None:
    res = json.loads(results.read_text(encoding="utf-8"))
    main = res["lookback"][str(pipeline.MAIN)]
    fig_workload(main, img)
    fig_coverage(main, img)
    fig_amlworld(res, img)
    lines = ["# Example case summaries (step 9)", "",
             "Written from the weekly tables only: no truth column is used in the text. The three "
             "cases were *chosen* with the truth (one of each kind), so they illustrate and do not "
             "sample. Banco Sintetico is synthetic.", ""]  # fmt: skip
    for label, text in res["examples"].items():
        lines += [f"## {label.capitalize()}", "", text, ""]
    cases_md.write_text("\n".join(lines), encoding="utf-8")
