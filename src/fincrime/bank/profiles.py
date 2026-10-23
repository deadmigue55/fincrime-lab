"""Latent behaviour profile of each customer.

These are the hidden drivers of genuine activity (true income, whether pay lands in the
account, how active the customer is). They are **latent**: a detector sees only what the bank
would see (declared income band, transactions), never these columns.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.customers import rng_for


def make_profiles(a: Assumptions, customers: pl.DataFrame) -> pl.DataFrame:
    """One row per customer: true monthly income, payroll flag and pay frequency, activity."""
    rng = rng_for(a.seed, "profiles")
    n = customers.height
    median = {name: m for name, _, m in a.income_bands}
    base = customers["income_band"].replace_strict(median, return_dtype=pl.Float64).to_numpy()
    income = base * np.exp(rng.normal(0.0, a.income_dispersion, n))

    p_sal = customers["income_band"].replace_strict(a.salaried_share, return_dtype=pl.Float64)
    salaried = rng.random(n) < p_sal.to_numpy()
    biweekly = salaried & (rng.random(n) < a.biweekly_pay_share)

    activity = rng.gamma(a.activity_shape, 1.0 / a.activity_shape, n)  # mean 1
    dormant = rng.random(n) < a.dormant_share
    activity = np.where(dormant, a.dormant_activity, activity)

    return pl.DataFrame(
        {
            "customer_id": customers["customer_id"],
            "monthly_income": income.round(2),
            "salaried": salaried,
            "biweekly": biweekly,
            "activity": activity,
            "dormant": dormant,
        }
    )
