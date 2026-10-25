"""Claims and exposure built from a small simulated bank, and the as-of rule that protects every estimate."""

from datetime import date, datetime

import numpy as np
import polars as pl
import pytest

from fincrime.risk import backtest, claims, simcheck


@pytest.fixture(scope="module")
def world():
    return simcheck.build_world(seed=11, customers=20_000)


def test_the_same_seed_gives_the_same_claims(world):
    again, _ = simcheck.build_world(seed=11, customers=20_000)
    assert world[0].equals(again)
    other, _ = simcheck.build_world(seed=12, customers=20_000)
    assert not world[0].equals(other)


def test_claims_are_consistent_with_the_episode_and_report_tables(world):
    c, _ = world
    assert c.height > 50
    assert c["amount"].min() > 0
    late = c.filter(pl.col("reported_ts").is_not_null())
    assert (late["reported_ts"] > late["loss_ts"]).all()
    # every claim is one of the two fraud types and has a loss date inside the simulated window
    assert set(c["fraud_type"].unique()) <= set(claims.TYPES)
    assert c["loss_ts"].min() >= datetime(2024, 1, 1) and c["loss_ts"].max() <= datetime(
        2025, 6, 30, 23, 59, 59
    )


def test_visible_is_exactly_what_was_reported_by_the_date(world):
    c, _ = world
    d = date(2024, 12, 31)
    v = claims.visible(c, d)
    assert v["reported_ts"].max() <= datetime(2024, 12, 31, 23, 59, 59, 999999)
    assert (
        v.height
        == c.filter(pl.col("reported_ts") <= datetime(2024, 12, 31, 23, 59, 59, 999999)).height
    )
    assert claims.visible(c, date(2030, 1, 1)).height == c["reported_ts"].is_not_null().sum()


def test_estimates_at_a_date_do_not_depend_on_later_reports(world):
    """The leak guard for the whole IBNR machinery: drop every later report, the numbers are identical."""
    c, expo = world
    d = date(2024, 9, 30)
    cut = c.with_columns(
        pl.when(pl.col("reported_ts") <= datetime(2024, 9, 30, 23, 59, 59))
        .then(pl.col("reported_ts"))
        .otherwise(None)
        .alias("reported_ts")
    )
    a = backtest.estimate(c, expo, d, reps=0)
    b = backtest.estimate(cut, expo, d, reps=0)
    for m in a:
        assert np.allclose(a[m].ibnr_n, b[m].ibnr_n) and np.allclose(
            a[m].ibnr_amt, b[m].ibnr_amt
        ), m


def test_exposure_is_the_mean_of_open_accounts_at_the_month_edges():
    accounts = pl.DataFrame(
        {
            "customer_id": [1, 2, 3, 4],
            "opened_on": [
                date(2024, 1, 5),
                date(2024, 1, 20),
                date(2024, 2, 10),
                date(2024, 2, 11),
            ],
        }
    )
    customers = pl.DataFrame(
        {"customer_id": [1, 2, 3, 4], "fraud_role": [None, None, None, "mule"]},
        schema_overrides={"fraud_role": pl.String},
    )
    e = claims.exposure(accounts, customers, date(2024, 1, 1), date(2024, 2, 1))
    assert e["accounts"].to_list() == [
        1.0,
        2.5,
    ]  # (0+2)/2 then (2+3)/2: the mule is not an exposure
    assert e["opened"].to_list() == [2, 1]


def test_the_calibration_summary_scores_coverage_and_the_pit():
    def row(seed, covered, est):
        return {
            "seed": seed,
            "claims": 1,
            "ibnr": [
                {
                    "method": "M4",
                    "target": "amount",
                    "estimate": est,
                    "truth": 100.0,
                    "lo": 50.0,
                    "hi": 150.0,
                    "covered": covered,
                }
            ],
            "delay": {
                k: {"median": m, "sigma": 0.8, "se_mu": 0.05, "mu": float(np.log(m))}
                for k, (m, _) in simcheck.PLANTED.items()
            },
            "reported_share": {"ato": 0.9, "card_testing": 0.8},
            "loss": {
                "realised": 100.0,
                "point": 100.0,
                "pit": 0.5,
                "lo90": 90.0,
                "hi90": 110.0,
                "covered90": True,
                "el_lo": 95.0,
                "el_hi": 105.0,
            },
        }

    out = simcheck.summarise([row(1, True, 110.0), row(2, False, 90.0)])
    m4 = next(r for r in out["ibnr"] if r["method"] == "M4")
    assert (
        m4["coverage"] == 0.5
        and m4["bias"] == pytest.approx(0.0)
        and m4["mae"] == pytest.approx(0.1)
    )
    assert out["loss"]["coverage_90"] == 1.0 and out["delay"]["ato"]["mu_covered"] == 1.0


def _attempts(rows):
    return pl.DataFrame(
        rows,
        schema={
            "episode_id": pl.Int64,
            "ts": pl.Datetime("us"),
            "amount": pl.Float64,
            "is_fraud": pl.Boolean,
            "crime_class": pl.String,
            "status": pl.String,
            "direction": pl.String,
        },
        orient="row",
    )


def _episodes(lost):
    return pl.DataFrame(
        {"episode_id": [1], "crime_class": ["fraud"], "fraud_type": ["ato"], "lost_amount": [lost]}
    )


def _no_reports():
    return pl.DataFrame(schema={"episode_id": pl.Int64, "reported_ts": pl.Datetime("us")})


def test_a_loss_cut_off_by_the_end_of_the_data_is_tolerated_but_a_mid_window_gap_is_a_bug():
    d = datetime
    # the episode says 300 pesos were lost but only 100 are in the data, and it sits on the last day
    edge = _attempts(
        [
            (1, d(2025, 6, 30, 23, 30), 100.0, True, "fraud", "approved", "out"),
            (2, d(2025, 6, 30, 23, 59), 5.0, False, "genuine", "approved", "out"),
        ]
    )
    out = claims.build_claims(edge, _episodes(300.0), _no_reports())
    assert out["amount"].to_list() == [100.0]  # the bank carries what it can see
    mid = _attempts(
        [
            (1, d(2024, 3, 1, 10), 100.0, True, "fraud", "approved", "out"),
            (2, d(2025, 6, 30, 23, 59), 5.0, False, "genuine", "approved", "out"),
        ]
    )
    with pytest.raises(ValueError, match="disagrees"):
        claims.build_claims(mid, _episodes(300.0), _no_reports())
