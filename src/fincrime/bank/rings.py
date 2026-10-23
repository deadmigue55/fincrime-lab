"""Mule rings: groups of accounts opened with synthetic identities.

Each ring shares a few phones (the same ``device_id`` appears on all its accounts), opens its
accounts within a few days of each other, and gives each a CURP whose date of birth often
disagrees with the declared one. These are the *observable* weak signals a detector can learn;
which accounts are mules (``fraud_role``, ``ring_id``) is latent.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.customers import rng_for
from fincrime.bank.identifiers import make_clabe, make_curp
from fincrime.bank.mexico import STATES

RING_DEVICE_BASE = 7_000_000_000  # ring devices: base + ring_id * 10 + k
ATTACKER_DEVICE_BASE = 8_000_000_000  # attacker devices used on victims' accounts


def ring_device(ring_id: int, k: int) -> int:
    """The ``k``-th shared phone of a ring (``k`` starting at 0)."""
    return RING_DEVICE_BASE + ring_id * 10 + k


def make_rings(
    a: Assumptions, customers: pl.DataFrame, accounts: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Return ``(customers, accounts, rings)`` with mule accounts appended.

    Genuine customers keep ids ``1..n``; mule ids continue from ``n + 1``. The customer table
    gains the latent columns ``fraud_role`` and ``ring_id`` (null for genuine customers).
    """
    rng = rng_for(a.seed, "rings")
    n = customers.height
    n_rings = round(a.mule_rings_per_100k * n / 100_000)
    base_c = customers.with_columns(
        fraud_role=pl.lit(None, dtype=pl.String), ring_id=pl.lit(None, dtype=pl.Int64)
    )
    if n_rings == 0:
        empty = pl.DataFrame(
            schema={"ring_id": pl.Int64, "n_accounts": pl.Int64, "formed_on": pl.Date}
        )
        return base_c, accounts, empty

    days = (a.end_date - a.start_date).days + 1
    codes = np.array(list(STATES))
    ring_rows, mule_rows = [], []
    next_id = n + 1
    for ring_id in range(1, n_rings + 1):
        size = int(rng.integers(a.ring_size_range[0], a.ring_size_range[1] + 1))
        # rings form in the first 70% of the window so they have time to operate
        formed = int(rng.integers(0, max(1, int(days * 0.7))))
        home = codes[rng.integers(0, len(codes))]
        for _ in range(size):
            mule_rows.append((next_id, ring_id, formed + int(rng.integers(0, 4)), home))
            next_id += 1
        ring_rows.append((ring_id, size, formed))

    m = len(mule_rows)
    ids = np.array([r[0] for r in mule_rows])
    ring_of = np.array([r[1] for r in mule_rows])
    onboard = np.datetime64(a.start_date, "D") + np.array([r[2] for r in mule_rows]).astype(
        "timedelta64[D]"
    )
    home = np.array([r[3] for r in mule_rows])
    sex = np.where(rng.random(m) < 0.5, "F", "M")
    age = rng.integers(19, 36, size=m)  # synthetic identities skew young
    birth = onboard - (age * 365.25).astype(int).astype("timedelta64[D]")
    # the CURP often encodes a different date of birth than the one declared
    mismatch = rng.random(m) < 0.6
    curp_birth = np.where(
        mismatch, birth + rng.integers(30, 3000, size=m).astype("timedelta64[D]"), birth
    )
    curp = make_curp(rng, curp_birth.astype("datetime64[D]").astype(object), sex, home)

    mules = pl.DataFrame(
        {
            "customer_id": ids,
            "curp": curp,
            "state": home,
            "sex": sex,
            "birth_date": birth,
            "income_band": np.full(m, "low"),
            "n_devices": np.ones(m, dtype=np.int8),
            "onboarded_on": onboard,
            "fraud_role": np.full(m, "mule"),
            "ring_id": ring_of,
        }
    )
    plaza = np.array([list(STATES).index(s) + 1 for s in home])
    mule_accounts = pl.DataFrame(
        {
            "account_id": ids,
            "customer_id": ids,
            "clabe": make_clabe(ids, plaza),
            "opened_on": onboard,
        }
    )
    rings = pl.DataFrame(
        ring_rows, schema=["ring_id", "n_accounts", "formed_on"], orient="row"
    ).with_columns(formed_on=pl.lit(a.start_date) + pl.duration(days=pl.col("formed_on")))
    all_customers = pl.concat([base_c, mules], how="vertical").cast(
        {"n_devices": pl.Int8, "ring_id": pl.Int64}
    )
    return all_customers, pl.concat([accounts, mule_accounts], how="vertical"), rings
