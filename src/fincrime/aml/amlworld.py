"""The external check: IBM AMLworld, with and without graph features (docs/methodology.md section 10).

The benchmark has labelled laundering transactions among millions of ordinary ones, and a pattern
file naming the scheme (fan-out, cycle, ...) of about a third to two thirds of them. Every feature
is the state of an account strictly before the transaction, so nothing in the future leaks.

* ``T`` (tabular): the transaction's own fields and the sender's counts and pesos so far.
* ``TR``: ``T`` plus the receiver's counts and pesos so far (a control: more history, no structure).
* ``G`` (graph): ``TR`` plus how many distinct accounts the sender and the receiver have dealt with,
  and whether the pair has traded before. ``G`` against ``TR`` is what the graph adds.

"Before" means in an earlier minute: the file records minutes, many transactions share one, and
their order is not known.

Split by time, 60% train, 20% skipped, 20% test, as the benchmark's authors did. Trees are fixed
(the same settings as the bank) and negatives are sampled at 5% for training.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

PARAMS = {"max_depth": 3, "max_iter": 70, "learning_rate": 0.1, "early_stopping": False}
NEG_SAMPLE = 0.05
FLAG_SHARES = (0.001, 0.005)
SEEDS = (3, 4, 5, 6, 7)
OWN = ["amount_paid", "amount_received", "cross_bank", "hour", "fmt", "cur_pair"]
SENDER = ["s_out_n", "s_in_n", "s_out_amt", "s_in_amt", "s_pass"]
RECEIVER = [
    "r_out_n",
    "r_in_n",
    "r_out_amt",
    "r_in_amt",
]  # the receiver's own history, no structure
GRAPH_ONLY = [
    "s_out_dist",
    "s_in_dist",
    "r_out_dist",
    "r_in_dist",
    "pair_seen",
]  # who with, not how much
GRAPH_EXTRA = RECEIVER + GRAPH_ONLY


def _states(tx: pl.DataFrame) -> pl.DataFrame:
    """Account state before each transaction, for the sender and for the receiver."""
    out = tx.select(
        "i", "ts", pl.col("src").alias("acct"), pl.col("dst").alias("partner"),
        pl.lit(1).alias("is_out"), pl.col("amount_paid").alias("amt"),
    )  # fmt: skip
    inn = tx.select(
        "i", "ts", pl.col("dst").alias("acct"), pl.col("src").alias("partner"),
        pl.lit(0).alias("is_out"), pl.col("amount_received").alias("amt"),
    )  # fmt: skip
    long = pl.concat([out, inn]).sort("acct", "ts", "i", "is_out", descending=[False] * 3 + [True])
    first = (pl.col("i").cum_count().over("acct", "is_out", "partner") == 1).cast(pl.Int32)
    long = long.with_columns(
        o=pl.col("is_out"),
        n_in=1 - pl.col("is_out"),
        a_out=pl.col("amt") * pl.col("is_out"),
        a_in=pl.col("amt") * (1 - pl.col("is_out")),
        f_out=first * pl.col("is_out"),
        f_in=first * (1 - pl.col("is_out")),
    )
    cols = {"o": "out_n", "n_in": "in_n", "a_out": "out_amt", "a_in": "in_amt",
            "f_out": "out_dist", "f_in": "in_dist"}  # fmt: skip
    long = long.with_columns(
        [
            (pl.col(c).cum_sum().over("acct") - pl.col(c).cum_sum().over("acct", "ts")).alias(n)
            for c, n in cols.items()
        ]  # everything in earlier minutes: rows of the same minute have no order
    )
    return long.select("i", "is_out", "acct", "partner", *cols.values())


def features(path: Path, name: str) -> pl.DataFrame:
    """One row per transaction: own fields, sender and receiver state, label, pattern type."""
    tx = (
        pl.read_parquet(path / f"{name}_Trans.parquet")
        .sort("ts", "txn_id")
        .with_row_index("i")
        .with_columns(
            src=pl.col("from_bank") + "|" + pl.col("from_account"),
            dst=pl.col("to_bank") + "|" + pl.col("to_account"),
            cross_bank=(pl.col("from_bank") != pl.col("to_bank")).cast(pl.Float32),
            hour=pl.col("ts").dt.hour().cast(pl.Float32),
            fmt=pl.col("payment_format").cast(pl.Categorical).to_physical().cast(pl.Float32),
            cur_pair=(pl.col("payment_currency") + ">" + pl.col("receiving_currency"))
            .cast(pl.Categorical)
            .to_physical()
            .cast(pl.Float32),
        )
    )
    st = _states(tx)
    s = st.filter(pl.col("is_out") == 1).select(
        "i",
        pl.col("out_n").alias("s_out_n"), pl.col("in_n").alias("s_in_n"),
        pl.col("out_amt").alias("s_out_amt"), pl.col("in_amt").alias("s_in_amt"),
        pl.col("out_dist").alias("s_out_dist"), pl.col("in_dist").alias("s_in_dist"),
    )  # fmt: skip
    r = st.filter(pl.col("is_out") == 0).select(
        "i",
        pl.col("out_n").alias("r_out_n"), pl.col("in_n").alias("r_in_n"),
        pl.col("out_amt").alias("r_out_amt"), pl.col("in_amt").alias("r_in_amt"),
        pl.col("out_dist").alias("r_out_dist"), pl.col("in_dist").alias("r_in_dist"),
    )  # fmt: skip
    df = tx.join(s, on="i").join(r, on="i")
    df = (
        df.sort("i")
        .with_columns(
            pair_seen=(
                pl.col("i").cum_count().over("src", "dst")
                - pl.col("i").cum_count().over("src", "dst", "ts")
                > 0
            ).cast(pl.Float32),
            s_pass=pl.min_horizontal("s_in_amt", "s_out_amt")
            / pl.max_horizontal("s_in_amt", "s_out_amt"),
        )
        .fill_nan(0.0)
        .fill_null(0.0)
    )
    pat = (
        pl.read_parquet(path / f"{name}_Patterns.parquet")
        .select(
            "ts",
            "from_bank",
            "from_account",
            "to_bank",
            "to_account",
            "amount_paid",
            "pattern_type",
        )
        .unique(["ts", "from_bank", "from_account", "to_bank", "to_account", "amount_paid"])
    )
    keys = ["ts", "from_bank", "from_account", "to_bank", "to_account", "amount_paid"]
    df = df.join(pat, on=keys, how="left")
    return df.select(
        "i", "ts", "src", "is_laundering", "pattern_type", *OWN, *SENDER, *GRAPH_EXTRA
    ).sort("i")


def _recall_at(score: np.ndarray, y: np.ndarray, share: float) -> tuple[float, np.ndarray]:
    k = max(1, int(share * len(score)))
    top = np.argsort(-score)[:k]
    flag = np.zeros(len(score), dtype=bool)
    flag[top] = True
    return float(flag[y == 1].mean()), flag


def _one(df: pl.DataFrame, cols: list[str], seed: int, reps: int) -> dict:
    """One fit of one feature set: PR-AUC, and recall overall and by pattern at fixed flag shares."""
    n = df.height
    y = df["is_laundering"].to_numpy()
    rng = np.random.default_rng(seed)
    keep = (np.arange(n) < int(0.6 * n)) & ((y == 1) | (rng.random(n) < NEG_SAMPLE))
    te = np.arange(n) >= int(0.8 * n)
    x = df.select(cols).to_numpy()
    model = HistGradientBoostingClassifier(random_state=seed, **PARAMS).fit(x[keep], y[keep])
    score = model.decision_function(x[te])
    yt, pt, accts = y[te], df["pattern_type"].to_numpy()[te], df["src"].to_numpy()[te]
    out: dict = {"pr_auc": float(average_precision_score(yt, score)), "flag": {}}
    for share in FLAG_SHARES:
        rec, flag = _recall_at(score, yt, share)
        by_type = {
            str(p): float(flag[(pt == p) & (yt == 1)].mean())
            for p in np.unique(pt[pt != None])  # noqa: E711
            if ((pt == p) & (yt == 1)).any()
        }
        unlisted = (pt == None) & (yt == 1)  # noqa: E711
        by_type["not in the pattern file"] = (
            float(flag[unlisted].mean()) if unlisted.any() else None
        )
        # clusters: the sending account, so a scheme's many hops are one draw
        u, inv = np.unique(accts[yt == 1], return_inverse=True)
        hits = np.bincount(inv, weights=flag[yt == 1].astype(float))
        cnt = np.bincount(inv).astype(float)
        idx = rng.integers(0, len(u), (reps, len(u)))
        boot = hits[idx].sum(axis=1) / cnt[idx].sum(axis=1)
        out["flag"][str(share)] = {
            "recall": rec,
            "lo": float(np.percentile(boot, 2.5)),
            "hi": float(np.percentile(boot, 97.5)),
            "by_type": by_type,
            "alerts": int(flag.sum()),
        }
    return out


def _mean(runs: list):
    """Mean of nested dicts of numbers (None stays None)."""
    first = runs[0]
    if isinstance(first, dict):
        return {k: _mean([r[k] for r in runs]) for k in first}
    return None if first is None else float(np.mean(runs))


def evaluate(df: pl.DataFrame, seeds: tuple[int, ...] = SEEDS, reps: int = 300) -> dict:
    """PR-AUC, recall at fixed flag shares and recall by pattern type, without and with graph.

    Each feature set is fitted once per seed (new negative sample, new trees) and reported as the
    mean with the lowest and highest PR-AUC of the refits.
    """
    n = df.height
    y = df["is_laundering"].to_numpy()
    res: dict = {
        "n_test": n - int(0.8 * n),
        "positives_test": int(y[int(0.8 * n) :].sum()),
        "models": {},
    }
    sets = {
        "T": OWN + SENDER,
        "TR": OWN + SENDER + RECEIVER,
        "G": OWN + SENDER + RECEIVER + GRAPH_ONLY,
    }
    for name, cols in sets.items():
        runs = [_one(df, cols, seed, reps) for seed in seeds]
        res["models"][name] = {
            **_mean(runs),
            "pr_auc_min": min(r["pr_auc"] for r in runs),
            "pr_auc_max": max(r["pr_auc"] for r in runs),
        }
    return res
