"""Headline metrics for fraud scores, as fixed in docs/methodology.md section 3.

Recall is always reported at a fixed *budget*, never at a model-chosen threshold:

* a **flag share**: the top q of all scored events are sent to review (0.1%, 0.5%, 1%);
* a **false-positive rate**: the threshold lets through at most f of the genuine events
  (0.5%, 1%, 5%; BAF's own protocol uses a fixed FPR, so numbers can be compared).

With fraud at a few in ten thousand, ROC-AUC hides what happens at the operating point, so it is
reported but never the headline. Intervals come from a **cluster bootstrap by account**: the
events of one account are not independent (a takeover or a card-testing burst puts many fraud
events in one account), so resampling single events would be far too confident.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

SHARES = (0.001, 0.005, 0.01)
FPRS = (0.005, 0.01, 0.05)


def _order(score: np.ndarray) -> np.ndarray:
    """Event indices from the highest score to the lowest (stable, so ties keep input order)."""
    return np.argsort(-score, kind="stable")


def recall_at_share(y: np.ndarray, score: np.ndarray, shares: Sequence[float] = SHARES) -> dict:
    """Share of all positives found among the top ``q`` of events, for each ``q``."""
    y = np.asarray(y, dtype=bool)[_order(score)]
    hits = np.cumsum(y)
    total = max(int(y.sum()), 1)
    return {q: hits[max(int(np.floor(q * len(y))), 1) - 1] / total for q in shares}


def recall_at_fpr(y: np.ndarray, score: np.ndarray, fprs: Sequence[float] = FPRS) -> dict:
    """Share of positives found while flagging at most ``f`` of the negatives, for each ``f``."""
    y = np.asarray(y, dtype=bool)[_order(score)]
    pos, neg = np.cumsum(y), np.cumsum(~y)
    n_neg, total = max(int((~y).sum()), 1), max(int(y.sum()), 1)
    out = {}
    for f in fprs:
        k = np.searchsorted(neg, f * n_neg, side="right")  # events flagged while FPR <= f
        out[f] = (pos[k - 1] / total) if k > 0 else 0.0
    return out


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    """Area under the precision-recall curve (average precision)."""
    y = np.asarray(y, dtype=bool)[_order(score)]
    hits = np.cumsum(y)
    precision = hits / np.arange(1, len(y) + 1)
    return float(precision[y].sum() / max(int(y.sum()), 1))


def roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Probability that a random fraud scores above a random genuine event (ties count half)."""
    y = np.asarray(y, dtype=bool)
    ranks = _average_ranks(np.asarray(score))
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _average_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    sorted_x = x[order]
    ranks = np.empty(len(x))
    _, start, counts = np.unique(sorted_x, return_index=True, return_counts=True)
    ranks[order] = np.repeat(start + (counts + 1) / 2, counts)
    return ranks


def brier(y: np.ndarray, prob: np.ndarray) -> float:
    """Mean squared error of the probabilities."""
    return float(np.mean((np.asarray(prob) - np.asarray(y, dtype=float)) ** 2))


def calibration_table(y: np.ndarray, prob: np.ndarray, bins: int = 10) -> list[tuple]:
    """Reliability table: ``(mean predicted, observed rate, events)`` per equal-count bin."""
    order = np.argsort(prob, kind="stable")
    rows = []
    for idx in np.array_split(order, bins):
        if len(idx):
            rows.append((float(np.mean(prob[idx])), float(np.mean(y[idx])), len(idx)))
    return rows


def cluster_bootstrap(
    y: np.ndarray,
    score: np.ndarray,
    cluster: np.ndarray,
    shares: Sequence[float] = SHARES,
    fprs: Sequence[float] = FPRS,
    reps: int = 300,
    seed: int = 0,
    top_share: float = 0.12,
) -> dict:
    """95% intervals for the recall metrics, resampling whole clusters (accounts) with replacement.

    Only the top ``top_share`` of events by score can matter for budgets up to that size, so the
    per-replicate work is restricted to them; totals come from per-cluster counts. Returns
    ``{("share", q): (lo, hi), ("fpr", f): (lo, hi), "clusters": n_clusters_with_positives}``.
    """
    y = np.asarray(y, dtype=bool)
    if max(*shares, 0.0) > top_share / 1.2:
        raise ValueError("top_share must exceed the largest flag share by a margin")
    _, cl = np.unique(cluster, return_inverse=True)
    n_cl = cl.max() + 1
    n_events = np.bincount(cl, minlength=n_cl).astype(float)
    n_pos = np.bincount(cl, weights=y, minlength=n_cl)
    order = _order(score)[: int(np.ceil(top_share * len(y))) + 1]
    top_cl, top_y = cl[order], y[order].astype(float)
    # a false-positive budget up to 5% of the negatives reaches deeper than 5% of events
    rng = np.random.default_rng(seed)
    results = {("share", q): [] for q in shares} | {("fpr", f): [] for f in fprs}
    for _ in range(reps):
        m = np.bincount(rng.integers(0, n_cl, n_cl), minlength=n_cl).astype(float)
        w = m[top_cl]
        flagged, hit = np.cumsum(w), np.cumsum(w * top_y)
        total_n, total_p = float(m @ n_events), float(m @ n_pos)
        total_neg = total_n - total_p
        if total_p == 0:
            continue
        neg = flagged - hit
        for q in shares:
            k = np.searchsorted(flagged, q * total_n, side="right")
            results[("share", q)].append(hit[k - 1] / total_p if k > 0 else 0.0)
        for f in fprs:
            k = np.searchsorted(neg, f * total_neg, side="right")
            if k >= len(neg):
                raise ValueError("top_share too small for this false-positive budget")
            results[("fpr", f)].append(hit[k - 1] / total_p if k > 0 else 0.0)
    out = {key: tuple(np.percentile(v, [2.5, 97.5])) for key, v in results.items()}
    out["clusters"] = int((n_pos > 0).sum())
    return out
