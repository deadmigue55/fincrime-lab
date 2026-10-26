"""Scored account-weeks and the cases an investigator would open (docs/methodology.md section 10).

``frame`` joins the weekly snapshots to the truth (positive account-weeks, fold by ring) and scores
them out of fold with the two boosted-tree models. ``cases`` turns one week of scores, or of rule
alerts, into cases: alerted accounts that share a transfer or a phone in the lookback are one case,
and cases are ranked by their best-scored account.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from sklearn.ensemble import HistGradientBoostingClassifier

from fincrime.aml import snapshots as sn

FOLDS = 5
SEEDS = (11, 12, 13, 14, 15)  # refits: the top of the ranking is noise-sensitive, see evaluate
NEG_SAMPLE = 0.02
CANDIDATES = 600  # accounts per week the case builder looks at, best scores first
POSITIVE_TYPES = ("scatter_gather", "structuring", "mule_aging")
NO_DEVICE = [g for g in sn.GRAPH if g != "g_dev_shared"]
MODELS = {"M1": sn.TAB, "M2": sn.TAB + sn.GRAPH, "M2n": sn.TAB + NO_DEVICE}  # M2n: the ablation
PARAMS = {"max_depth": 3, "max_iter": 70, "learning_rate": 0.1, "early_stopping": False}


def folds(bank_dir: Path, n: int, seed: int = 9) -> np.ndarray:
    """Fold of every account id: whole rings together, other accounts at random."""
    acc = pl.read_parquet(bank_dir / "accounts.parquet").select("account_id", "customer_id")
    cust = pl.read_parquet(bank_dir / "customers.parquet").select("customer_id", "ring_id")
    m = acc.join(cust, on="customer_id", how="left")
    rng = np.random.default_rng(seed)
    out = rng.integers(0, FOLDS, n)
    rings = m.filter(pl.col("ring_id").is_not_null())
    ring_ids = np.unique(rings["ring_id"].to_numpy())
    by_ring = dict(zip(ring_ids, rng.permutation(len(ring_ids)) % FOLDS, strict=True))
    out[rings["account_id"].to_numpy()] = [by_ring[r] for r in rings["ring_id"].to_numpy()]
    return out


def frame(
    t: sn.Tables, bank_dir: Path, k: int, start: np.datetime64, seeds: tuple[int, ...] = SEEDS
) -> pl.DataFrame:
    """Snapshots with ``label``, ``fold`` and out-of-fold scores ``s_<model>_<seed index>``.

    Every model is refitted for each seed (new negative sample, new trees). Many account-weeks of a
    ring score close to 1, so which of them are on top of a short queue is decided by tiny
    differences; one fit would report one arbitrary ordering as if it were the method.
    """
    df = sn.all_weeks(t, k, start)
    pos = (
        t.truth.filter(pl.col("fraud_type").is_in(POSITIVE_TYPES))
        .select("account_id", "w")
        .unique()
        .with_columns(label=pl.lit(1, dtype=pl.Int8))
    )
    fold = folds(bank_dir, t.n)
    df = df.join(pos, on=["account_id", "w"], how="left").with_columns(
        pl.col("label").fill_null(0),
        fold=pl.Series(fold[df["account_id"].to_numpy()]).cast(pl.Int8),
    )
    y = df["label"].to_numpy()
    fo = df["fold"].to_numpy()
    for name, cols in MODELS.items():
        x = df.select(cols).to_numpy()
        for i, seed in enumerate(seeds):
            keep = (y == 1) | (np.random.default_rng(seed).random(len(y)) < NEG_SAMPLE)
            score = np.zeros(len(y), dtype=np.float32)
            for f in range(FOLDS):
                tr = keep & (fo != f)
                model = HistGradientBoostingClassifier(random_state=seed, **PARAMS)
                model.fit(x[tr], y[tr])
                te = fo == f
                score[te] = model.decision_function(
                    x[te]
                )  # log-odds: probabilities saturate to 1.0 and tie
            df = df.with_columns(pl.Series(f"s_{name}_{i}", score))
    return df


def links(t: sn.Tables, w: int, k: int, accounts: np.ndarray) -> sp.csr_matrix:
    """Adjacency among ``accounts``: they exchanged money or shared a phone in the lookback."""
    lo = max(0, w - k + 1)
    index = {int(a): i for i, a in enumerate(accounts)}
    pos = np.full(t.n, -1)
    pos[accounts] = np.arange(len(accounts))
    e = t.edges.filter((pl.col("w") >= lo) & (pl.col("w") <= w))
    s, d = e["src"].to_numpy(), e["dst"].to_numpy()
    ok = (pos[s] >= 0) & (pos[d] >= 0)
    rows, cols = [pos[s][ok]], [pos[d][ok]]
    dv = t.dev.filter(
        (pl.col("w") >= lo) & (pl.col("w") <= w) & pl.col("account_id").is_in(list(index))
    ).unique(["device_id", "account_id"])
    for _, grp in dv.group_by("device_id"):
        members = pos[grp["account_id"].to_numpy()]
        if len(members) > 1:
            rows.append(np.repeat(members, len(members)))
            cols.append(np.tile(members, len(members)))
    r, c = np.concatenate(rows), np.concatenate(cols)
    return sp.coo_matrix((np.ones(len(r)), (r, c)), shape=(len(accounts),) * 2).tocsr()


def cases(
    t: sn.Tables, w: int, k: int, accounts: np.ndarray
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Cases of one week from ``accounts`` listed best first: connected groups, best group first.

    Each case is ``(accounts, ranks)`` with the members in rank order, where a rank is the position
    in the input. A team that opens ``b`` cases works the accounts ranked above the point where the
    ``b + 1``-th case would open, which is how ``evaluate`` credits a budget.
    """
    if len(accounts) == 0:
        return []
    n, label = connected_components(links(t, w, k, accounts), directed=False)
    rank = np.arange(len(accounts))
    first = np.full(n, len(accounts))
    np.minimum.at(first, label, rank)
    return [(accounts[label == c], rank[label == c]) for c in np.argsort(first)]
