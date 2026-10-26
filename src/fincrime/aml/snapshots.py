"""Weekly account snapshots: the features a monitoring batch has on a Sunday night.

For week ``w`` and a lookback of ``k`` weeks, every account active in weeks ``w-k+1 .. w`` gets one
row, built only from events up to the end of week ``w``. ``tab_*`` columns are what an account's own
history shows (counts, pesos, cash just under the review line, pass-through of money). ``g_*``
columns need the other accounts: how many it deals with, how connected those are, how big the
cluster of large transfers around it is, and how many accounts share its phone.

Graph features use the transfers between accounts of the bank. Two graphs are built per window: all
transfers (for degrees and reach) and only the edges that moved at least ``EDGE_MIN`` pesos in the
window (for clusters), because the unfiltered genuine graph is one giant component
(docs/methodology.md section 10).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

EDGE_MIN = 20_000.0
SHARED_DEVICE_RULE = 4  # R4: the phone was used by at least this many other accounts
TAB = [
    "in_n", "in_amt", "out_n", "out_amt", "cash_n", "cash_amt", "near_n", "int_out_n",
    "int_out_amt", "pass_ratio", "wk_in_amt", "wk_out_amt", "wk_near_n", "age_days",
]  # fmt: skip
GRAPH = [
    "g_deg", "g_deg_in", "g_deg_out", "g_nbr_deg", "g_reach2", "g_comp_size", "g_comp_flow",
    "g_nbr_pass", "g_nbr_young", "g_dev_shared",
]  # fmt: skip


class Tables:
    """The parquet tables of ``build``, plus account ages, loaded once."""

    def __init__(self, folder: Path, bank_dir: Path) -> None:
        self.aw = pl.read_parquet(folder / "aw.parquet")
        self.edges = pl.read_parquet(folder / "edges.parquet")
        self.dev = pl.read_parquet(folder / "dev.parquet")
        self.rules = pl.read_parquet(folder / "rules.parquet")
        self.truth = pl.read_parquet(folder / "truth.parquet")
        acc = pl.read_parquet(bank_dir / "accounts.parquet")
        self.n = int(acc["account_id"].max()) + 1
        self.opened = np.full(self.n, np.datetime64("2100-01-01"), dtype="datetime64[D]")
        self.opened[acc["account_id"].to_numpy()] = acc["opened_on"].to_numpy()
        self.weeks = int(self.aw["w"].max()) + 1


def _csr(n: int, src: np.ndarray, dst: np.ndarray, val: np.ndarray) -> sp.csr_matrix:
    return sp.coo_matrix((val, (src, dst)), shape=(n, n)).tocsr()  # duplicates are summed


def snapshot(t: Tables, w: int, k: int, start: np.datetime64) -> pl.DataFrame:
    """One row per account active in the ``k`` weeks ending with week ``w``."""
    lo = max(0, w - k + 1)
    aw = t.aw.filter((pl.col("w") >= lo) & (pl.col("w") <= w))
    this = aw.filter(pl.col("w") == w).select(
        "account_id",
        pl.col("in_amt").alias("wk_in_amt"),
        pl.col("out_amt").alias("wk_out_amt"),
        pl.col("near_n").alias("wk_near_n"),
    )
    sums = ["in_n", "in_amt", "out_n", "out_amt", "cash_n", "cash_amt", "near_n", "int_out_n"]
    sums.append("int_out_amt")
    df = (
        aw.group_by("account_id")
        .agg([pl.col(c).sum() for c in sums])
        .join(this, on="account_id", how="left")
    )
    df = df.with_columns(
        pass_ratio=pl.min_horizontal("in_amt", "out_amt") / pl.max_horizontal("in_amt", "out_amt")
    ).with_columns(pl.col("pass_ratio", "wk_in_amt", "wk_out_amt", "wk_near_n").fill_null(0.0))
    ids = df["account_id"].to_numpy()
    end = start + np.timedelta64(7 * w + 6, "D")
    age = (end - t.opened[ids]).astype("timedelta64[D]").astype(float)
    n = t.n

    e = t.edges.filter((pl.col("w") >= lo) & (pl.col("w") <= w))
    src, dst, amt = e["src"].to_numpy(), e["dst"].to_numpy(), e["amt"].to_numpy()
    directed = _csr(n, src, dst, amt)
    und = ((directed + directed.T) > 0).astype(np.float32).tocsr()
    deg = np.asarray(und.sum(axis=1)).ravel()
    deg_in = np.asarray((directed.T > 0).sum(axis=1)).ravel()
    deg_out = np.asarray((directed > 0).sum(axis=1)).ravel()
    safe = np.maximum(deg, 1)
    nbr_deg = (und @ deg) / safe
    two = (und @ und + und).tocsr()
    two.setdiag(0)
    two.eliminate_zeros()
    reach2 = np.diff(two.indptr)

    heavy = directed.copy()
    heavy.data = np.where(heavy.data >= EDGE_MIN, heavy.data, 0.0)
    heavy.eliminate_zeros()
    heavy = heavy + heavy.T
    _, label = connected_components(heavy, directed=False)
    comp_size = np.bincount(label)[label].astype(float)
    hs, _ = heavy.nonzero()
    flow = np.bincount(label[hs], weights=heavy.data, minlength=label.max() + 1)
    comp_flow = flow[label] / 2.0

    full_pass = np.zeros(n)
    full_pass[ids] = df["pass_ratio"].to_numpy()
    young = np.zeros(n)
    young[ids] = (age < 60).astype(float)
    nbr_pass = (und @ full_pass) / safe
    nbr_young = (und @ young) / safe

    d = t.dev.filter((pl.col("w") >= lo) & (pl.col("w") <= w)).select("device_id", "account_id")
    d = d.unique()
    shared = d.group_by("device_id").agg((pl.len() - 1).alias("n_other"))
    dev_shared = (
        d.join(shared, on="device_id")
        .group_by("account_id")
        .agg(pl.col("n_other").max().alias("g_dev_shared"))
    )
    extra = {
        "age_days": age,
        "g_deg": deg[ids],
        "g_deg_in": deg_in[ids],
        "g_deg_out": deg_out[ids],
        "g_nbr_deg": nbr_deg[ids],
        "g_reach2": reach2[ids].astype(float),
        "g_comp_size": comp_size[ids],
        "g_comp_flow": comp_flow[ids],
        "g_nbr_pass": nbr_pass[ids],
        "g_nbr_young": nbr_young[ids],
    }
    df = df.with_columns([pl.Series(c, v) for c, v in extra.items()])
    df = df.join(dev_shared, on="account_id", how="left").with_columns(
        pl.col("g_dev_shared").fill_null(0)
    )
    rules = t.rules.filter(pl.col("w") == w).select("account_id", "r1", "r2", "r3")
    df = df.join(rules, on="account_id", how="left").with_columns(
        pl.col("r1", "r2", "r3").fill_null(False),
        r4=pl.col("g_dev_shared") >= SHARED_DEVICE_RULE,
        w=pl.lit(w, dtype=pl.Int16),
    )
    cast = [pl.col(c).cast(pl.Float32) for c in TAB + GRAPH]
    return df.with_columns(cast).select(["account_id", "w", *TAB, *GRAPH, "r1", "r2", "r3", "r4"])


def all_weeks(t: Tables, k: int, start: np.datetime64) -> pl.DataFrame:
    """The snapshots of every week with a full lookback or less (the first weeks have little)."""
    return pl.concat([snapshot(t, w, k, start) for w in range(t.weeks)])
