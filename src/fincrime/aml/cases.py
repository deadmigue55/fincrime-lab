"""SAR-style case summaries: plain text built only from what the bank can see.

A summary states facts from the weekly tables (who, how much, how fast, which phones) and which
rules or score put the case in the queue. It never uses the truth columns, so the same text could be
written about a real case. ``pick`` uses the truth only to choose illustrative examples, and says so.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fincrime.aml import snapshots as sn

LINE = 15_000.0


def summary(t: sn.Tables, w: int, k: int, accounts: np.ndarray, start: np.datetime64) -> str:
    """A short narrative for one case in the week ``w`` batch."""
    lo = max(0, w - k + 1)
    ids = [int(a) for a in accounts]
    aw = t.aw.filter(pl.col("account_id").is_in(ids) & (pl.col("w") >= lo) & (pl.col("w") <= w))
    tot = aw.select(pl.all().exclude("account_id", "w").sum()).row(0, named=True)
    e = t.edges.filter(
        pl.col("src").is_in(ids)
        & pl.col("dst").is_in(ids)
        & (pl.col("w") >= lo)
        & (pl.col("w") <= w)
    )
    inner_n, inner_amt = int(e["n"].sum() or 0), float(e["amt"].sum() or 0.0)
    dv = t.dev.filter(pl.col("account_id").is_in(ids) & (pl.col("w") >= lo) & (pl.col("w") <= w))
    shared = (
        dv.group_by("device_id")
        .agg(pl.col("account_id").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    age = (
        (start + np.timedelta64(7 * w + 6, "D") - t.opened[ids])
        .astype("timedelta64[D]")
        .astype(int)
    )
    day = lambda x: str(start + np.timedelta64(7 * x, "D"))  # noqa: E731
    external_out = tot["out_amt"] - tot["int_out_amt"]
    fired = t.rules.filter(pl.col("account_id").is_in(ids) & (pl.col("w") == w))
    hits = [r.upper() for r in ("r1", "r2", "r3") if fired[r].any()]
    if shared.height and shared["n"].max() - 1 >= sn.SHARED_DEVICE_RULE:
        hits.append("R4")
    lines = [
        f"Weeks {day(lo)} to {day(w + 1)}: {len(ids)} linked account(s), opened a median of "
        f"{int(np.median(age))} days earlier.",
        f"They received MXN {tot['in_amt']:,.0f} ({tot['cash_n']} cash deposits, {tot['near_n']} of "
        f"them between 80% and 100% of the MXN {LINE:,.0f} review line) and sent MXN "
        f"{tot['out_amt']:,.0f}, of which MXN {external_out:,.0f} left the bank.",
        f"{inner_n} transfers moved MXN {inner_amt:,.0f} among the accounts of the case; "
        f"{shared.height} phone(s) were used by more than one of them.",
    ]
    if tot["in_amt"] > 0:
        lines.append(f"Sent as a share of received: {tot['out_amt'] / tot['in_amt']:.0%}.")
    lines.append(f"Rules fired: {', '.join(hits) if hits else 'none (queued by model score)'}.")
    return " ".join(lines)


def pick(
    t: sn.Tables, cases: list[list[np.ndarray]], budget: int = 3
) -> dict[str, tuple[int, int]]:
    """Weeks and case positions of one scatter-gather, one structuring and one false positive.

    Chosen with the truth, so they are illustrations and not a sample.
    """
    truth = {
        (int(a), int(w)): ft
        for a, w, ft in zip(t.truth["account_id"], t.truth["w"], t.truth["fraud_type"], strict=True)
        if ft in ("scatter_gather", "structuring")
    }
    out: dict[str, tuple[int, int]] = {}
    for w, wk in enumerate(cases):
        for j, (c, _) in enumerate(wk[:budget]):
            kinds = {truth[(int(a), w)] for a in c if (int(a), w) in truth}
            if not kinds and w >= 12 and len(c) > 1:  # past the cold start, more than one account
                out.setdefault("a case with no laundering in it", (w, j))
            for kd in kinds:
                out.setdefault(kd.replace("_", "-"), (w, j))
    return out
