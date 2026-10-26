"""README figures drawn from a built synthetic bank (``fincrime figures``).

Palette: the first three slots of a validated categorical palette (blue, orange, aqua), which
hold up when the series are not adjacent. One y-axis per chart; related measures sit in
separate panels. Every figure carries a SYNTHETIC footer, per ``docs/methodology.md``.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
FOOTER = (
    "SYNTHETIC DATA: Banco Sintetico, not a real institution. github.com/deadmigue55/fincrime-lab"
)

TYPE_LABEL = {
    "card_pos": "Card, in person",
    "card_online": "Card, online",
    "spei_out": "SPEI transfer out",
    "spei_in": "SPEI transfer in",
    "payroll": "Payroll",
    "cash_in": "Cash deposit at a store",
    "atm": "ATM withdrawal",
    "bill_pay": "Bill payment",
    "codi": "CoDi QR payment",
}


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _frame(width: float, height: float, ncols: int = 1) -> tuple[plt.Figure, np.ndarray]:
    fig, axes = plt.subplots(1, ncols, figsize=(width, height), facecolor=SURFACE)
    axes = np.atleast_1d(axes)
    for ax in axes:
        _style(ax)
    return fig, axes


def _finish(
    fig: plt.Figure,
    path: Path,
    title: str,
    subtitle: str,
    left: float = 0.07,
    wspace: float = 0.28,
    footer: str = FOOTER,
) -> None:
    fig.text(0.015, 0.965, title, fontsize=14, fontweight="bold", color=INK, va="top")
    fig.text(0.015, 0.905, subtitle, fontsize=10, color=INK2, va="top")
    fig.text(0.015, 0.012, footer, fontsize=7.5, color=MUTED, va="bottom")
    fig.subplots_adjust(top=0.78, bottom=0.15, left=left, right=0.97, wspace=wspace)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def _connect(folder: Path) -> duckdb.DuckDBPyConnection:
    f = folder.as_posix()
    con = duckdb.connect()
    for name in ("customers", "episodes", "fraud_reports"):
        con.sql(f"create view {name} as select * from '{f}/{name}.parquet'")
    con.sql(f"create view attempts as select * from '{f}/attempts/*.parquet'")
    return con


def fig_growth(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Customers and activity over the simulated calendar."""
    cust = con.sql(
        "select date_trunc('month', onboarded_on) m, count(*) n from customers "
        "where fraud_role is null group by 1 order by 1"
    ).fetchall()
    ev = con.sql(
        "select date_trunc('month', ts) m, count(*) n from attempts "
        "where crime_class is null and status = 'approved' group by 1 order by 1"
    ).fetchall()
    fig, (a1, a2) = _frame(11, 4.3, 2)
    months = [r[0] for r in cust]
    cum = np.cumsum([r[1] for r in cust])
    a1.plot(months, cum / 1000, color=BLUE, linewidth=2)
    a1.scatter(months[-1:], cum[-1:] / 1000, color=BLUE, s=36, zorder=3)
    a1.set_ylabel("Customers (thousands)", color=INK2, fontsize=9)
    a1.set_title("Customers who have joined", loc="left", fontsize=10.5, color=INK)
    a2.bar([r[0] for r in ev], [r[1] / 1e6 for r in ev], width=22, color=BLUE)
    a2.set_ylabel("Approved transactions (millions)", color=INK2, fontsize=9)
    a2.set_title("Transactions per month", loc="left", fontsize=10.5, color=INK)
    for ax in (a1, a2):
        ax.tick_params(axis="x", rotation=0)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %y"))
        ax.xaxis.set_major_locator(matplotlib.dates.MonthLocator(interval=3))
    _finish(
        fig, path, "A bank that launches empty and grows 4% a month",
        "Volume follows the customer base; pay days and weekends add the monthly rhythm.",
    )  # fmt: skip


def fig_amounts(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Typical amount by event type: median with the 10th-90th percentile range."""
    rows = con.sql(
        "select event_type, median(amount), quantile_cont(amount, 0.10), quantile_cont(amount, 0.90) "
        "from attempts where crime_class is null and status = 'approved' group by 1 order by 2"
    ).fetchall()
    fig, (ax,) = _frame(11, 4.6)
    for i, (_etype, med, lo, hi) in enumerate(rows):
        ax.plot([lo, hi], [i, i], color=BLUE, linewidth=2, solid_capstyle="round")
        ax.scatter([med], [i], color=BLUE, s=48, zorder=3, edgecolor=SURFACE, linewidth=1.5)
        ax.text(hi * 1.15, i, f"median {med:,.0f}", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(range(len(rows)), [TYPE_LABEL.get(r[0], r[0]) for r in rows], color=INK2)
    ax.set_xscale("log")
    ax.set_xlim(10, 6e5)
    ax.set_xlabel(
        "Amount in MXN (log scale); line spans the 10th to 90th percentile", color=INK2, fontsize=9
    )
    ax.grid(axis="y", visible=False)
    _finish(
        fig, path, "Card payments are small, payroll and cash deposits are large",
        "Amounts scale with each customer's income; ATM withdrawals come in whole hundreds.",
        left=0.20,
    )  # fmt: skip


def fig_fraud(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """What the baseline rules stop, and how late the labels arrive."""
    eps = con.sql(
        "select fraud_type, sum(lost_amount), sum(blocked_amount), count(*) from episodes "
        "where crime_class = 'fraud' group by 1 order by 1"
    ).fetchall()
    cdf = con.sql(
        "with lost as (select episode_id, fraud_type from episodes "
        "              where crime_class = 'fraud' and lost_amount > 0), "
        "first_hit as (select episode_id, min(ts) t0 from attempts "
        "              where is_fraud and status = 'approved' and event_type <> 'spei_in' group by 1), "
        "rep as (select episode_id, min(reported_ts) r from fraud_reports group by 1) "
        "select l.fraud_type, datediff('hour', f.t0, r.r) / 24.0 d "
        "from lost l join first_hit f using (episode_id) left join rep r using (episode_id)"
    ).fetchall()
    fig, (a1, a2) = _frame(11, 4.4, 2)
    names = {"ato": "Account takeover", "card_testing": "Card testing"}
    ys = np.arange(len(eps))
    for k, (colour, idx, label) in enumerate(((ORANGE, 1, "Lost"), (BLUE, 2, "Blocked"))):
        vals = [e[idx] / 1e6 for e in eps]
        a1.barh(ys + (k - 0.5) * 0.36, vals, height=0.34, color=colour, label=label)
        for y, v in zip(ys + (k - 0.5) * 0.36, vals, strict=True):
            a1.text(v + max(vals) * 0.015, y, f"{v:,.2f}M", va="center", fontsize=8.5, color=INK2)
    a1.set_yticks(ys, [names.get(e[0], e[0]) for e in eps], color=INK2)
    a1.set_xlabel(
        "MXN attempted, by outcome (millions); each retry counts as its own attempt",
        color=INK2,
        fontsize=8,
    )
    a1.grid(axis="y", visible=False)
    a1.legend(frameon=False, fontsize=9, labelcolor=INK2, loc="lower right")
    a1.set_title("What the baseline rules stop", loc="left", fontsize=10.5, color=INK)

    horizon = 120
    x = np.linspace(0, horizon, 241)
    for key, colour in (("ato", AQUA), ("card_testing", INK2)):
        d = np.array([r[1] for r in cdf if r[0] == key], dtype=float)
        if len(d) == 0:
            continue
        share = [(np.nan_to_num(d, nan=1e9) <= v).mean() for v in x]
        a2.plot(x, share, color=colour, linewidth=2)
        a2.text(horizon, share[-1] + 0.02, names[key], color=INK2, fontsize=9, ha="right")
    a2.set_ylim(0, 1.05)
    a2.set_xlim(0, horizon)
    a2.set_xlabel("Days after the fraud", color=INK2, fontsize=9)
    a2.set_ylabel("Share of fraud losses reported", color=INK2, fontsize=9)
    a2.set_title(
        "The bank learns late, and not about everything", loc="left", fontsize=10.5, color=INK
    )
    _finish(
        fig, path, "Fraud labels are late and incomplete",
        "A fifth to a tenth of victims never report; card fraud takes about a month to surface.",
        left=0.15,
        wspace=0.42,
    )  # fmt: skip


def make_figures(folder: Path, out_dir: Path) -> list[Path]:
    """Draw every README figure and return the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = _connect(folder)
    made = []
    for name, fn in (
        ("bank_growth.png", fig_growth),
        ("bank_amounts.png", fig_amounts),
        ("bank_fraud.png", fig_fraud),
    ):
        path = out_dir / name
        fn(con, path)
        made.append(path)
    return made
