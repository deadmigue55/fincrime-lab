"""Turn the benchmark CSV into figures and a short Markdown report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fincrime.figures import BLUE, FOOTER, GRID, INK, INK2, MUTED, SURFACE

LABELS = {
    "scan_aggregate": "Scan and aggregate",
    "high_cardinality_groupby": "Group by account (top 10)",
    "distinct_counts": "Distinct accounts",
    "edge_table": "Build the transfer-edge table",
    "join_dimension": "Join to the accounts table",
    "rolling_24h_velocity": "Rolling 24-hour velocity",
    "one_day_slice": "Read one day",
}


def read_results(path: Path) -> list[dict]:
    """Rows of the benchmark CSV with numbers parsed."""
    rows = []
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                {
                    "rows": int(r["rows"]),
                    "query": r["query"],
                    "status": r["status"],
                    "cold_s": float(r["cold_s"]) if r["cold_s"] else None,
                    "median_s": float(r["median_s"]) if r["median_s"] else None,
                    "peak_mb": float(r["peak_mb"]) if r["peak_mb"] else None,
                    "detail": r["detail"],
                }
            )
    return rows


def fig_scaling(rows: list[dict], path: Path) -> None:
    """One small panel per query: seconds against rows, with a linear-scaling reference."""
    queries = list(LABELS)
    fig, axes = plt.subplots(2, 4, figsize=(13, 6.2), facecolor=SURFACE, sharex=True)
    flat = axes.ravel()
    sub_linear = 0
    for ax, q in zip(flat, queries, strict=False):
        pts = sorted((r["rows"], r["median_s"]) for r in rows if r["query"] == q and r["median_s"])
        x, y = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
        sub_linear += int(y[-1] / y[0] < x[-1] / x[0])
        ax.set_facecolor(SURFACE)
        ax.plot(x, x / x[0] * y[0], color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
        ax.plot(x, y, color=BLUE, linewidth=2)
        ax.scatter(x[-1:], y[-1:], color=BLUE, s=34, zorder=3, edgecolor=SURFACE, linewidth=1.2)
        ax.annotate(
            f"{y[-1]:.1f}s",
            (x[-1], y[-1]),
            textcoords="offset points",
            xytext=(-4, 8),
            ha="right",
            fontsize=9,
            color=INK2,
        )
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(LABELS[q], loc="left", fontsize=10, color=INK)
        ax.grid(True, color=GRID, linewidth=0.6)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax = flat[-1]
    ax.axis("off")
    ax.text(
        0, 0.82, "How to read", fontsize=10, color=INK, fontweight="bold", transform=ax.transAxes
    )
    ax.text(
        0,
        0.60,
        "Solid line: median seconds.\nDashed line: what perfectly\nlinear growth would cost,\nanchored at 1M rows.\nBelow the dashed line means\nthe query scales better\nthan linear.",
        fontsize=9,
        color=INK2,
        va="top",
        transform=ax.transAxes,
    )
    for ax in axes[1]:
        ax.set_xlabel("Rows", color=INK2, fontsize=9)
    for ax in axes[:, 0]:
        ax.set_ylabel("Seconds", color=INK2, fontsize=9)
    fig.text(
        0.015,
        0.975,
        f"{sub_linear} of {len(queries)} queries grow more slowly than the data, {len(queries) - sub_linear} faster",
        fontsize=14,
        fontweight="bold",
        color=INK,
        va="top",
    )
    fig.text(
        0.015,
        0.935,
        "AMLworld HI-Large on one laptop, 1 million to 180 million rows. Above the dashed line: slower than linear growth.",
        fontsize=10,
        color=INK2,
        va="top",
    )
    fig.text(
        0.015,
        0.012,
        FOOTER.replace("Banco Sintetico, not a real institution", "IBM AMLworld synthetic data"),
        fontsize=7.5,
        color=MUTED,
        va="bottom",
    )
    fig.subplots_adjust(top=0.84, bottom=0.11, left=0.06, right=0.98, wspace=0.28, hspace=0.42)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig_layouts(layouts: list[dict], path: Path) -> None:
    """The same one-day query on three file layouts."""
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    order = sorted(layouts, key=lambda r: -r["median_s"])
    ys = np.arange(len(order))
    ax.barh(ys, [r["median_s"] for r in order], height=0.5, color=BLUE)
    for y, r in zip(ys, order, strict=True):
        ax.text(
            r["median_s"] * 1.02,
            y,
            f"{r['median_s'] * 1000:,.0f} ms  ({r['size_mb']:,.0f} MB on disk)",
            va="center",
            fontsize=9,
            color=INK2,
        )
    ax.set_yticks(ys, [r["layout"] for r in order], color=INK2)
    ax.set_xlabel("Seconds to read one day out of 50 million rows", color=INK2, fontsize=9)
    ax.set_xlim(0, max(r["median_s"] for r in order) * 1.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    fig.text(
        0.015,
        0.965,
        "How the files are laid out decides how much of them a query reads",
        fontsize=13,
        fontweight="bold",
        color=INK,
        va="top",
    )
    fig.text(
        0.015,
        0.012,
        FOOTER.replace("Banco Sintetico, not a real institution", "IBM AMLworld synthetic data"),
        fontsize=7.5,
        color=MUTED,
        va="bottom",
    )
    fig.subplots_adjust(top=0.82, bottom=0.2, left=0.17, right=0.97)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def render_markdown(rows: list[dict], info: dict, layouts: list[dict] | None) -> str:
    """The benchmark report."""
    sizes = sorted({r["rows"] for r in rows})
    lines = [
        "# Scaling benchmark",
        "",
        "Query time and memory from 1 million to 180 million rows on one machine. Protocol in",
        "`src/fincrime/lake/benchmark.py`: each size is a prefix of the same time-ordered file, each",
        "query runs three times (once only if its first run exceeds two minutes), the median is",
        "reported, and memory is the peak resident set sampled every 50 ms.",
        "",
        (
            f"Machine: {info['cpu_threads']} threads, {info['memory_gb']} GB RAM, {info['os']}, "
            f"DuckDB {info['duckdb']}, Python {info['python']}."
            if info
            else "Machine: not recorded."
        ),
        "",
        "| Query | " + " | ".join(f"{s / 1e6:,.0f}M" for s in sizes) + " | Growth 1M to max |",
        "|---|" + "---|" * (len(sizes) + 1),
    ]
    for q, label in LABELS.items():
        cells, pts = [], {}
        for s in sizes:
            r = next((x for x in rows if x["query"] == q and x["rows"] == s), None)
            if r is None:
                cells.append("")
            elif r["status"] != "ok":
                cells.append(r["status"].replace("_", " "))
            else:
                pts[s] = r["median_s"]
                cells.append(f"{r['median_s']:.2f}s")
        growth = ""
        if len(pts) >= 2:
            lo, hi = min(pts), max(pts)
            growth = f"time x{pts[hi] / pts[lo]:.0f} for data x{hi / lo:.0f}"
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {growth} |")
    peak = max((r["peak_mb"] for r in rows if r["peak_mb"]), default=0)
    lines += ["", f"Peak process memory over the whole run: {peak / 1000:.1f} GB.", ""]
    if layouts:
        lines += ["## File layout", "", "| Layout | Median time | Size on disk |", "|---|---|---|"]
        lines += [
            f"| {r['layout']} | {r['median_s'] * 1000:,.0f} ms | {r['size_mb']:,.0f} MB |"
            for r in layouts
        ]
    return "\n".join(lines) + "\n"


def build(csv_path: Path, out_md: Path, img_dir: Path) -> list[Path]:
    """Write the two figures and the Markdown report next to the CSV."""
    rows = read_results(csv_path)
    layouts_file = csv_path.with_suffix(".layouts.json")
    machine_file = csv_path.with_suffix(".machine.json")
    info = json.loads(machine_file.read_text(encoding="utf-8")) if machine_file.exists() else {}
    layouts = None
    if layouts_file.exists():
        layouts = json.loads(layouts_file.read_text(encoding="utf-8"))["layouts"]
    img_dir.mkdir(parents=True, exist_ok=True)
    made = [img_dir / "lake_scaling.png"]
    fig_scaling(rows, made[0])
    if layouts:
        made.append(img_dir / "lake_layouts.png")
        fig_layouts(layouts, made[1])
    out_md.write_text(render_markdown(rows, info, layouts), encoding="utf-8")
    return [*made, out_md]
