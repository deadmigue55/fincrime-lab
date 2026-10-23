# Acceptance report of the synthetic bank

Build: 200,000 customers, seed 2026, 2024-01-01 to 2025-06-30. **17 checks pass, 0 fail**, 3 are reported for information.

| Check | Result | Observed | Expected | Note |
|---|---|---|---|---|
| genuine customers | pass | 200,000 | 200,000 |  |
| state mix vs census x adoption tier | pass | max 2.2 sd | < 5 sd | census is checked data; the tier multiplier is an assumption |
| female share | pass | 0.466 | 0.463 | derived from ENIF 2024 (58.6% vs 68.0% hold a savings account) |
| age at onboarding | pass | 18.00 to 71.00 | 18 to 71 |  |
| CURP shape | pass | 0 malformed | 0 |  |
| identifiers unique | pass | 0/0 dups | 0 |  |
| identifiers invalid by design | pass | 0 of 100,000 sampled CLABEs valid | 0 | no CLABE can belong to a real account |
| SPEI transfers under 10,000 pesos | pass | 0.946 | about 0.94 | published system-wide share for 2025; reported by an opinion article citing Banxico (to verify) |
| internal share of outgoing transfers | pass | 0.250 | 0.250 |  |
| genuine events per active customer-month | info | 21.8 | assumed ~23 incl. payroll |  |
| ato episodes vs declared rate | pass | 516 | 531 +- 92 | episodes whose victim joined too late for the window are skipped |
| card_testing episodes vs declared rate | pass | 770 | 796 +- 113 | episodes whose victim joined too late for the window are skipped |
| mule rings | pass | 50 | 50 |  |
| event ids unique | pass | 0 duplicates | 0 |  |
| every event has an account | pass | 0 orphans | 0 |  |
| every counterparty account exists | pass | 0 orphans | 0 |  |
| no genuine event before the account opened | pass | 0 early | 0 |  |
| genuine attempts blocked or stepped up and failed | info | 1.4 per 10,000 | none (no source) | customer friction of the baseline rules |
| fraud attempts per 100,000 attempts | info | 24.6 | none (no source) | prevalence is a named assumption; see sensitivity in the fraud module |
| card-testing report delay (median days) | pass | 29 | about 30 | label lag; secondary source says 30-120 days for chargebacks |
