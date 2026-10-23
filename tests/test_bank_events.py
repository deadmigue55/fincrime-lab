import numpy as np
import polars as pl
import pytest

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.customers import make_customers
from fincrime.bank.events import EVENT_SCHEMA, MIRROR_OFFSET, iter_genuine_events
from fincrime.bank.profiles import make_profiles

A = Assumptions(n_customers=6_000)


@pytest.fixture(scope="module")
def world():
    customers, _ = make_customers(A)
    profiles = make_profiles(A, customers)
    events = pl.concat(list(iter_genuine_events(A, customers, profiles)))
    return customers, profiles, events


def test_schema_and_unique_ids(world):
    _, _, ev = world
    assert dict(ev.schema) == EVENT_SCHEMA
    assert ev["event_id"].n_unique() == ev.height
    assert ev["amount"].min() >= 10.0
    assert ev["is_fraud"].sum() == 0  # genuine layer only


def test_deterministic():
    customers, _ = make_customers(Assumptions(n_customers=800))
    profiles = make_profiles(Assumptions(n_customers=800), customers)
    a = pl.concat(list(iter_genuine_events(Assumptions(n_customers=800), customers, profiles)))
    b = pl.concat(list(iter_genuine_events(Assumptions(n_customers=800), customers, profiles)))
    assert a.equals(b)


def test_no_event_before_onboarding_or_outside_calendar(world):
    customers, _, ev = world
    joined = ev.join(
        customers.select("customer_id", "onboarded_on"),
        left_on="account_id",
        right_on="customer_id",
    )
    assert (joined["ts"].dt.date() >= joined["onboarded_on"]).all()
    assert ev["ts"].dt.date().min() >= A.start_date
    assert ev["ts"].dt.date().max() <= A.end_date


def test_internal_counterparties_exist_and_were_onboarded(world):
    customers, _, ev = world
    internal = ev.filter(pl.col("counterparty_account").is_not_null())
    assert internal.height > 0
    assert (internal["counterparty_account"] != internal["account_id"]).all()
    onboard = customers.select(
        pl.col("customer_id").alias("counterparty_account"), pl.col("onboarded_on").alias("cp_onb")
    )
    joined = internal.join(onboard, on="counterparty_account")
    assert joined.height == internal.height
    assert (joined["ts"].dt.date() >= joined["cp_onb"]).all()


def test_double_entry_for_internal_transfers(world):
    _, _, ev = world
    out = ev.filter(
        (pl.col("event_type") == "spei_out") & pl.col("counterparty_account").is_not_null()
    )
    inn = ev.filter(pl.col("event_id") >= MIRROR_OFFSET)
    assert out.height == inn.height
    pair = out.with_columns(mirror_id=pl.col("event_id") + MIRROR_OFFSET).join(
        inn, left_on="mirror_id", right_on="event_id", suffix="_in"
    )
    assert pair.height == out.height
    assert (pair["amount"] == pair["amount_in"]).all()
    assert (pair["ts"] == pair["ts_in"]).all()
    assert (pair["account_id"] == pair["counterparty_account_in"]).all()
    assert (pair["counterparty_account"] == pair["account_id_in"]).all()
    assert (pair["event_type_in"] == "spei_in").all()


def test_payroll_only_for_salaried_on_pay_days(world):
    _, profiles, ev = world
    pay = ev.filter(pl.col("event_type") == "payroll").join(
        profiles, left_on="account_id", right_on="customer_id"
    )
    assert pay.height > 0
    assert pay["salaried"].all()
    days = pay["ts"].dt.day()
    last = pay["ts"].dt.month_end().dt.day()
    assert ((days == 15) | (days == last)).all()
    assert (pay.filter(pl.col("ts").dt.day() == 15)["biweekly"]).all()


def test_devices_belong_to_the_account_and_atm_amounts_are_round(world):
    _, _, ev = world
    dev = ev.filter(pl.col("device_id").is_not_null())
    assert ((dev["device_id"] // 10) == dev["account_id"]).all()
    assert ev.filter(pl.col("channel").is_in(["atm", "card_pos"]))["device_id"].is_null().all()
    atm = ev.filter(pl.col("event_type") == "atm")["amount"]
    assert ((atm % 100) == 0).all() and atm.min() >= 100


def test_calibration_to_published_aggregates(world):
    _, _, ev = world
    spei = ev.filter(pl.col("event_type") == "spei_out")["amount"]
    # SPEI: about 94% of operations are under 10,000 pesos (to verify against Banxico tables)
    assert 0.92 <= (spei <= 10_000).mean() <= 0.97
    out = ev.filter(pl.col("event_type") == "spei_out")
    assert abs(out["counterparty_account"].is_not_null().mean() - A.internal_transfer_share) < 0.02


def test_activity_is_unequal_and_dormant_accounts_are_quiet(world):
    _, profiles, ev = world
    per = (
        ev.group_by("account_id").len().join(profiles, left_on="account_id", right_on="customer_id")
    )
    dormant = per.filter(pl.col("dormant"))["len"].median()
    active = per.filter(~pl.col("dormant"))["len"].median()
    assert dormant < 0.4 * active
    top10 = per.sort("len", descending=True).head(per.height // 10)["len"].sum() / per["len"].sum()
    assert top10 > 0.25  # heavy users exist
    assert np.isfinite(top10)
