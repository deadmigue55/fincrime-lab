"""The multi-bank delayed-label study: its summary, its resume rule, and the completion weights."""

import json

import numpy as np

from fincrime.experiments import labels, outcomes


class _Fit:
    def cdf(self, age):
        return np.clip(np.asarray(age) / 10.0, 0, 1)


def _row(seed, truth, ult, reported, weighted, treatment="tight_all", customers=1000):
    w = list(range(1, 4))
    return {
        "seed": seed,
        "treatment": treatment,
        "customers": customers,
        "weeks": w,
        "true": [truth] * 3,
        "reported": [reported] * 3,
        "weighted": [weighted] * 3,
        "truth_ult": [ult] * 3,
        **{f"truth_{k}": truth for k in w},
    }


def test_summary_scores_each_readout_against_its_own_target():
    rows = [_row(1, -10.0, -8.0, -4.0, -8.0), _row(2, -10.0, -8.0, -6.0, -8.0)]
    s = labels.summarise(rows)["readouts"]
    # reported-only is scored against all losses: -5 against -10 is 50% towards zero
    assert np.allclose(s["reported"]["bias_share"], 0.5)
    # weighted is scored against the losses that will be reported, so it is exact here
    assert np.allclose(s["weighted"]["bias_share"], 0.0)
    # ...and 20% short of all losses, the never-reported gap
    assert np.allclose(s["weighted"]["bias_share_vs_all_losses"], 0.2)


def test_resume_ignores_other_treatments_and_cut_off_lines(tmp_path, monkeypatch):
    out = tmp_path / "labels.jsonl"
    good = _row(1, -1.0, -1.0, -1.0, -1.0)
    other = _row(2, -1.0, -1.0, -1.0, -1.0, treatment="velocity3")
    out.write_text(json.dumps(good) + "\n" + json.dumps(other) + "\n" + '{"seed": 3, "tre', "utf-8")
    ran = []

    def fake(args):
        ran.append(args)
        return _row(args[0], -1.0, -1.0, -1.0, -1.0, args[1], args[2])

    monkeypatch.setattr(labels, "readout_one", fake)
    monkeypatch.setattr(labels, "ProcessPoolExecutor", _Inline)
    rows = labels.run(out, "tight_all", range(1, 4), 1000, 1)
    assert sorted(a[0] for a in ran) == [2, 3]  # seed 2 was another rule, seed 3 was cut off
    assert [r["seed"] for r in rows] == [1, 2, 3]


class _Inline:
    def __init__(self, max_workers):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, items):
        return [fn(i) for i in items]


def test_completion_weight_is_floored_and_by_type():
    fits = {"ato": _Fit(), "card_testing": _Fit()}
    kind = np.array(["ato", "card_testing", "ato"])
    c = outcomes.completion(fits, kind, np.array([5.0, 20.0, 0.0]))
    assert np.allclose(c, [0.5, 1.0, 1e-3])
