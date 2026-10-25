"""Helpers behind the step 6 report: hand-checked toys, so a wrong number cannot hide in a figure."""

import numpy as np
import polars as pl

from fincrime.fraud import baf, report


def test_cost_curve_and_best_share_on_a_toy():
    # four events, scores 4 > 3 > 2 > 1; the first and third are fraud of 100 and 50 pesos
    y = np.array([True, False, True, False])
    amount = np.array([100.0, 10.0, 50.0, 10.0])
    score = np.array([4.0, 3.0, 2.0, 1.0])
    cost, loss = report.cost_curve(y, amount, score, review=1.0, friction=5.0)
    assert loss == 150.0
    # flag 1: review 1 + miss 50 = 51; flag 2: 2 + 5 friction + 50 = 57; flag 3: 3 + 5 + 0 = 8; flag 4: 4 + 10 = 14
    assert cost.tolist() == [51.0, 57.0, 8.0, 14.0]
    # with the default unit costs (15 and 40) flagging only the top event is cheapest: 65, 120, 85, 140
    assert report.best_share(y, amount, score, scale=1.0, max_share=1.0) == 0.25


def test_account_costs_add_up_to_the_curve():
    y = np.array([True, False, True, False])
    amount = np.array([100.0, 10.0, 50.0, 10.0])
    flagged = np.array([True, True, True, False])
    acc = np.array([0, 0, 1, 1])
    per = report.account_costs(acc, y, amount, flagged, review=1.0, friction=5.0)
    assert per.sum() == 8.0  # the same 8 as flagging the top 3 above
    assert per.tolist() == [1 + 1 + 5, 1.0]


def test_psi_is_zero_for_the_same_distribution_and_large_for_a_shift():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 50_000)
    w = np.ones(len(ref))
    assert report.psi(ref, w, rng.normal(0, 1, 50_000)) < 0.01
    assert report.psi(ref, w, rng.normal(1.5, 1, 50_000)) > 0.5
    # weights act like repetition: a 10x weight on the upper half is the same as shifting the reference
    w2 = np.where(ref > 0, 10.0, 1.0)
    assert report.psi(ref, w2, rng.normal(0, 1, 50_000)) > 0.1


def test_group_table_recovers_planted_error_rates():
    """Two groups with a known 1% vs 3% false-alarm rate; intervals must cover the truth."""
    rng = np.random.default_rng(1)
    n = 200_000
    g = rng.choice(["a", "b"], n)
    y = rng.random(n) < 0.002
    flagged = rng.random(n) < np.where(g == "a", 0.01, 0.03)
    df = pl.DataFrame({"account_id": rng.integers(0, 20_000, n), "grp": g})
    rows = {r["value"]: r for r in report.group_table(df, flagged, y, reps=200, groups=("grp",))}
    assert rows["a"]["fa_lo"] < 0.01 < rows["a"]["fa_hi"]
    assert rows["b"]["fa_lo"] < 0.03 < rows["b"]["fa_hi"]
    assert rows["a"]["false_alarm_rate"] < rows["b"]["false_alarm_rate"]


def test_baf_design_puts_categories_first_and_turns_sentinels_into_missing():
    row = {c: 1 for c in baf.CATEGORICAL} | {
        "customer_age": 40,
        "bank_months_count": -1,
        "income": 0.5,
    }
    df = pl.DataFrame(
        [
            {
                **row,
                "payment_type": "AA",
                "fraud_bool": 0,
                "month": 0,
                "variant": "Base",
                "device_fraud_count": 0,
                "row": 0,
            }
        ]
    )
    x, vocab = baf.design(df)
    assert x.shape[1] == len(baf.CATEGORICAL) + 3
    assert vocab["payment_type"] == ["AA"] and x[0, 0] == 0
    names = [c for c in df.columns if c not in (*baf.CATEGORICAL, *baf.DROP, "row")]
    assert np.isnan(x[0, len(baf.CATEGORICAL) + names.index("bank_months_count")])
