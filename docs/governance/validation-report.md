# Validation report (step 10): a self-challenge

A challenge of the three models in this repository: what was tested, what was found, what is still
open, and how each model would be watched in production. Scope: fraud scoring (step 6), fraud loss
and capital (step 7), AML detection (step 9). Model cards: [README.md](README.md).

## 1. Read this first: what this is, and is not

- **It is a self-challenge, not an independent validation.** The models were built by one person
  working with an AI coding assistant. The challenge comes from (a) protocols written before any model
  was scored, (b) tests with a known answer, because the data are simulated, and (c) read-only code
  reviews by an agent that had not seen the code. It does not replace a second human team, and the
  conclusions in section 2 are the builder's own opinion.
- **Protocol first.** For steps 6, 7 and 8 the protocol was committed before any model or outcome was
  read (`050a72c`, `60468aa`, `59c2d05`). Step 9's protocol (methodology section 10) was committed together with its
  code and results (`5414570`), so for step 9 "written first" is attested in the text only.
- **Code reviews on record:** steps 7, 8 and 9 (methodology sections 8, 9, 10). Step 6 has no code
  review on record, only a smoke run made by the author before fitting (finding 1).
- **The data are synthetic.** Findings about *method* (does it recover a planted truth?) transfer.
  Findings about *level* (how much fraud is caught, what capital is) do not.
- **The register is built from the repository's own amendments, decision log and commit
  history.** Where no source is recorded, "found by" says so. I cannot show that nothing was left out.

## 2. Conclusion by model (the builder's opinion)

| Model | Conclusion | Conditions |
|---|---|---|
| Fraud scoring | Conceptually reasonable; leakage controls pass their tests; **not validated for production** | Refit and revalidate on real data; review the high-income false-alarm gap; champion-challenger test |
| Fraud loss and capital | Method recovers a planted truth in 200 banks; **capital figure is a model output with a wide interval** | State the never-reported share from real data; treat 99.5% capital as an input to judgement; severity choice is the largest model risk |
| AML detection | Rules and trees usable; **the graph model is not stable enough to use at the lookbacks tested most**; the primary comparison is saturated | Prefer rules plus trees until the graph model shows a refit range under 20 points on enough rings |

## 3. How each model was challenged

| Test | Fraud scoring | Fraud loss | AML |
|---|---|---|---|
| Protocol before scoring | methodology section 7 (committed first) | section 8 (committed first) | section 10 (text only; see section 1) |
| Known-answer or hand-built test | feature tests against a slow Python oracle on a 2,500-customer, 36-day bank (the oracle shares the author's definition of arrival time) | chain ladder on the Taylor-Ashe triangle: reserve 18,680,856, each per-cohort error and the total error match R's ChainLadder (finding 15). FFT against Monte Carlo and a constant-claim Poisson | hand-built events for the three daily rules (the R3 case would fail with a window one day early; R1 and R2 cases would not); case join by transfer or phone |
| Leakage control | an event is known only if it happened and was recorded before the scored attempt (late events are excluded until they arrive); 120-day purge gap | out-of-time severity re-chosen on 2024 alone | a snapshot never reads past its week (one week, one 6,000-customer bank, snapshot code only: tables are filtered after being built); benchmark features use the state before the minute (tested, including same-minute rows) |
| Outcomes against truth | held-out quarter; 95% cluster bootstrap by account | 200 banks of known truth; out of time | 5 folds by ring, 5 refits, ring-cluster bootstrap |
| Benchmark | BAF | | AMLworld HI-Small and LI-Small |
| Sensitivity | feature-family ablation (validation block); costs half and double | severity model, onboarding, gross-up | lookback 1, 2, 4, 13 weeks; half and double ring rate; ablation |
| Fairness | age, sex, income, state; BAF age | | |
| Code review | none on record (smoke run only) | review: three departures | review: crash, early stopping, budget credit and others |

## 4. Findings register

Severity is the builder's judgement: **High** can change the conclusion, **Medium** changes a number or
hides a weakness, **Low** is presentation or a small departure. Status: *Fixed* (defect corrected),
*Amended* (a protocol choice changed after data were seen; recorded in methodology), *Open*
(accepted, documented) or *Finding* (reported as a result, not a defect).

| # | Step | Finding | Severity | Found by | Status |
|---|---|---|---|---|---|
| 1 | 6 | Boosting step exploded on weighted rare positives (margins in the millions) | Medium | Author's smoke run, no validation result used | Fixed: L2 = 1 for all boosting models, on record |
| 2 | 6 | The *friction* feature family is built from the incumbent's own declines (feedback loop); dropping it costs 0.9 points of recall and 0.13 of average precision | Medium | Ablation | Open: not tested anywhere; step 8 tests rules and estimators, not the model under another policy |
| 3 | 6 | High-income band flagged 0.92% against 0.42% to 0.54% for the others | Medium | Fairness table | Finding: features read large amounts as unusual; review before use |
| 4 | 6 | Synthetic score is near perfect because features mirror the generator | High | Comparison with BAF (54.7% at 5% false positives) | Open: stated at the top of the write-up and the card |
| 5 | 6 | BAF applicants aged 50 and over flagged 3 times as often (11.4% against 3.8%) | Medium | Fairness table on real benchmark data | Finding |
| 6 | 6 | The 30% of takeovers that clone the victim's device, and so pass a step-up check, are an assumption; the model's saving and 97.1% pesos figure credit it with preventing every flag, while the incumbent actually stops 37.1% of events and 66.8% of pesos | Medium | Stated by the author in the write-up | Open: stated in limits, not applied to the model's row |
| 7 | 7 | The IBNR method chosen by the protocol (M2) was the worst of four on pesos bias and error in 200 banks, with pesos interval coverage 69% (M1: 67.5%; 95% promised). On counts M1 is worse than M2 | High | 200-bank study | Fixed: M4 used; both reported |
| 8 | 7 | M4 predictive interval about 1.7 times too narrow for the newest month | Medium | Code review | Fixed: gamma mixing of the unreported mean |
| 9 | 7 | Bornhuetter-Ferguson prior silently fell back on the three oldest months for card testing | Medium | Code review | Fixed: last six cohorts the delay model calls 95% complete |
| 10 | 7 | Out-of-time check chose severity on the claims it then forecast | Medium | Code review | Fixed: severity re-chosen on 2024 alone (no before and after number recorded) |
| 11 | 7 | Frequency bootstrap redrew only the unreported part, so the first interval collapsed to a point | Medium | Author's inspection of the interval | Fixed: parametric bootstrap |
| 12 | 7 | Trend rule is tested but the forecast has no trend branch | Low | Code review | Open: no trend found in this bank |
| 13 | 7 | The first 24 of 200 worlds were computed with older code | Low | Not recorded | Fixed: the whole study rerun |
| 14 | 7 | Severity model moves capital by more than a factor of two (2.12M against 0.97M), but the lognormal alternative was rejected on held-out data (it understates the card-testing mean by 44%); the chosen splice has its tail shape at the boundary (zero, an exponential tail) after the unconstrained fit went negative | High | Sensitivity run | Finding: severity remains the largest model risk; a tail with a negative shape or another family is not shown |
| 15 | 7 | The Taylor-Ashe test used a total standard error recalled from memory (2,447,618) with a 0.1% tolerance that hid a 523 difference from our code's 2,447,095 | Medium | Known-answer test, then R's ChainLadder 0.2.21 | Fixed: R gives 2,447,095, so the recalled figure was wrong; the test now asserts it to the unit |
| 16 | 8 | A check that could not fail (V3 with exact halves), and a leak of hidden truth into the V5 weights | Medium | Code review | Fixed: amendments 8 and 9 (experiment lab section 9) |
| 17 | 8 | Label-study resume keyed only by seed: a rerun with other settings would silently reuse the wrong results (no published result is known to be wrong) | Medium | Ponytail review (a review plugin, not independent) | Fixed: key is (seed, treatment, customers), cut-off lines skipped (e80e6ae) |
| 18 | 8 | Two checks fell short: takeover-loss false-positive rate 4.0% against a 4.1% band edge, and detection of an outcome-dependent table loss 83.9% | Low | Acceptance tests | Finding: reported with the reason |
| 19 | 9 | Single fits disagreed by 44 points at 5 cases a week (M1 0.877 against 0.438) | High | Two runs of identical code | Fixed: 5 refits per model, range reported |
| 20 | 9 | Gradient boosting stopped early at 71 and 77 rounds, contrary to the protocol; with early stopping off the 200 rounds in the protocol made refits far more unstable | Medium | Code review | Amended: protocol changed from 200 to 70 rounds, not tuned on detection results |
| 21 | 9 | Budget credited every member of the first `b` cases, including low-ranked accounts that joined by link (up to 4 points flattering) | Medium | Code review | Fixed: credit by rank |
| 22 | 9 | Precision counted only the same week; delay counted in weeks; heavy-edge filter applied to the sum of two directions | Low | Partly code review (amendments 4 to 6) | Fixed: window, days, per direction |
| 23 | 9 | Shared-phone ablation was not a clean ablation (phones still link cases) | Medium | Code review | Fixed: relabelled as partial; limit stated |
| 24 | 9 | AMLworld graph claim confounded with extra history; ties within a minute broke by file order | Medium | Code review (ties); author (confound) | Fixed: three feature sets (T, TR, G), strict earlier-minute ties |
| 25 | 9 | Pipeline crashed on a missing results key and left a stale results file | Medium | Not recorded in the repository | Fixed: key corrected, results rerun |
| 26 | 9 | Probabilities saturate at exactly 1.0, so ties are broken by array order | Medium | Author's analysis | Fixed: rank by log-odds |
| 27 | 9 | Graph model unstable at 2 and 4 weeks of lookback (refit range 2% to 89% at 5 cases at 4 weeks); at 1 week it is stable and ahead of trees (94% against 84%); primary comparison saturated at 20 cases | High | Evaluation | Finding: not recommended at the default lookback; reported as a result |
| 28 | 7 | Two choices made after the selection block was read: a fifth IBNR variant (M4s, shown for information only) and a non-negative-shape severity candidate | Low | Author's disclosure (methodology section 8, amendments 2 and 3) | Amended: recorded; M4s cannot be chosen |
| 29 | 7 | The first development run printed the selection and confirmation blocks together, so the confirmation block was not read once and in order | Low | Author's disclosure | Open: the 200-world study does not depend on it |
| 30 | 8 | The always-valid test (mSPRT) on loss never rejects under the pre-period schedule: A/A rate 0.0% | Medium | Code review | Finding: reported; the test has no power on loss |
| 31 | 8 | Criteria changed after data were seen: V2 bias tolerance 1 to 3 Monte Carlo standard errors (6 of 54 cells exceed 1, worst 2.01), V4 made one-sided, V3 pass mark set after the first run | Medium | Author's disclosure (amendments 6, 7 and 10) | Amended: recorded with the reason; V1 still fails |
| 32 | 8 | The label study read the delay clock from a different origin in two code paths and used a different completion floor | Medium | Ponytail review | Fixed: shared clock and completion (e80e6ae); study rerun |

32 findings: 5 High, 21 Medium, 6 Low; 18 Fixed, 3 Amended, 5 Open, 6 Finding.

## 5. Challenges that came back clean, and the caveats on each

- **Leakage in the fraud features:** a late-arriving event is excluded until it arrives, and a feature
  at a cut point does not change when later rows are added (tested at one cut point on a small
  bank). The 120-day purge gap is consistent with the loss model's delay curve, which is a fit to
  the simulator's planted delay: pooled 95.5% of reports arrive within 120 days, **card testing
  (90% of the fraud events) 95.1% fitted and 93.3% observed**. That is circular support, not independent.
- **Two-bank truth in the experiment lab:** A/A rejection 4.0% to 4.95% across six metrics and three
  estimators, and V2 coverage 94.25% to 97.5%. But V1 failed on takeover loss (finding 18), V2 passes only
  after the bias tolerance was relaxed (finding 31), and the always-valid test on loss never rejects
  (finding 30).
- **IBNR method (M4):** interval coverage 94.0% for counts in 200 banks. On pesos M4 has bias +20% and
  coverage 88.5%, and M3 has a lower pesos error (0.51 against 0.60). The 90% annual forecast
  interval covered the realised loss in 92% of banks; the expected-loss interval covered it in 87.5%.
- **AML snapshot discipline:** a snapshot recomputed from tables cut at its week equals the full one
  (one week, one small bank; the tables themselves are filtered after being built).

## 6. Monitoring plan

Thresholds are **proposed starting points**, not calibrated on any real bank. PSI 0.10 and 0.25 are
the common "watch" and "major shift" marks. The refit-range rule applies to every model: M1 in this
bank has a range of 10.5 points at 5 cases a week (83.6% to 94.1%), which is already amber.

| Model | Metric | Frequency | Amber | Red | Action |
|---|---|---|---|---|---|
| Fraud | Score PSI against training | Weekly | 0.10 | 0.25 | Investigate inputs; at red, refit |
| Fraud | Flag rate at the fixed threshold | Daily | 25% change from baseline | 50% change | Check data feeds, then threshold |
| Fraud | Recall at the fixed threshold, on confirmed fraud, labels at 120 days | Monthly | 2 points below the first month | 5 points | Refit; check new attack types |
| Fraud | Share of genuine events flagged, by income band, age band, against the all-events average | Monthly | A band at 1.5 times the average | 2 times (this bank's high-income band: 1.96) | Review features for that band |
| Fraud | Label completeness at 120 days, by fraud type | Quarterly | Below 90% | Below 85% (card testing here: 93% to 95%) | Lengthen purge gap |
| Fraud loss | Actual against expected reports, by cohort | Monthly | Outside the 80% interval 2 months running | Outside the 95% interval | Refit delay model (and quarterly in any case) |
| Fraud loss | Claims since last fit | Monthly | 15% more | 25% more | Refit severity and frequency; recompute capital |
| Fraud loss | Single claim above the 99th percentile of fitted severity | On event | 1 a year | 2 a year | Re-examine tail |
| AML | Cases opened against team capacity | Weekly | 90% of capacity | 100% | Re-budget or retune rules |
| AML | Share of cases with a confirmed ring account | Weekly | Fall of 10 points | Fall of 20 points | Retune rules, retrain |
| AML | Confirmed rings per quarter against the training rate | Quarterly | Under 70% | Under 50% (trees collapse at half here: 31% at 5 cases) | Fall back on rules |
| AML | Refit range at the working budget, every model (max minus min over 5 refits) | Each retrain | 10 points | 20 points | Do not promote the model |

## 7. Re-validation triggers

Any of: a new data source or feature family; a change of the incumbent rule policy (the friction
family changes with it); a red in the table above; a change of definition of fraud or laundering; a
step-up or review process change; a regulator or audit request.

## 8. What a real validation would add

- A second team that did not build the model, with access to production data.
- Out-of-sample proof on real fraud, with real label delay.
- Benchmarking against a vendor or challenger model.
- Review of how investigators use the output (override rates, case notes).
- Stress tests the simulator cannot produce: attacker adaptation, regime change, claim inflation.
