# Model card: fraud scoring (step 6)

Full evidence: [../fraud-models.md](../fraud-models.md). Protocol: [../methodology.md](../methodology.md)
section 7. Results file: `docs/fraud-results/results.json`.

## 1. Purpose and use

- **Decision:** score every outgoing attempt (card online, transfer out) before it is decided, and
  send the top-scored share to review or step-up.
- **Users:** a fraud operations team with a fixed daily review capacity.
- **Out of scope:** account-opening fraud (the BAF check is only an external sanity test), money
  laundering (see the AML card), any use on a real institution's data without refitting and
  revalidating.

## 2. Data

| | |
|---|---|
| Source | Banco Sintético, a simulator calibrated to public data (not a real bank), 27.2M attempts |
| Label | An approved event is fraud only if its victim *reported* it; blocked attempts have no outcome and are left out |
| Training | January to November 2024, labels as known on 1 April 2025 (120-day purge gap, where 95.5% of reports have arrived: pooled, card testing 93% to 95%) |
| Validation | January and February 2025 (model choice) |
| Test | April to June 2025, read once: 9,067,273 attempts, 2,878 fraud events in 440 accounts |
| Features | 37 point-in-time columns; an event is known to a feature only if it happened and was *recorded* before the scored attempt |

## 3. Method

Gradient-boosted trees, depth 3 (chosen on validation among four candidates, a number that is part
of the result), L2 = 1. Training sees a 2% sample of the genuine events, weighted back up. The flag
threshold is a **review budget** (share of attempts), not a probability cut-off.

## 4. Performance on the held-out test block

| Measure | Value | 95% interval (cluster bootstrap by account) |
|---|---|---|
| Fraud events found when 0.5% of genuine events are flagged (0.54% of attempts) | 99.3% | 99.0 to 99.5 |
| Same workload, the bank's own rules (0.54%) | 40.4% | 39.2 to 41.9 |
| Pesos of fraud found at 0.5% flagged | 97.1% | |
| Pesos found by the incumbent rules | 90.2% | |
| Average precision | 0.951 | |
| ROC-AUC | 0.9992 | |
| Expected saving against the rules (unit costs as stated) | MXN 2.35M a quarter | 2.11 to 2.56 |
| External check, BAF (a hard benchmark): fraud found at 5% false positives | 54.7% | 52.7 to 56.5 |

**Read the first rows with care.** The synthetic fraud is generated from mechanisms (bursts of small
charges, a takeover from a new device) that the features express directly, so the near-perfect score
says the *pipeline* is sound and not how a bank would fare. BAF is the honest counterweight.

## 5. What drives it

On the validation block, leaving velocity features out cuts recall at 0.5% from 99.5% to 82.6% and
average precision from 0.97 to 0.53; leaving novelty (new device, new payee) out cuts recall to
96.9% and precision to 0.71. The event family costs 1.1 points of recall and the friction family
(earlier declines) 0.9 points and 0.13 of average precision; the other families under 0.5 points.
The friction family is built from the incumbent's own decisions; if the incumbent is replaced,
those inputs change, and that has not been tested.

## 6. Fairness

At one shared 0.5% threshold, the share of genuine events flagged is 0.45% to 0.48% in every age
band and 0.47% for both sexes. **The high-income band is flagged 0.92% of the time, about twice the
others (0.42% to 0.54%)**: large amounts look unusual against the account's own history. On BAF,
applicants aged 50 and over are flagged 11.4% against 3.8% of younger ones, three times as often.
Recall by group is 95% or higher where it can be measured; small groups are marked inconclusive.

## 7. Limits (the ones that matter)

1. **One simulated world, one seed, one split.** Intervals are across accounts in that world, not
   across worlds.
2. **Feature mechanisms mirror the generator.** Expect lower performance on real fraud.
3. **No attacker adaptation** to the model, and no feedback loop: a static evaluation cannot show
   what happens once the model changes who is blocked.
4. **Step-up leak:** takeovers reuse a clone of the victim's own phone in 30% of cases (an assumption), so a step-up check can be passed; the
   model's flags would face the same leak, which is not applied to the model's row. The incumbent
   as it actually performs stops 37.1% of events and 66.8% of pesos, against 40.4% and 90.2% flagged.
5. **Unit costs** (MXN 15 per review, MXN 40 per customer disturbed) are assumptions; the saving
   stays positive at half and double.
6. **Label delay:** 95.5% of reports (pooled; 93% to 95% for card testing) arrive within 120 days; later months of training data are
   incomplete by design.

## 8. Monitoring and re-validation

See the monitoring plan in [validation-report.md](validation-report.md) section 6. In this world
the score distribution barely moved (PSI at most 0.005), recall at the fixed January threshold fell
from 99.9% to 98.7% by June and the flag rate from 0.50% to 0.43%, and account age and prior-event
count drifted hard (PSI up to 0.91) because accounts age. These show stability by construction, not
resilience to attackers.

## 9. Risk rating and conditions of use

**Fit for demonstration; not validated for production.** Before any real use: refit on real data,
revalidate the label delay, re-run the fairness table on the real population (the income finding
suggests which features to review first), and add a champion-challenger test with a holdout.

## 9. Change control

Every change after the protocol was fixed is an amendment with the reason in
[../methodology.md](../methodology.md) (sections 7 to 10 and the decision log); the findings
register in [validation-report.md](validation-report.md) lists them against this model. A change to
features, labels, the incumbent rule policy or the evaluation window is a re-validation trigger.
