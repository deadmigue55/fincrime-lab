"""Labels and training frames: the one place where the feature store meets the answers.

Two kinds of label exist, and mixing them up is the classic way to fool yourself:

* **truth** (`eval.truth.is_fraud`): what really happened. Only the evaluation code and the
  "what if we had perfect labels" comparison may use it. A bank never has it.
* **observed**: an *approved* event is labelled fraud only if the victim reported it **before the
  moment the model is trained** (``as_of``). Reports arrive weeks later (median 25 days, 5% after
  four months) and some fraud is never reported, so recent events look cleaner than they are.
  Blocked attempts have no observed outcome at all and are left out of observed training.

The purge gap of docs/methodology.md follows from this: train only on events older than the label
maturity, so that "no report yet" really means "no fraud" for nearly all of them.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

from fincrime.fraud import features
from fincrime.lake import warehouse

CATEGORICAL = ("event_type", "channel", "mcc_group", "counterparty_kind")
NUMERIC = [f.name for f in features.catalogue() if f.name not in CATEGORICAL]


def connect(store: Path, lake: Path) -> duckdb.DuckDBPyConnection:
    """A session with the feature store (``feat``) and the lake views."""
    con = duckdb.connect(":memory:")
    warehouse.refresh_views(con, lake)
    con.execute(
        f"create view feat as select * from read_parquet('{store.as_posix()}/*/*.parquet', hive_partitioning = false)"
    )
    return con


def frame(
    con: duckdb.DuckDBPyConnection,
    first_day: date,
    last_day: date,
    *,
    as_of: date | None = None,
    approved_only: bool = False,
    keep_negatives: float | None = None,
    sample_on: str = "y_obs",
) -> pl.DataFrame:
    """Scored events in ``[first_day, last_day]`` with both kinds of label.

    ``y_true`` is the truth, ``y_obs`` the label a bank could have at ``as_of`` (reported before
    that date). ``keep_negatives`` keeps all frauds and a deterministic share of the rest (a hash
    of the event id), for training on a manageable sample.
    """
    where = [f"f.event_day between date '{first_day}' and date '{last_day}'"]
    if approved_only:
        where.append("a.status = 'approved'")
    as_of_sql = f"timestamp '{datetime.combine(as_of, datetime.min.time())}'" if as_of else "null"
    positive = {
        "y_true": "coalesce(t.is_fraud, false)",
        "y_obs": f"coalesce(r.reported_ts < {as_of_sql}, false)",
    }[sample_on]
    if (
        keep_negatives is not None
    ):  # the sample is drawn on the label being trained, never on the other one
        where.append(f"({positive} or hash(f.event_id) % 100000 < {int(keep_negatives * 100000)})")
    sql = f"""
        select f.*, a.status, a.decision, a.rule,
               coalesce(t.is_fraud, false) as y_true,
               coalesce(r.reported_ts < {as_of_sql}, false) as y_obs
        from feat f
        join lake.attempts a on a.event_id = f.event_id and a.event_day = f.event_day
        left join lake.truth t on t.event_id = f.event_id and t.event_day = f.event_day
        left join (select event_id, min(reported_ts) as reported_ts from lake.reports group by 1) r
               on r.event_id = f.event_id
        where {" and ".join(where)}
    """
    return con.execute(sql).pl()


def design(df: pl.DataFrame, vocab: dict[str, list[str]] | None = None):
    """Model matrix (float32) and the vocabularies of the categorical columns.

    Numbers keep their NaN for "not applicable" (HistGradientBoosting treats it as missing);
    categories become integer codes, with -1 reserved for values never seen at fit time.
    """
    vocab = vocab or {c: sorted(df[c].drop_nulls().unique().to_list()) for c in CATEGORICAL}
    cols = []
    for c in CATEGORICAL:
        index = {v: i for i, v in enumerate(vocab[c])}
        cols.append(
            np.array([index.get(v, -1) for v in df[c].to_list()], dtype=np.float32)
            if len(df)
            else np.empty(0, dtype=np.float32)
        )
    numeric = df.select(NUMERIC).cast(pl.Float64).to_numpy().astype(np.float32)
    return np.column_stack([*cols, numeric]), vocab
