# Governance pack (step 10)

Model risk documents for the three models in this repository, written the way a bank's model risk
team expects them (the structure follows the US supervisory guidance on model risk, SR 11-7: purpose,
conceptual soundness, data, outcomes analysis, limitations, monitoring, change control). They are
**about a simulated bank**. Nothing here has been approved by anyone, and the "tier" column is what
the tier would be if these models were deployed, to show the reasoning.

| Document | What it is |
|---|---|
| [model-card-fraud.md](model-card-fraud.md) | Real-time card and transfer fraud scoring (step 6) |
| [model-card-fraud-loss.md](model-card-fraud-loss.md) | Chargeback IBNR, frequency-severity loss and capital (step 7) |
| [model-card-aml.md](model-card-aml.md) | Weekly money-laundering detection and case workload (step 9) |
| [validation-report.md](validation-report.md) | The builder's self-challenge: what was challenged and how, the findings register (32 findings), the monitoring plan |

The experiment lab (step 8) is a testing method, not a model, so it has no card; its pass or fail
criteria are in [../experiment-lab.md](../experiment-lab.md).

## Inventory

| Model | Decision it supports | Method | Tier if deployed | Why that tier | Headline (synthetic bank) |
|---|---|---|---|---|---|
| Fraud scoring | Which attempts go to review or step-up | Gradient-boosted trees, depth 3 | 1 (high) | Acts on every payment; false alarms land on customers | 99.3% of fraud events found at 0.54% of attempts reviewed, against 40.4% for the rules |
| Fraud loss | Reserve and capital for fraud | Delay model, negative binomial count, spliced severity, FFT | 2 (medium) | Feeds a capital figure but a human sets it | Capital MXN 2.12M (90% interval 1.14 to 3.77) |
| AML detection | Which accounts are investigated each week | Rules R1 to R4; trees on account features; trees plus graph features | 1 (high) | Regulatory duty; missed laundering is a legal risk | Rules find 100% of episodes at 14.8 cases a week; trees find 90% against 66% for rules at 5 cases; graph model unstable |

## Reading order

1. The model card for the model you care about (two pages: use, data, performance, limits).
2. [validation-report.md](validation-report.md): the builder's self-challenge, including the 32
   findings. Two of them overturned a choice already made (the IBNR method, the graph AML model).
3. The step write-up it links to, for the full evidence.

## How the numbers are kept honest

- The headline numbers in the cards are checked by `tests/test_governance.py` against the results
  files (`docs/*-results/results.json`). If a result changes and a card is not updated, the test
  fails.
- The protocols of steps 6, 7 and 8 were committed before any model or outcome was read; step 9's is
  attested in the text only (it was committed with its code). Every later change is an amendment
  with the reason ([../methodology.md](../methodology.md) sections 7 to 10).
- Steps 7, 8 and 9 each had a read-only review by an agent that had not seen the code; step 6 had
  none on record. These are a second reader, **not** an independent human validation; the
  validation report says what that does and does not cover.

## What is missing on purpose

A real model inventory has owners, approval dates, committee minutes and production incidents.
There is no production, so there are none. The monitoring plan is a proposal with thresholds that
are a reasonable starting point and are **not calibrated** on any real bank.
