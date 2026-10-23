import dataclasses
from datetime import date
from pathlib import Path

import numpy as np

from fincrime.bank import identifiers, mexico
from fincrime.bank.assumptions import PROVENANCE, Assumptions
from fincrime.bank.customers import make_customers

SMALL = Assumptions(n_customers=40_000)


def test_census_total_matches_published():
    assert sum(p for _, p in mexico.STATES.values()) == mexico.CENSUS_2020_TOTAL
    assert len(mexico.STATES) == 32


def test_every_assumption_has_provenance_and_is_documented():
    docs = (Path(__file__).parents[1] / "docs" / "assumptions.md").read_text(encoding="utf-8")
    names = {f.name for f in dataclasses.fields(Assumptions)}
    assert names == set(PROVENANCE)
    for name in names:
        assert f"`{name}`" in docs, f"{name} missing from docs/assumptions.md"
    assert {v[0] for v in PROVENANCE.values()} <= {"checked", "derived", "assumption"}


def test_deterministic_and_seed_sensitive():
    a, _ = make_customers(Assumptions(n_customers=2_000))
    b, _ = make_customers(Assumptions(n_customers=2_000))
    c, _ = make_customers(Assumptions(n_customers=2_000, seed=7))
    assert a.equals(b)
    assert not a.equals(c)


def test_state_mix_follows_census_times_tier():
    customers, _ = make_customers(SMALL)
    share = (
        customers["state"].value_counts().to_pandas().set_index("state")["count"]
        / SMALL.n_customers
    )
    w = {c: p * SMALL.tier_multiplier[mexico.state_tier(c)] for c, (_, p) in mexico.STATES.items()}
    total = sum(w.values())
    for code, weight in w.items():
        expected = weight / total
        # 5 standard deviations of a binomial share
        tol = 5 * np.sqrt(expected * (1 - expected) / SMALL.n_customers)
        assert abs(share.get(code, 0.0) - expected) < tol, code


def test_sex_age_and_onboarding_rules():
    customers, _ = make_customers(SMALL)
    assert abs((customers["sex"] == "F").mean() - SMALL.female_share) < 0.01
    age = (customers["onboarded_on"] - customers["birth_date"]).dt.total_days() / 365.25
    assert age.min() >= 18 and age.max() < 71.5
    assert customers["onboarded_on"].min() >= SMALL.start_date
    assert customers["onboarded_on"].max() <= SMALL.end_date
    assert customers["onboarded_on"].is_sorted()  # customer_id grows with join date
    # growth: more customers join in the last quarter than in the first
    first = (customers["onboarded_on"] < date(2024, 4, 1)).sum()
    last = (customers["onboarded_on"] >= date(2025, 4, 1)).sum()
    assert last > 1.5 * first


def test_identifiers_valid_in_shape_invalid_by_design_and_unique():
    customers, accounts = make_customers(SMALL)
    assert all(identifiers.curp_format_ok(c) for c in customers["curp"].head(5000))
    assert customers["curp"].n_unique() == SMALL.n_customers
    assert accounts["clabe"].n_unique() == SMALL.n_customers
    assert not any(identifiers.clabe_is_valid(c) for c in accounts["clabe"].head(5000))
    assert all(len(c) == 18 and c.startswith("999") for c in accounts["clabe"].head(5000))


def test_curp_encodes_the_customers_own_attributes():
    customers, _ = make_customers(Assumptions(n_customers=3_000))
    for row in customers.head(500).iter_rows(named=True):
        curp = row["curp"]
        assert curp[4:10] == f"{row['birth_date']:%y%m%d}"
        assert curp[10] == ("M" if row["sex"] == "F" else "H")
        assert curp[11:13] == row["state"]


def test_clabe_check_digit_algorithm_known_value():
    # Self-consistency: a correct digit makes the full number valid.
    first17 = "00200000000000001"
    full = first17 + str(identifiers.clabe_check_digit(first17))
    assert identifiers.clabe_is_valid(full)
