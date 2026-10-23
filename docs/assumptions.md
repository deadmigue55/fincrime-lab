# Assumptions of the Banco Sintético

Nothing here describes a real institution. Each parameter has a status:

- **checked**: read in a primary source
- **derived**: computed from a checked number
- **assumption**: our choice; no source. Results are reported with a sensitivity to it.

The same list lives in code (`src/fincrime/bank/assumptions.py`, `PROVENANCE`) and a test fails if
a parameter is missing from this page.

## Run

| Parameter | Value | Status | Note |
|---|---|---|---|
| `seed` | 2026 | assumption | Same seed and assumptions give identical data |
| `n_customers` | 50,000 | assumption | Scale knob; tests use hundreds, full builds hundreds of thousands |
| `start_date` | 2024-01-01 | assumption | The bank launches on this day with no customers |
| `end_date` | 2025-06-30 | assumption | 18 months |

## Customers

| Parameter | Value | Status | Note |
|---|---|---|---|
| `female_share` | 0.463 | derived | ENIF 2024: 58.6% of women and 68.0% of men hold a formal savings account, so among account holders women are 58.6 / (58.6 + 68.0). Assumes equal numbers of women and men aged 18-70 |
| `tier_multiplier` | high 1.25, mid 1.0, low 0.7 | assumption | State weights are census population (checked) times this tier multiplier. ENIF reports regional gaps in inclusion but the press release has no state table |
| `age_bands` | 18-24: 20%, 25-34: 34%, 35-44: 24%, 45-54: 14%, 55-70: 8% | assumption | A young, app-first customer base. ENIF: 69.1% of account holders use a phone app, 10.3% hold an account opened via internet or a non-bank app |
| `income_bands` | low 30%, lower_mid 32%, mid 23%, upper_mid 11%, high 4%; medians 6k, 10k, 18k, 35k, 80k MXN a month | assumption | Illustrative |
| `onboarding_monthly_growth` | 4% | assumption | New accounts per month grow 4% a month |
| `second_device_share` | 20% | assumption | No public figure |

State of residence uses the 2020 census (INEGI, 126,014,024 people; checked). The adoption tiers
(high: CDMX, NL, BC, BCS, Sonora, Chihuahua, Coahuila, Querétaro, Aguascalientes, Jalisco, Quintana
Roo, Tamaulipas; low: Chiapas, Oaxaca, Guerrero, Veracruz, Tabasco, Michoacán, Puebla, Hidalgo) are
an assumption.

## Identifiers

CURP and CLABE are generated in the real format with deliberately wrong check digits, and the
CLABE bank code `999` and the external banks are synthetic, so no identifier can match a real
person or institution.

## Simplifications to keep in mind

- One account per customer.
- The bank launches at `start_date`, so the first months are small.
- Customers are 18 to 70 at onboarding, the ENIF age range.
