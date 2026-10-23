"""Every parameter of the synthetic bank, with where it comes from.

Nothing here describes a real institution. ``PROVENANCE`` tags each field as ``checked`` (read
in a primary source), ``derived`` (computed from a checked number) or ``assumption`` (our
choice, no source). ``docs/assumptions.md`` must list every field; a test enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Assumptions:
    """Parameters of the Banco Sintético. Frozen: change by building a new instance."""

    # Run
    seed: int = 2026
    n_customers: int = 50_000
    start_date: date = date(2024, 1, 1)
    end_date: date = date(2025, 6, 30)

    # Who the customers are
    female_share: float = 0.463  # 58.6 / (58.6 + 68.0), ENIF 2024 account holding by sex
    tier_multiplier: dict[str, float] = field(
        default_factory=lambda: {"high": 1.25, "mid": 1.0, "low": 0.7}
    )
    age_bands: tuple[tuple[int, int, float], ...] = (
        (18, 24, 0.20),
        (25, 34, 0.34),
        (35, 44, 0.24),
        (45, 54, 0.14),
        (55, 70, 0.08),
    )
    income_bands: tuple[tuple[str, float, float], ...] = (
        # name, share of customers, median monthly income in MXN
        ("low", 0.30, 6_000.0),
        ("lower_mid", 0.32, 10_000.0),
        ("mid", 0.23, 18_000.0),
        ("upper_mid", 0.11, 35_000.0),
        ("high", 0.04, 80_000.0),
    )

    # When they join
    onboarding_monthly_growth: float = 0.04  # new accounts per month grow 4% a month
    second_device_share: float = 0.20  # customers who use a second phone or tablet


PROVENANCE: dict[str, tuple[str, str]] = {
    "seed": ("assumption", "any fixed integer; same seed and assumptions give identical data"),
    "n_customers": (
        "assumption",
        "scale knob; tests use hundreds, full builds hundreds of thousands",
    ),
    "start_date": ("assumption", "start of the simulated calendar"),
    "end_date": ("assumption", "end of the simulated calendar"),
    "female_share": (
        "derived",
        "ENIF 2024: 58.6% of women and 68.0% of men hold a formal savings account",
    ),
    "tier_multiplier": (
        "assumption",
        "state adoption tiers; ENIF shows regional gaps but gives no state table here",
    ),
    "age_bands": (
        "assumption",
        "young, app-first mix; ENIF says 69.1% of account holders use a phone app",
    ),
    "income_bands": ("assumption", "illustrative five-band split and medians"),
    "onboarding_monthly_growth": ("assumption", "a growing neobank, not a mature one"),
    "second_device_share": ("assumption", "no public figure"),
}
