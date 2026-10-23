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

    # How customers behave (genuine activity)
    events_per_month: dict[str, float] = field(
        default_factory=lambda: {
            "spei_out": 4.0,
            "spei_in": 2.5,
            "card_pos": 9.0,
            "card_online": 3.5,
            "cash_in": 0.8,
            "atm": 1.2,
            "codi": 0.4,
            "bill_pay": 1.0,
        }
    )  # mean count per active month for an average customer
    amount_median_frac: dict[str, float] = field(
        default_factory=lambda: {
            "spei_out": 0.08,
            "spei_in": 0.07,
            "card_pos": 0.012,
            "card_online": 0.02,
            "cash_in": 0.10,
            "atm": 0.04,
            "codi": 0.01,
            "bill_pay": 0.05,
        }
    )  # median event amount as a fraction of the customer's monthly income
    amount_sigma: dict[str, float] = field(
        default_factory=lambda: {
            "spei_out": 1.2,
            "spei_in": 1.2,
            "card_pos": 0.9,
            "card_online": 1.0,
            "cash_in": 0.8,
            "atm": 0.6,
            "codi": 0.8,
            "bill_pay": 0.7,
        }
    )  # spread (sigma of the log) of event amounts
    income_dispersion: float = 0.35  # sigma of log income around the band median
    salaried_share: dict[str, float] = field(
        default_factory=lambda: {
            "low": 0.35,
            "lower_mid": 0.55,
            "mid": 0.70,
            "upper_mid": 0.75,
            "high": 0.70,
        }
    )  # share of customers whose pay lands in the account, by income band
    biweekly_pay_share: float = 0.60  # paid on the 15th and last day; the rest monthly
    activity_shape: float = 1.5  # gamma shape of customer-level activity (lower = more unequal)
    dormant_share: float = 0.12  # customers who barely use the account
    dormant_activity: float = 0.10  # their activity relative to an average customer
    internal_transfer_share: float = 0.25  # outgoing transfers that stay inside the bank
    second_device_use: float = 0.15  # app events made from the second device, if there is one
    n_external_banks: int = 20  # anonymised counterparty banks, Zipf-weighted
    weekday_weights: tuple[float, ...] = (0.9, 0.9, 0.95, 1.0, 1.2, 1.25, 1.0)  # Monday first
    payday_boost: float = 1.4  # spending weight on the 1st-2nd, 15th-17th and last 2 days
    mcc_mix_pos: dict[str, float] = field(
        default_factory=lambda: {
            "grocery": 0.30,
            "restaurant": 0.20,
            "transport": 0.12,
            "fuel": 0.08,
            "pharmacy": 0.06,
            "retail": 0.14,
            "entertainment": 0.05,
            "other": 0.05,
        }
    )
    mcc_mix_online: dict[str, float] = field(
        default_factory=lambda: {
            "marketplace": 0.35,
            "subscriptions": 0.15,
            "food_delivery": 0.15,
            "travel": 0.08,
            "digital_goods": 0.10,
            "utilities": 0.05,
            "other": 0.12,
        }
    )


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
    "events_per_month": (
        "assumption",
        "no public customer-level data; order of magnitude of an active digital account",
    ),
    "amount_median_frac": (
        "assumption",
        "tuned so about 94% of transfers are under 10,000 pesos, the SPEI share (to verify)",
    ),
    "amount_sigma": (
        "assumption",
        "log-normal spread; heavier for transfers than for card payments",
    ),
    "income_dispersion": ("assumption", "illustrative; no public customer-level data"),
    "salaried_share": (
        "assumption",
        "ENIF shows payroll accounts are the most common type (36.2%) but gives no split by income",
    ),
    "biweekly_pay_share": (
        "assumption",
        "quincenal pay is common in Mexico; no source for the share",
    ),
    "activity_shape": ("assumption", "a few heavy users and many light ones"),
    "dormant_share": ("assumption", "neobanks have many barely used accounts"),
    "dormant_activity": ("assumption", "illustrative; no public customer-level data"),
    "internal_transfer_share": ("assumption", "illustrative; no public customer-level data"),
    "second_device_use": ("assumption", "illustrative; no public customer-level data"),
    "n_external_banks": ("assumption", "banks are anonymised B01..B20 on purpose"),
    "weekday_weights": ("assumption", "spending peaks on Friday and Saturday"),
    "payday_boost": ("assumption", "spending and cash use rise around pay days"),
    "mcc_mix_pos": ("assumption", "merchant category mix of in-person card payments"),
    "mcc_mix_online": ("assumption", "merchant category mix of online card payments"),
}
