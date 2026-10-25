"""The same pipeline on the public Bank Account Fraud benchmark (NeurIPS 2022), Base variant.

External validation: methodology.md section 1 requires every headline to be repeated on data that
this project did not simulate. BAF is account-opening fraud, a different problem from the
synthetic bank's payment fraud, so the point is not to compare the two numbers but to check that
the *pipeline* (time split, four candidates, fixed-FPR metrics, cluster-free bootstrap, group
error rates) behaves sensibly on real-looking data with a hard, noisy signal.

Protocol, the dataset's own: train on months 0 to 5, test on months 6 and 7. Candidates are chosen
on month 5 after fitting on months 0 to 4, then refitted on months 0 to 5. BAF labels are final
(there is no reporting delay to purge) and the rows are independent applications, so the bootstrap
resamples applications, not accounts. ``device_fraud_count`` is constant in BAF and is dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from fincrime.fraud import metrics, models, report

CATEGORICAL = ("payment_type", "employment_status", "housing_status", "source", "device_os")
# -1 means "missing" in these columns
SENTINEL = (
    "prev_address_months_count",
    "current_address_months_count",
    "bank_months_count",
    "session_length_in_minutes",
    "intended_balcon_amount",
)
DROP = ("fraud_bool", "month", "variant", "device_fraud_count")
FPRS = (0.005, 0.01, 0.05)


def load(path: Path) -> pl.DataFrame:
    """The Base variant with a row id (the bootstrap unit)."""
    df = duckdb.sql(f"select * from read_parquet('{path.as_posix()}')").pl()
    return df.with_row_index("row")


def design(df: pl.DataFrame, vocab: dict | None = None):
    """Matrix with the categorical codes first (as ``models.make`` expects) and NaN for missing."""
    vocab = vocab or {c: sorted(df[c].drop_nulls().unique().to_list()) for c in CATEGORICAL}
    cats = [
        np.array(
            [{v: i for i, v in enumerate(vocab[c])}.get(v, -1) for v in df[c].to_list()],
            dtype=np.float32,
        )
        for c in CATEGORICAL
    ]
    numeric_cols = [c for c in df.columns if c not in (*CATEGORICAL, *DROP, "row")]
    num = df.select(numeric_cols).cast(pl.Float64).to_numpy().astype(np.float32)
    for j, c in enumerate(numeric_cols):
        if c in SENTINEL:
            num[num[:, j] == -1, j] = np.nan
    return np.column_stack([*cats, num]), vocab


def run(path: Path, out: Path, candidates=models.CANDIDATES) -> dict:
    """Fit, select, test, and write ``baf_results.json``."""
    df = load(path)
    n_cat = len(CATEGORICAL)
    summary: dict = {"variants_tried": len(candidates), "rows": len(df)}

    sel_train, sel_val = df.filter(pl.col("month") <= 4), df.filter(pl.col("month") == 5)
    x, vocab = design(sel_train)
    y = sel_train["fraud_bool"].to_numpy().astype(bool)
    xv, _ = design(sel_val, vocab)
    yv = sel_val["fraud_bool"].to_numpy().astype(bool)
    table = {}
    for name in candidates:
        s = models.predict(models.fit(name, x, y, np.ones(len(y)), n_cat=n_cat), xv)
        table[name] = {
            "tpr_at_5pct_fpr": metrics.recall_at_fpr(yv, s, (0.05,))[0.05],
            "ap": metrics.average_precision(yv, s),
            "auc": metrics.roc_auc(yv, s),
        }
    chosen = max(
        candidates, key=lambda n: (round(table[n]["tpr_at_5pct_fpr"], 6), -candidates.index(n))
    )
    summary |= {"validation": table, "chosen": chosen}

    train, test = df.filter(pl.col("month") <= 5), df.filter(pl.col("month") >= 6)
    x, vocab = design(train)
    y = train["fraud_bool"].to_numpy().astype(bool)
    xt, _ = design(test, vocab)
    yt = test["fraud_bool"].to_numpy().astype(bool)
    s = models.predict(models.fit(chosen, x, y, np.ones(len(y)), n_cat=n_cat), xt)
    ci = metrics.cluster_bootstrap(yt, s, test["row"].to_numpy(), shares=(0.01,), fprs=FPRS)
    summary["test"] = {
        "rows": len(yt),
        "fraud": int(yt.sum()),
        "tpr_at_fpr": {
            f: (r, *ci[("fpr", f)]) for f, r in metrics.recall_at_fpr(yt, s, FPRS).items()
        },
        "ap": metrics.average_precision(yt, s),
        "auc": metrics.roc_auc(yt, s),
        "brier": metrics.brier(yt, s),
    }
    # the dataset's own fairness question: older applicants (50+) against the rest, at the 5% FPR threshold
    thr = np.quantile(s[~yt], 1 - 0.05)
    flagged = s > thr
    older = (test["customer_age"] >= 50).to_numpy()
    groups = pl.DataFrame(
        {"account_id": test["row"], "grp_age": np.where(older, "50 and over", "under 50")}
    )
    rows = report.group_table(groups, flagged, yt, groups=("grp_age",))
    summary["age_groups"] = rows
    out.mkdir(parents=True, exist_ok=True)
    (out / "baf_results.json").write_text(
        json.dumps(summary, indent=2, default=float), encoding="utf-8"
    )
    return summary
