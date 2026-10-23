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

## Genuine activity

Counts, amounts and timing of what customers really do, before any fraud. Nothing below has a
public customer-level source; the one calibrated outcome is the SPEI amount mix.

| Parameter | Value | Status | Note |
|---|---|---|---|
| `events_per_month` | spei_out 4.0, spei_in 2.5, card_pos 9.0, card_online 3.5, cash_in 0.8, atm 1.2, codi 0.4, bill_pay 1.0 | assumption | Mean count per active month for an average customer. ENIF shows app use rising (69.1%) and ATM use falling, which motivates a card/app-heavy mix. CoDi is low because only 12.8% of people who know it use it |
| `amount_median_frac` | spei_out 8%, spei_in 7%, card_pos 1.2%, card_online 2%, cash_in 10%, atm 4%, codi 1%, bill_pay 5% of monthly income | assumption | Median event amount relative to the customer's true monthly income |
| `amount_sigma` | spei 1.2, card_pos 0.9, card_online 1.0, cash_in 0.8, atm 0.6, codi 0.8, bill_pay 0.7 | assumption | Spread of the log of amounts. The SPEI value is set so about 94% of transfers fall under 10,000 pesos, the system-wide share reported for 2025 (to verify against Banxico). The synthetic bank lands at about 94.6% |
| `income_dispersion` | 0.35 | assumption | Spread of true income around the band median |
| `salaried_share` | low 35%, lower_mid 55%, mid 70%, upper_mid 75%, high 70% | assumption | Customers whose pay lands in the account. ENIF: payroll or pension accounts are the most common account type (36.2% of people), with no split by income |
| `biweekly_pay_share` | 60% | assumption | Paid half on the 15th and half on the last day; the rest are paid monthly on the last day |
| `activity_shape` | 1.5 | assumption | Gamma shape of customer activity (mean 1): a few heavy users, many light ones |
| `dormant_share` | 12% | assumption | Customers who barely use the account |
| `dormant_activity` | 10% | assumption | Their activity relative to an average customer |
| `internal_transfer_share` | 25% | assumption | Outgoing transfers that stay inside the bank; they create a matching incoming event |
| `second_device_use` | 15% | assumption | App events made from the second device, for customers who have one |
| `n_external_banks` | 20 | assumption | Counterparty banks are anonymised `B01`..`B20` with Zipf weights, so none is a real institution |
| `weekday_weights` | Mon 0.9, Tue 0.9, Wed 0.95, Thu 1.0, Fri 1.2, Sat 1.25, Sun 1.0 | assumption | Day-of-week weight for non-payroll activity |
| `payday_boost` | 1.4 | assumption | Extra weight on the 1st-2nd, 15th-17th and last two days of the month |
| `mcc_mix_pos` | grocery 30%, restaurant 20%, transport 12%, fuel 8%, pharmacy 6%, retail 14%, entertainment 5%, other 5% | assumption | In-person card payments |
| `mcc_mix_online` | marketplace 35%, subscriptions 15%, food_delivery 15%, travel 8%, digital_goods 10%, utilities 5%, other 12% | assumption | Online card payments |

Hour of day (constants in `events.py`, `HOUR_PMF`): daytime peaks around 13-14h with an evening
bump at 19-21h; online card payments have a heavier late-evening tail; payroll lands in the early
hours (about 3h). These shapes are assumptions.

Other simplifications: ATM amounts are rounded to whole hundreds, minimum event amount is 10
pesos, internal counterparties are drawn uniformly from customers already onboarded, and no
balances are tracked (outflows are scaled to income instead).

## Identifiers

CURP and CLABE are generated in the real format with deliberately wrong check digits, and the
CLABE bank code `999` and the external banks are synthetic, so no identifier can match a real
person or institution.

## Simplifications to keep in mind

- One account per customer.
- The bank launches at `start_date`, so the first months are small.
- Customers are 18 to 70 at onboarding, the ENIF age range.
