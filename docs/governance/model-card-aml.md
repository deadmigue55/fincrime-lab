# Model card: AML detection (step 9)

Full evidence: [../aml.md](../aml.md). Protocol: [../methodology.md](../methodology.md) section 10
and its ten amendments. Results file: `docs/aml-results/results.json`.

## 1. Purpose and use

- **Decision:** each Monday, which accounts become investigation cases, given a team that can open a
  fixed number of cases a week.
- **Users:** AML investigators and the person who sizes the team (4 hours a case and 35 hours per
  full-time week are assumptions).
- **Out of scope:** transaction monitoring of cash movements outside the bank, sanctions screening,
  any real bank's alerts without recalibration of the 15,000 MXN structuring line (an assumption: the
  regulatory threshold was not verified).

## 2. Data

Banco Sintético (200,000 customers): 1.42M account-to-account transfers among 162,283 accounts, 1.14M
cash-ins, 50 planted mule rings (48 with an episode), 219 laundering episodes (111 scatter-gather,
108 structuring), plus fraud proceeds forwarded through mules (a separate typology, 272 approved
events). Truth is complete (every planted event is labelled), unlike a real bank. The bank sees only
its own side of every transfer. A second test uses IBM AMLworld (HI-Small, LI-Small; synthetic).

## 3. Method

| Id | Detector |
|---|---|
| R1 | at least 3 cash-ins at 80% to 100% of the line in 7 days |
| R2 | within 2 days, at least 50,000 in and at least 80% of it out again |
| R3 | at least 4 distinct internal counterparties in 2 days, at least 100,000 |
| R4 | at least 4 other accounts on the account's phone in the lookback |
| M1 | boosted trees (70 rounds, depth 3, no early stopping) on account-week features |
| M2 | M1 plus graph features (degree, neighbours, component size and flow, shared phones) |
| M2n | M2 without the shared-phone count (partial ablation) |

Five-fold validation by ring, five refits per model, ranking by log-odds. Alerted accounts become
cases when linked by a transfer or a shared phone in a 4-week lookback; a budget of `b` cases works
the accounts ranked above where case `b + 1` would start.

## 4. Performance

| Measure (4-week lookback) | Rule bundle | Trees (M1) | Trees + graph (M2) |
|---|---|---|---|
| Episodes found at 5 cases a week | 66% (cut) | **90%** | 65% (refits 2% to 89%) |
| Episodes found at 10 cases a week | 89% | 99% | 79% |
| Episodes found at 20 cases a week (primary) | 100% | 100% | 85% (refits 27% to 100%) |
| Cases a week at natural volume | 14.8 (1.7 investigators) | | |
| Fraud-proceeds mules caught (100 cases a week) | 31% (natural) | 22% | 44% |

The primary comparison at 20 cases a week is **saturated**: it cannot rank detectors, and the
write-up says so. Trees beat the cut rules at 5 cases by 24 points (95% interval 16 to 32).
M1 itself ranges from 84% to 94% across refits at 5 cases. Graph features *hurt* at 2 and 4 weeks of
lookback in this bank, and help at 1 week (M2 94%, stable, against 84% for M1) and at 13 weeks (M2 77%
against 32% for cut rules; M1 90%). On AMLworld HI-Small the graph set lifts
PR-AUC from 0.13 to 0.41 and recall at a 0.5% flag rate from 39% to 61%, and does nothing for laundering outside the
labelled patterns (26%).

## 5. Sensitivity

- Half the planted rings: trees collapse (31% at 5 cases a week), cut rules hold (73%).
- Double the rings: trees better (72%) than cut rules (46%).
- Lookback of 13 weeks: R4 floods; the rule bundle needs 22.6 cases a week (2.6 investigators).
- Lookback of 2 weeks: the graph model's range at 5 cases is 4% to 93%.

## 6. Limits (the ones that matter)

1. **The rules and the simulator share an author.** Near-perfect rule coverage is partly by
   construction.
2. **48 rings.** Everything unstable here is a few-rings problem: intervals are bootstrapped over
   rings and are wide.
3. **Shared phones separate rings by construction** (no genuine phone is shared); the graph model
   leans on a feature that is a simulator artefact. M2n shows the cost of dropping it.
4. **Refit instability.** The graph model's results depend on the random sample of negatives; this
   is why every number is a five-refit mean with its range, and why M1, not M2, is recommended at the default lookback. The refit-range rule applies to M1 too.
5. **No feedback loop:** investigators never block or freeze anything, so a blocked ring does not
   change behaviour. No money leaving the bank is visible after it leaves.
6. **Assumed constants:** the 15,000 MXN line, 4 hours per case, 35 hours per week.
7. AMLworld numbers have not been compared with published results.

## 7. Monitoring and re-validation

Weekly: cases opened against capacity, share of cases with a confirmed ring account, ring
confirmed rings per quarter against the training rate (supervised models collapse when it halves),
and refit range at the working budget. Retrain only on confirmed cases clustered by ring. See
[validation-report.md](validation-report.md) section 6.

## 8. Risk rating and conditions of use

**Fit for demonstration; not validated for production.** Recommended configuration if built on real
data: the rule bundle as the safety net, trees on account features as the ranker, graph features
only after refit stability is shown on enough confirmed rings and when phone sharing is not an
artefact of the data.

## 9. Change control

Every change after the protocol was fixed is an amendment with the reason in
[../methodology.md](../methodology.md) (section 10, amendments 1 to 10); the findings register in
[validation-report.md](validation-report.md) lists them against this model. A change to the
lookback, the case-linking rule, the rule thresholds or the feature set is a re-validation trigger.
Note that section 10 was committed together with its code and results, not before them.
