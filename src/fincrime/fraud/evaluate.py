"""Run the step 6 protocol of docs/methodology.md section 7 on the synthetic bank.

Stage 1 (selection): fit the four candidates on the selection block, score the validation block
against the truth, keep the best at a 0.5% flag share. Stage 2 (final): refit the chosen candidate
on the final block with observed labels, and, as a diagnostic, with true labels; score January to
June 2025 (the test block is April to June). The scores are saved so reports and figures can be
rebuilt without refitting.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from fincrime.fraud import labels, metrics, models

SELECTION_TRAIN = (
    date(2024, 1, 1),
    date(2024, 8, 31),
    date(2025, 1, 1),
)  # first, last, labels as of
VALIDATION = (date(2025, 1, 1), date(2025, 2, 28))
FINAL_TRAIN = (date(2024, 1, 1), date(2024, 11, 30), date(2025, 4, 1))
SCORED = (date(2025, 1, 1), date(2025, 6, 30))  # January to March feed the drift view
TEST_START = date(2025, 4, 1)
KEEP = 0.02  # share of the genuine events kept for training
SELECT_SHARE = 0.005
KEEP_COLUMNS = [
    "event_id", "ts", "event_day", "account_id", "event_type", "amount", "status", "decision",
    "y_true", "grp_state", "grp_income_band", "grp_age_band", "grp_sex",
    "n_att_10m", "n_att_24h", "out_amt_24h", "n_prior", "account_age_days", "is_new_device",
]  # fmt: skip


def months(first: date, last: date):
    """Calendar-month windows ``(start, end)`` covering ``[first, last]``."""
    d = first
    while d <= last:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield d, min(nxt - timedelta(days=1), last)
        d = nxt


def train_set(con, window: tuple, kind: str, vocab: dict | None = None):
    """Design matrix, labels and weights. ``obs``: approved events, reported-before-``as_of``
    labels. ``true``: every attempt, the truth (the impossible diagnostic)."""
    first, last, as_of = window
    if kind == "obs":
        df = labels.frame(
            con,
            first,
            last,
            as_of=as_of,
            approved_only=True,
            keep_negatives=KEEP,
            sample_on="y_obs",
        )
        y = df["y_obs"].to_numpy()
    else:
        df = labels.frame(con, first, last, keep_negatives=KEEP, sample_on="y_true")
        y = df["y_true"].to_numpy()
    x, vocab = labels.design(df, vocab)
    return x, y, np.where(y, 1.0, 1.0 / KEEP), vocab, df


def score_blocks(con, first: date, last: date, fitted: dict, vocab: dict) -> pl.DataFrame:
    """Score every outgoing attempt in ``[first, last]`` with each fitted model (month by month)."""
    parts = []
    for a, b in months(first, last):
        t0 = time.time()
        df = labels.frame(con, a, b)
        x, _ = labels.design(df, vocab)
        out = df.select(KEEP_COLUMNS)
        for name, model in fitted.items():
            out = out.with_columns(pl.Series(f"score_{name}", models.predict(model, x)))
        parts.append(out)
        print(f"  scored {a:%Y-%m}: {len(df):,} events in {time.time() - t0:.0f}s", flush=True)
    return pl.concat(parts)


def run(store: Path, lake: Path, out: Path, candidates=models.CANDIDATES) -> dict:
    """Both stages; writes ``selection.json`` and the scores under ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    con = labels.connect(store, lake)
    t0 = time.time()
    summary: dict = {"candidates": list(candidates), "variants_tried": len(candidates)}

    print("stage 1: selection", flush=True)
    x, y, w, vocab, df = train_set(con, SELECTION_TRAIN, "obs")
    summary["selection_train"] = {
        "rows": len(y),
        "observed_fraud": int(y.sum()),
        "weighted_rows": int(w.sum()),
    }
    print(f"  train: {len(y):,} rows, {int(y.sum())} observed fraud", flush=True)
    fitted = {}
    for name in candidates:
        fitted[name] = models.fit(name, x, y, w)
        print(f"  fitted {name} ({time.time() - t0:.0f}s)", flush=True)
    val = score_blocks(con, *VALIDATION, fitted, vocab)
    yv = val["y_true"].to_numpy()
    table = {}
    for name in candidates:
        s = val[f"score_{name}"].to_numpy()
        table[name] = {
            "recall_at_0.5%": metrics.recall_at_share(yv, s, (SELECT_SHARE,))[SELECT_SHARE],
            "ap": metrics.average_precision(yv, s),
            "auc": metrics.roc_auc(yv, s),
        }
    chosen = max(
        candidates, key=lambda n: (round(table[n]["recall_at_0.5%"], 6), -candidates.index(n))
    )
    summary |= {
        "validation": table,
        "chosen": chosen,
        "validation_positives": int(yv.sum()),
        "validation_rows": len(yv),
    }
    val.select([*KEEP_COLUMNS, f"score_{chosen}"]).rename(
        {f"score_{chosen}": "score"}
    ).write_parquet(out / "validation.parquet")
    print(f"  chosen: {chosen} {json.dumps(table[chosen])}", flush=True)

    print("stage 2: final fit and test scoring", flush=True)
    final = {}
    for kind in ("obs", "true"):
        x, y, w, vocab, df = train_set(con, FINAL_TRAIN, kind, vocab)
        summary[f"final_train_{kind}"] = {"rows": len(y), "fraud": int(y.sum())}
        print(f"  {kind} train: {len(y):,} rows, {int(y.sum())} fraud", flush=True)
        final[kind] = models.fit(chosen, x, y, w)
        if kind == "obs":
            ref = df.select(["amount", "n_att_24h", "out_amt_24h", "n_prior", "account_age_days"])
            ref = ref.with_columns(pl.Series("weight", w))
            ref.write_parquet(out / "train_reference.parquet")
    scored = score_blocks(con, *SCORED, {"final": final["obs"], "oracle": final["true"]}, vocab)
    scored.write_parquet(out / "scored.parquet")
    summary["seconds"] = round(time.time() - t0)
    (out / "selection.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary


def ablation(store: Path, lake: Path, out: Path, name: str) -> dict:
    """Leave one feature family out, refit on the selection block, score the validation block.

    Uses validation only (never the test block). Shows which signals carry the result and
    guards against a single leaky feature doing all the work.
    """
    from fincrime.fraud import features

    con = labels.connect(store, lake)
    x, y, w, vocab, _ = train_set(con, SELECTION_TRAIN, "obs")
    val = pl.concat([labels.frame(con, a, b) for a, b in months(*VALIDATION)])
    xv, _ = labels.design(val, vocab)
    yv = val["y_true"].to_numpy()
    family = {f.name: f.family for f in features.catalogue()}
    cols = [*labels.CATEGORICAL, *labels.NUMERIC]
    result = {}
    for drop in [None, *sorted({family[c] for c in cols})]:
        keep = [i for i, c in enumerate(cols) if drop is None or family[c] != drop]
        n_cat = sum(cols[i] in labels.CATEGORICAL for i in keep)
        model = models.fit(name, x[:, keep], y, w, n_cat=n_cat)
        s = models.predict(model, xv[:, keep])
        result[drop or "all features"] = {
            "features": len(keep),
            "recall_at_0.5%": metrics.recall_at_share(yv, s, (SELECT_SHARE,))[SELECT_SHARE],
            "ap": metrics.average_precision(yv, s),
        }
        print(f"  without {drop}: {result[drop or 'all features']}", flush=True)
    (out / "ablation.json").write_text(
        json.dumps(result, indent=2, default=float), encoding="utf-8"
    )
    return result
