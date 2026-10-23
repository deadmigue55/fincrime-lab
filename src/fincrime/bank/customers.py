"""Customers and accounts of the synthetic bank.

One account per customer (a documented simplification). Customers join over the simulated
calendar with growing monthly volume, so the bank "launches" on ``start_date``.
"""

from __future__ import annotations

import zlib

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.identifiers import make_clabe, make_curp
from fincrime.bank.mexico import STATES, state_tier


def rng_for(seed: int, name: str) -> np.random.Generator:
    """A reproducible random stream per component, so adding one never shifts another."""
    return np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(name.encode())]))


def _onboarding_offsets(
    rng: np.random.Generator, n: int, days: int, monthly_growth: float
) -> np.ndarray:
    """Day offsets from the start date, density growing exponentially at ``monthly_growth``."""
    g = np.log1p(monthly_growth) / 30.4375  # per day
    if g == 0:
        return rng.integers(0, days, size=n)
    u = rng.random(n)
    t = np.log1p(u * np.expm1(g * days)) / g
    return np.minimum(t.astype(int), days - 1)


def make_customers(a: Assumptions) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return ``(customers, accounts)`` for the given assumptions. Deterministic in ``a.seed``."""
    rng = rng_for(a.seed, "customers")
    n = a.n_customers
    codes = np.array(list(STATES))

    weights = np.array(
        [STATES[c][1] * a.tier_multiplier[state_tier(c)] for c in codes], dtype=float
    )
    state = codes[rng.choice(len(codes), size=n, p=weights / weights.sum())]
    sex = np.where(rng.random(n) < a.female_share, "F", "M")

    days = (a.end_date - a.start_date).days + 1
    offset = _onboarding_offsets(rng, n, days, a.onboarding_monthly_growth)
    onboarded = np.datetime64(a.start_date, "D") + offset.astype("timedelta64[D]")

    band_p = np.array([b[2] for b in a.age_bands])
    band = rng.choice(len(a.age_bands), size=n, p=band_p / band_p.sum())
    lo = np.array([b[0] for b in a.age_bands])[band]
    hi = np.array([b[1] for b in a.age_bands])[band]
    age = rng.integers(lo, hi + 1)  # age at onboarding, 18 to 70
    birth = onboarded - np.ceil(age * 365.25).astype(int).astype("timedelta64[D]")  # never under 18
    birth = birth - rng.integers(0, 365, size=n).astype("timedelta64[D]")

    inc_p = np.array([b[1] for b in a.income_bands])
    income = np.array([b[0] for b in a.income_bands])[
        rng.choice(len(a.income_bands), size=n, p=inc_p / inc_p.sum())
    ]
    n_devices = np.where(rng.random(n) < a.second_device_share, 2, 1)

    birth_py = birth.astype("datetime64[D]").astype(object)
    curp = make_curp(rng, birth_py, sex, state)
    for _ in range(20):  # re-draw the random part of any duplicate until all are unique
        _, first = np.unique(curp, return_index=True)
        dup = np.ones(n, dtype=bool)
        dup[first] = False
        if not dup.any():
            break
        curp[dup] = make_curp(rng, birth_py[dup], sex[dup], state[dup])

    order = np.argsort(onboarded, kind="stable")  # customer_id grows with join date
    customer_id = np.arange(1, n + 1)
    customers = pl.DataFrame(
        {
            "customer_id": customer_id,
            "curp": curp[order],
            "state": state[order],
            "sex": sex[order],
            "birth_date": birth[order],
            "income_band": income[order],
            "n_devices": n_devices[order].astype(np.int8),
            "onboarded_on": onboarded[order],
        }
    )

    plaza = np.array([list(STATES).index(s) + 1 for s in state[order]])
    accounts = pl.DataFrame(
        {
            "account_id": customer_id,
            "customer_id": customer_id,
            "clabe": make_clabe(customer_id, plaza),
            "opened_on": onboarded[order],
        }
    )
    return customers, accounts
