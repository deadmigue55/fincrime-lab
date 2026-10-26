"""The reserving methods against known answers, before they touch a single claim of the bank."""

from datetime import date, datetime

import numpy as np
import polars as pl
import pytest

from fincrime.risk import triangle as tri

# Taylor and Ashe (1983), the triangle every reserving package ships as ``GenIns``. The reference
# figures below (chain-ladder reserve 18,680,856; Mack standard errors by cohort and 2,447,095 in
# total) were checked against R's ChainLadder 0.2.21, MackChainLadder(GenIns, est.sigma = "Mack"),
# on 2026-10-25. An earlier version of this test used 2,447,618 from memory with a 0.1% tolerance,
# which hid the difference; that figure was wrong.
TAYLOR_ASHE = [
    [357848, 1124788, 1735330, 2218270, 2745596, 3319994, 3466336, 3606286, 3833515, 3901463],
    [352118, 1236139, 2170033, 3353322, 3799067, 4120063, 4647867, 4914039, 5339085],
    [290507, 1292306, 2218525, 3235179, 3985995, 4132918, 4628910, 4909315],
    [310608, 1418858, 2195047, 3757447, 4029929, 4381982, 4588268],
    [443160, 1136350, 2128333, 2897821, 3402672, 3873311],
    [396132, 1333217, 2180715, 2985752, 3691712],
    [440832, 1288463, 2419861, 3483130],
    [359480, 1421128, 2864498],
    [376686, 1363294],
    [344014],
]


def _cum() -> np.ndarray:
    n = len(TAYLOR_ASHE)
    c = np.full((n, n), np.nan)
    for i, row in enumerate(TAYLOR_ASHE):
        c[i, : len(row)] = row
    return c


def test_chain_ladder_reproduces_the_published_taylor_ashe_reserve():
    cl = tri.chain_ladder(_cum(), tail=1.0)
    assert cl.ibnr.sum() == pytest.approx(18_680_856, abs=1)
    assert cl.factors[0] == pytest.approx(3.4906, abs=1e-4)  # the first published age-to-age factor


def test_mack_standard_error_matches_the_published_value():
    se, total = tri.mack(_cum())
    assert [round(x) for x in se[1:]] == [
        75535,
        121699,
        133549,
        261406,
        411010,
        558317,
        875328,
        971258,
        1363155,
    ]
    assert total == pytest.approx(2_447_095, abs=1)
    assert se[0] == 0.0


def test_odp_bootstrap_is_centred_on_chain_ladder_with_a_plausible_spread():
    c = _cum()
    incr = np.where(np.isnan(c), np.nan, np.diff(c, axis=1, prepend=0.0))
    reserve = tri.chain_ladder(c, tail=1.0).ibnr.sum()
    draws = tri.odp_bootstrap(incr, reps=1500, seed=3, tail=1.0).sum(axis=1)
    assert np.mean(draws) == pytest.approx(reserve, rel=0.06)
    # England and Verrall (2002) give a prediction error near 2.9 million for it (recalled, not re-checked)
    assert 1.8e6 < np.std(draws) < 3.8e6


def test_tail_factor_is_one_without_a_decay_and_above_one_with_one():
    assert tri.tail_factor(np.array([5.0, 1.0, 1.0, 1.0])) == 1.0
    f = np.array([4.0, 2.0, 1.5, 1.2, 1.1, 1.05])
    assert tri.tail_factor(f) > 1.0
    assert (
        tri.tail_factor(np.array([1.0 + 0.1 * j for j in range(1, 6)])) == 1.0
    )  # growing: no tail


def _claims(rows):
    return pl.DataFrame(
        rows,
        schema={
            "episode_id": pl.Int64,
            "fraud_type": pl.String,
            "loss_ts": pl.Datetime("us"),
            "amount": pl.Float64,
            "reported_ts": pl.Datetime("us"),
        },
        orient="row",
    )


def test_triangle_cells_and_the_as_of_cut_by_hand():
    d = datetime
    claims = _claims(
        [
            (1, "ato", d(2024, 1, 10), 100.0, d(2024, 1, 20)),  # cohort 0, lag 0
            (2, "ato", d(2024, 1, 25), 50.0, d(2024, 2, 3)),  # cohort 0, lag 1
            (3, "card_testing", d(2024, 2, 5), 10.0, d(2024, 3, 9)),  # cohort 1, lag 1
            (
                4,
                "card_testing",
                d(2024, 2, 6),
                20.0,
                d(2024, 4, 2),
            ),  # reported after the as-of: unseen
            (5, "ato", d(2024, 2, 8), 7.0, None),  # never reported: unseen
            (
                6,
                "ato",
                d(2024, 1, 12),
                5.0,
                d(2024, 1, 22),
            ),  # same cell as claim 1: counts must add
        ]
    )
    t = tri.build_triangle(claims, date(2024, 3, 31), "count")
    assert t.n == 3
    expect = np.array([[2, 1, 0], [0, 1, np.nan], [0, np.nan, np.nan]])
    assert np.array_equal(t.incr, expect, equal_nan=True)
    pesos = tri.build_triangle(claims, date(2024, 3, 31), "amount", fraud_type="ato")
    assert pesos.incr[0, 0] == 105.0 and pesos.incr[0, 1] == 50.0 and pesos.incr[1, 0] == 0.0
    with pytest.raises(ValueError):
        tri.build_triangle(claims, date(2024, 3, 30))


def test_truncating_the_future_cannot_change_the_triangle():
    """The leak guard: removing every report after the as-of date leaves the triangle identical."""
    rng = np.random.default_rng(0)
    base = datetime(2024, 1, 1)
    loss = [
        base.replace(day=1) + (datetime(2024, 6, 28) - base) * float(u) for u in rng.random(300)
    ]
    rows = [
        (
            i,
            "ato" if i % 2 else "card_testing",
            t,
            10.0 + i,
            t + (datetime(2024, 9, 1) - t) * float(rng.random()),
        )
        for i, t in enumerate(loss)
    ]
    full = _claims(rows)
    as_of = date(2024, 6, 30)
    cut = full.filter(pl.col("reported_ts") <= datetime(2024, 6, 30, 23, 59, 59))
    a = tri.build_triangle(full, as_of, "amount").incr
    b = tri.build_triangle(cut, as_of, "amount").incr
    assert np.array_equal(a, b, equal_nan=True)


def test_bornhuetter_ferguson_is_a_weighted_blend_of_data_and_prior():
    latest = np.array([100.0, 40.0])
    cdf = np.array([1.0, 2.0])  # cohort 1 has reported half of its ultimate
    prior = np.array([100.0, 200.0])
    bf = tri.bornhuetter_ferguson(latest, cdf, prior)
    assert bf.tolist() == [100.0, 40.0 + 200.0 * 0.5]
