# Methodology and validity

The rules the project follows so that its results mean something. Written before any modelling,
so the protocol cannot be bent to fit the results. Changes after step 2 are logged at the bottom.

## 1. What synthetic data can and cannot prove

The risk: a detector that finds fraud *we planted* proves little if the detector and the generator
share the same assumptions (circularity). What synthetic data **can** show:

- the method is correct: given known truth, estimators are unbiased, intervals cover, tests hold their size;
- the pipeline is correct: no leakage, labels mature as modelled, counts reconcile;
- how results *change* when an assumption changes (sensitivity).

What it **cannot** show: how well a model would do at a real bank. So:

1. Detector features are built from observable fields only; the generator's latent variables
   (fraud intent, ring id, true mule flag) are visible to the evaluation code, never to models.
2. Fraud behaviour is generated from *mechanisms* (an attacker with a budget, a playbook, and an
   adaptation rule), not from the features a model will use.
3. Every headline is repeated on at least one **external** benchmark (BAF, IEEE-CIS, AMLworld) so
   no claim rests on our simulator alone. When the two disagree, we report both.
4. Prevalence and attack mix are named assumptions with a sensitivity table (half and double), and
   every figure from synthetic data carries a `SYNTHETIC` footer.
5. We never write "the model catches X% of fraud at a bank". We write "on the simulated bank,
   under assumption set A".

## 2. The generator is a simulator with a decision hook

A/B tests, selection bias and attacker adaptation need to ask "what would have happened if the
rule had been different". A static table cannot answer that. So `Banco Sintético` is built as:

- a **latent layer**: customers, accounts, devices, and every *intended* transaction, genuine or
  fraudulent, with its true label and, for each fraud attempt, its would-be loss;
- a **policy layer**: rules and models decide approve, step-up or decline; declined or stepped-up
  attempts may be abandoned or retried by the attacker according to the adaptation rule;
- an **observed layer**: what the bank sees (approved transactions, delayed chargeback labels, alerts).

Latent outcomes of declined transactions are kept, so selection-bias corrections (reject
inference) can be scored against the truth. Same seed plus same policy gives the same data.

## 3. Evaluation protocol (fixed in advance)

**Splits.** Strictly by time: train, validation, test in order. A purge gap equal to the label
maturity (chargeback window) separates train from the next block, so no label from the future
leaks into training. No random k-fold on transactions, ever.

**Point-in-time features.** Every feature at time *t* uses only data strictly before *t*; a test
fails the build if a feature changes when future rows are deleted.

**Headline metrics.**
- Recall (TPR) at a fixed review budget: the share of transactions flagged (0.1%, 0.5%, 1%) or
  FPR (0.5%, 1%, 5%). BAF's own protocol uses a fixed FPR, so numbers are comparable.
- Expected cost: missed fraud loss + review cost + customer-friction cost, with the unit costs as
  named assumptions; the cost-optimal threshold is reported next to the fixed-budget one.
- Secondary: PR-AUC, calibration (reliability curve, Brier score). ROC-AUC is reported but never
  a headline: with 1% fraud it hides what happens at the operating point.

**Labels.** Evaluated on cohorts with equal label maturity. Immature months are completed with
the chargeback development factors (step 7), never read as "no fraud".

**Uncertainty.** Transactions of one account are not independent, so intervals come from a
**cluster bootstrap by account** (and by ring for AML), never an i.i.d. bootstrap. Each headline
shows a 95% interval and the number of independent clusters behind it.

**Fairness.** Report error rates by protected-style groups (age band, region, income band) at the
shared threshold, with intervals, and say plainly when groups are too small to conclude.

**Experiments.** A/A tests must reject at about the nominal 5% over many simulated runs; planted
effects must be recovered inside their intervals at the stated coverage; sample-ratio mismatch is
checked first. Randomisation unit is the customer, and shared devices or rings are treated as
possible interference and reported as a limitation or handled by cluster randomisation.

**Multiple comparisons.** Variants compared on the same data are counted, and the number tried is
reported next to the best result. Test data is touched once per model family.

## 4. Acceptance tests for the synthetic bank (step 2)

The generator passes only if its output reproduces the calibration anchors within stated
tolerances, tested in CI on a small build and in the README on the full one:

- adult share with an account, regional mix and age distribution within tolerance of ENIF/INEGI shares;
- share of transfers under 10,000 pesos close to the published SPEI share;
- identifiers valid in format, invalid by design, and unique;
- reproducible: same seed gives byte-identical Parquet;
- planted fraud and laundering rates match the declared assumptions exactly (they are inputs);
- reconciliation: every transaction has a customer, every event references a customer who already exists, and no balances are modelled (outflows are scaled to income).

## 5. Scope guard

Five modules is a lot. The rule: **a module is not started until the previous one has a README
section with a figure, an assumptions list, tests, and a commit.** If time runs out, the order of
priority is `platform`, `fraud`, `experiments`, then `risk`, then `aml`. A smaller project with
every claim checkable beats a large one with soft edges.

## 7. Step 6 protocol for the synthetic bank (fixed before any model was fitted, 2026-10-25)

Written after step 5 and after looking only at counts (positives per month, report delays,
incumbent flag rate), never at a model score.

**Population.** Every outgoing attempt (27.2M), scored before the decision, approved or not.
Truth label: `is_fraud` (account takeover and card testing, 8,552 events). Laundering is the AML
module's problem and is not a positive here.

**Splits and the purge gap.** Report delay has a median of 25 days and 95% of reports arrive within
120 days, so the purge gap is **120 days**.

| Block | Events | Labels | Used for |
|---|---|---|---|
| Selection train | 2024-01-01 to 2024-08-31 | reports received before 2025-01-01 | fitting the candidates |
| Validation | 2025-01-01 to 2025-02-28 | truth | choosing a candidate |
| Final train | 2024-01-01 to 2024-11-30 | reports received before 2025-04-01 | refitting the chosen candidate |
| **Test** | **2025-04-01 to 2025-06-30** | truth | touched once, at the end |

Training uses **observed labels**: an approved event is fraud only if it was reported before the
training date; blocked attempts have no observed outcome and are left out of training. All frauds
and a 2% sample of the other events are kept, with weight 50 on the sampled ones.

**Candidates (four, and the number is reported with the result):** logistic regression on log-scaled
features; gradient boosting with depth 3 and 200 rounds; depth 6 and 200 rounds; depth 6 and 400
rounds. All boosting candidates use L2 = 1 (see the amendment in the decision log). Selection rule: highest recall at a 0.5% flag share on validation; ties go to the
simpler model. Nothing is tuned on the test block.

**Baseline.** The incumbent rule policy, as an operating point: its own flag rate and recall on the
same test block (it cannot be moved along a curve). The model is also read at the incumbent's flag
share, so the two are compared at equal review workload.

**Headline metrics.** Recall at flag shares 0.1%, 0.5%, 1% and at false-positive rates 0.5%, 1%, 5%,
each with a 95% interval from a cluster bootstrap by account (300 replicates), and the number of
accounts with fraud behind it. PR-AUC, ROC-AUC and Brier score as secondary.

**Diagnostic, not a competitor.** The same chosen model trained on **true labels over all attempts**
(impossible at a real bank) shows what delayed, incomplete and approved-only labels cost.

**Cost.** Expected cost = pesos of fraud not flagged + review cost per flagged event + friction cost
per flagged genuine event. Unit costs are assumptions: review MXN 15, friction MXN 40, with the
cost-optimal threshold reported at half and double both. A flagged fraud is assumed prevented.

**Fairness and drift.** Flag rate among genuine events (the burden of false alarms) and recall by
age band, income band, sex and state at the shared 0.5% threshold, each with a cluster-bootstrap
interval; groups whose interval is wider than 0.3 are reported as inconclusive. Drift: score
and feature PSI by month against the training block, and recall at the fixed threshold by month.

**External check.** The same pipeline is run on BAF (train months 0-5, test months 6-7, the dataset's
own protocol) and reported next to the synthetic result; when they disagree both are shown.

## 8. Step 7 protocol: chargeback maturation and fraud loss (fixed before any model was fitted, 2026-10-25)

Written after looking only at counts: claims per month, the planted report-delay parameters in
`assumptions.md`, the shape of the exposure and the size quantiles of lost amounts. No estimate of
a completion factor, a frequency or a capital figure had been computed.

**What a claim is.** A *claim* is a fraud episode (`crime_class = fraud`) with at least one approved
outgoing loss: 1,102 of the 1,286 fraud episodes (the rest were blocked and lost nothing). Its
*loss date* is the timestamp of the first approved loss event and its *amount* is the episode's
`lost_amount`. The bank only learns that a loss was fraud when the victim reports it, so at an
as-of date D the bank *sees* the claims with `reported_ts <= D`; it has no record of the others,
neither the ones still to be reported nor the ones that never will be. The simulator keeps every
report, including those after the end of the data (the last is February 2026), so the **ultimate
of every cohort is known** and every estimate below can be scored against it. At a real bank that
check is impossible; here it is the whole point.

**Two targets, kept apart.** (1) *Reported ultimate*: claims that will ever be reported. Reporting
lag (IBNR in the claims sense) is identifiable from the reports alone and is what the methods below
estimate. (2) *True loss*: also the claims never reported (planted: 8% of takeovers, 20% of
card-testing episodes). The never-reported share cannot be learnt from reports; it enters only as a
named gross-up, shown at the planted value and at half and double.

**Cohorts and the triangle.** Cohort = calendar month of the loss; development lag = months between
the loss month and the report month, so cohort *m* has lags 0 to (D month - m) at a month-end D.
Counts and pesos, pooled and by fraud type (takeover reports in days, card testing in weeks, so
pooling lets the mix change the pattern).

**Methods (four, and the number is reported with the result).**

| Id | Method | Interval |
|---|---|---|
| M1 | Chain ladder on the pooled triangle, volume-weighted factors | Mack standard error; over-dispersed Poisson bootstrap (1,000 replicates) |
| M2 | Chain ladder by fraud type, summed | over-dispersed Poisson bootstrap by type |
| M3 | Bornhuetter-Ferguson by type: prior ultimate = exposure x the claim rate of the last six cohorts that are complete under M2 | point estimate only |
| M4 | Reporting-delay model: lognormal delay per type, fitted to the reported claims by right-truncated maximum likelihood; ultimate = reported / completion factor of the cohort | parametric bootstrap (1,000 replicates): parameter draws, then Poisson process error |

The chain-ladder tail is fixed in advance: an exponential-decay fit to the last three development
factors above 1, extrapolated to 24 lags, and 1.0 when fewer than three exceed 1. It is not tuned.
Pesos in M4 assume the delay is independent of the amount; that is tested (rank correlation of
amount and delay by type) and reported, not assumed.

**As-of dates and the choice of method.** Month-ends. *Selection block*: 2024-06-30 to 2024-12-31
(7 dates). *Confirmation block*: 2025-01-31 to 2025-06-30 (6 dates), read once, after the choice.
Cohorts from April 2024 on take part (earlier months have fewer than 15 claims). Criterion on the
selection block, fixed now: lowest mean absolute relative error of the total IBNR in pesos; a tie
within 1 point goes to the simpler method (M1 < M2 < M4 < M3). Also reported for every method:
bias of total IBNR in counts and pesos, the cohort-level absolute error, the width of the 95%
interval and its coverage of the true IBNR. As-of dates overlap, so coverage over them is not
independent: it is repeated across independent simulated banks (below) where it is.

**Completion factors and label maturity.** The chosen method yields a completion factor by fraud
type and lag in days. Two uses: immature months in the step 6 figures are completed with it instead
of being read as "no fraud", and it is checked against the 120-day purge gap that step 6 fixed
(the share of eventual reports arrived by day 120).

**Frequency.** Ultimate (completed) claim counts per month and type, divided by exposure = open
genuine accounts in the month. Negative binomial with mean exposure x rate; a log-linear time
trend is kept only if a likelihood-ratio test gives p < 0.01. Only cohorts that are at least 95%
complete under the chosen method enter the fit; the newer ones are shown, not fitted.

**Severity.** Lost amount per claim, by type. Two candidates: a lognormal; a lognormal body with a
generalised Pareto tail above the type's 90th percentile. Chosen by log-likelihood on the claims of
2025-01 to 2025-06 after fitting on 2024-01 to 2024-12 (the simulator's full report file supplies
their amounts). No inflation of amounts.

**Aggregate loss, capital and cover.** Claims of the two types are independent; counts and
amounts are independent (checked: correlation of monthly count and mean size). The distribution of
the next-12-months aggregate (July 2025 to June 2026) is built by the fast Fourier transform of the
compound distribution on a MXN 50 grid with a mean-preserving split of each amount between its two
neighbouring grid points; checked against Monte Carlo in the tests. Exposure scenarios: onboarding
continues at the mean of the last six months (base), no new accounts, onboarding 50% higher.
Reported: expected loss; 99.5% value at risk; capital = VaR(99.5%) - expected loss; tail value at
risk at 99%; the net expected recovery of an aggregate stop-loss at 120%, 150% and 200% of the
expected loss. Intervals (95%) come from 1,000 replicates that redraw the IBNR, refit the
frequency, resample the claims for severity and recompute the aggregate. The gross-up for unreported
claims is shown separately and is not part of the base figure.

**Validation on planted truth (the part that makes it checkable).**

1. *Known answer*: the textbook Taylor and Ashe triangle reproduces its published chain-ladder
   reserve and Mack standard error.
2. *Parameter recovery*: the fitted delay medians and spreads against the planted values (2 and 30
   days, 0.8), and the report rates against 92% and 80%.
3. *Out of time on this world*: the IBNR estimates above against the ultimates the file holds.
4. *Across independent worlds*: 200 banks of 200,000 customers (seeds 1 to 200, built attacks-only,
   which reproduces the main world exactly for seed 2026). In each, at the as-of date 2024-12-31:
   the 95% interval for the total IBNR in pesos and the 90% predictive interval for the aggregate
   loss of January to June 2025 are scored against what that world produced. Coverage should be
   near nominal; if it is not, that is the finding.

**Amendments made after fitting began.** The protocol above was committed before any estimate
existed (60468aa). Seven things changed while the work ran, each for a reason found in the data, and
each is listed here so the result is read as what it is: a protocol with these amendments, not a
protocol that was followed to the letter.

1. *The development-factor tail.* The fixed exponential-decay tail gives card testing a tail factor
   of 1.12 at the end of the data, so no card-testing month ever counts as 95% complete under
   chain ladder (the delay model says the oldest are 99.9% complete). Because of that, the months
   that enter the frequency fit are screened with the delay model's completion (M4), whatever method
   the backtest picks for IBNR totals. The tail rule itself is unchanged and its weakness is
   reported in the backtest.
2. *A fifth variant, M4s.* After the selection block had been read, M4 with the pesos of unreported
   claims computed as expected count times mean claim size was added to see whether the lumpy
   reported pesos explained M4's error in pesos. It did not. It cannot be chosen, and the number of
   variants tried is five, not four.
3. *A third severity candidate.* The unconstrained generalised Pareto tail came out with a negative
   shape (a largest possible loss) for both fraud types, and a later claim exceeded that maximum, so
   its held-out likelihood is minus infinity. The candidate "splice with the shape held at 0 or above"
   was added. The selection criterion, held-out log-likelihood, is the declared one and was not
   changed; the lognormal it replaces had a model mean of 941 pesos for card testing against 1,688
   in the data it was fitted on and 2,654 in the claims it was tested on.
4. *The bootstrap of the frequency.* The first version of the replicates redrew only the unreported
   claims, so the sampling error of the claim rate was missing and its interval collapsed to a point.
   The replicates now redraw the monthly counts from the fitted negative binomial (a parametric
   bootstrap), which is the standard way to carry that error.
5. *Losses cut off at the end of the data.* An episode that starts in the last day can have an event
   dropped by the end of the window (one of 24 worlds had one). The claim carries the sum of the
   events the bank has; a gap anywhere else still raises an error.

6. *An independent review* of the code (a separate agent reading it cold) found three departures,
   all fixed before the final run of the 200 worlds. The predictive interval of M4 drew the
   unreported count as a Poisson around its estimate and so left out the estimation error of the
   cohort's size (about 1.7 times too narrow for the newest card-testing month); it now draws the
   mean from a gamma first. The Bornhuetter-Ferguson prior took its "complete" cohorts from chain
   ladder, which has none for card testing (amendment 1), and silently fell back on the three oldest
   months; it now takes the last six cohorts the delay model calls at least 95% complete. The
   out-of-time check on the main bank had chosen its severity model on the same claims it then
   forecast; it now re-chooses on 2024 alone (fit before July, test July to December).
7. *The trend rule cannot act.* Section 8 keeps a log-linear trend in the claim rate only if its
   test gives p < 0.01. The test is run and reported (no trend was found in the main bank), but the
   forecast code has no trend branch; a bank where it triggered would need one.

**What was seen before the choice.** The first development run printed the selection and the
confirmation blocks together, so the confirmation block was not read once and in order as
specified. The rule picks the same method either way; the 200-world study below, in which each
world is a fresh draw, is the check that does not depend on this.

**Limits stated now.** One bank, 18 months, 1,102 claims: the 99.5% capital figure rests on the
fitted tail, not on observed 1-in-200 years, and is reported as a model output with its interval.
Fraud arrives from the simulator's constant attack mix, so there is no regime change to forecast;
adaptive attackers are step 8.

## 9. Experiment lab: protocol fixed before any outcome was read (step 8)

The counterfactual banks were started building before this section was written, but no outcome
table had been opened. Anything chosen after the first result is recorded as an amendment at the
end of this section.

**The question.** Does a change to the bank's real-time rules reduce fraud loss, at what cost to
genuine customers, and how long must the test run to say so with the error rates promised? Asked
of a bank where the answer is known: the simulator builds the same world under two rule sets
(section 2), so every customer has an outcome under both.

**Unit, population, window.**
- Unit: the customer. Population: genuine customers onboarded before 1 January 2025 (about
  190,000). Assignment: completely randomised, exactly half to each arm, from a seeded generator.
- The new rules apply from 1 January 2025; the outcome window ends 30 June 2025 (25 whole weeks
  plus six days). Covariates are measured from 1 July to 31 December 2024 in the baseline bank.
- Control is the baseline rules (`baseline_v1`). Treatments: `velocity3` (velocity limit 6 to 3),
  `device500` (new-device step-up from MXN 3,000 to 500) and `tight_all` (those two plus the
  young-beneficiary step-up from 2,000 to 500 and the night-time step-up from 5,000 to 1,000).
  Three, fixed now; no others will be tried.

**Truth.** The true effect of a rule on a metric is the mean over *all* eligible customers of the
outcome under the treatment bank minus the outcome under the baseline bank (the finite-population
average treatment effect). An experiment on a random half estimates it. Outcomes are assigned
from the two separate worlds, which assumes no interference between customers; that assumption is
built in, not tested, and is a stated limit.

**Metrics.**

| | Metric | Why |
|---|---|---|
| P1 (primary) | approved fraud loss, pesos per customer, true basis | what the rule is for |
| G1 (guardrail) | share of customers with at least one failed verification or decline of a genuine outgoing attempt | a customer who could not pay |
| G2 (guardrail) | step-ups per customer | verification burden even when it is passed |
| S1 to S3 | fraud claims per customer; takeover loss; card-testing loss | where the effect comes from |

**Estimators, fixed.** E1: difference in means, Welch standard error. E2: CUPED with the metric's own
pre-period value as the single covariate. E3: regression adjustment with the interacted
specification of Lin (2013) and robust standard errors, on five pre-treatment covariates
(log of pre-period approved outgoing count and pesos, pre-period failed verifications, pre-period
step-ups, tenure). Intervals are normal at 95%. For the primary metric a permutation test is
computed as well, because a rare heavy-tailed outcome can break the normal approximation.

**Criteria, all stated as pass or report.**
- *V1, A/A.* 4,000 re-randomisations of the baseline bank, every metric and estimator. The share of
  tests rejecting at 5% must fall inside the exact two-sided 99% binomial band for that number of
  runs. A failure is a finding and is reported with the remedy it forces (for P1 the permutation
  test replaces the normal one), not hidden.
- *V2, planted effect.* For each treatment and metric, 2,000 re-randomisations. The mean estimate
  must be within one Monte Carlo standard error of the truth (bias), and the 95% interval must
  contain the truth in at least 93% of runs. The share of runs with p below 0.05 is the power.
- *V3, sample ratio mismatch.* The arm counts are tested against 50/50 at p below 0.001 before any
  outcome. A deliberately broken assignment (5% of one arm's customers dropped at random from the
  analysis table) must be flagged, and the unbroken one must pass.
- *V4, sequential.* Looks at the end of each of 25 whole weeks, 4,000 A/A runs and the same under
  each treatment. Procedures: the naive test at every look, Lan-DeMets alpha-spending of the
  Pocock type and of the O'Brien-Fleming type (information fraction taken as calendar time), and
  the mixture sequential probability ratio test with a normal mixing standard deviation of 25% of
  the control mean. The two spending boundaries and the mixture test must keep the A/A rate of
  *any* rejection inside the 99% band around 5%; the naive one is expected not to. Power and median
  stopping week are reported.
- *V5, delayed labels.* At each weekly look the effect is read three ways: from true losses (the
  bank cannot see these), from reported losses only, and from reported losses weighted by the
  inverse of the completion factor of the step 7 delay model fitted on 2024 claims alone. No
  pass mark: the question is how far the reported readout lags the truth and whether the
  weighting closes it.
- *V6, selection.* Rolling a rule out to the newest third of customers by onboarding date instead
  of randomising. The naive difference, the tenure-adjusted one and the inverse-probability one
  are scored against the truth for that same subpopulation's randomised effect. Separately, a ratio
  metric (fraud pesos approved per MXN 1,000 of genuine pesos approved, summed within each arm) is
  compared with the per-customer effect it is often mistaken for. No pass mark; the claim is a bias
  or a sign.
- *Power.* Simulated by re-randomisation at 4, 8, 13 and 25 weeks of accrual for P1 and G1 under each
  treatment, and the smallest effect detectable with 80% power at each length.

**Counted.** Three estimators, three treatments, five outcome metrics, four procedures. The
numbers of variants tried are reported next to every best result.

**Limits stated now.** One bank and one seed of the world; the effect of a rule is constant over
the window; attackers adapt within an episode (section 2) but do not learn across customers, so
the interference an adapting fraud ring would create is not tested; the guardrail of friction is
the simulator's, and a real customer's tolerance is not.

## 6. Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-10-22 | One repo, five modules | Shared warehouse; one story |
| 2026-10-22 | Synthetic Mexican bank plus public benchmarks | No Mexican transaction data is public; benchmarks guard against circularity |
| 2026-10-22 | Generator built as simulator with a policy layer | A/B tests and reject inference need counterfactuals |
| 2026-10-22 | Protocol fixed before modelling | Avoid tuning the protocol to the results |
| 2026-10-22 | Attackers adapt (retry smaller, clone devices, seasoned mules) | Rules and simulator share an author; without adaptation the baseline would catch most fraud by construction |
| 2026-10-22 | All attack randomness drawn before the policy decides; event ids derived from episode ids | Same seed under two policies must give the same world, for clean A/B tests |
| 2026-10-22 | Fraud prevalence is a named assumption with sensitivity, not calibrated | CONDUSEF counts claims and there is no denominator |
| 2026-10-25 | Step 6 protocol fixed before fitting: 120-day purge, four candidates, test touched once, cost units named | Methodology rule: no bending the protocol to the results |
| 2026-10-25 | Amendment before the full run: L2 = 1 on all boosting candidates, not only the largest | A smoke run on one month (no validation result used) showed the unregularised Newton step explode with weighted rare positives (margins in the millions, train AUC 0.49); L2 = 1 gives margins within 20 |
| 2026-10-25 | Step 7 protocol fixed before fitting: claim definition, four IBNR methods, selection block then a confirmation block, FFT aggregate, 200-world calibration | Methodology rule: no bending the protocol to the results; the simulator keeps every report, so ultimates are checkable |
| 2026-10-25 | Reported-ultimate and true-loss kept as separate targets; never-reported share is a named gross-up | Reports cannot reveal what is never reported; pretending otherwise would hide the largest uncertainty |
| 2026-10-25 | Step 7 amendments after fitting began: frequency months screened by the delay model, a fifth IBNR variant (M4s), a third severity candidate (splice with shape at least 0), a parametric bootstrap for the frequency, an end-of-data guard | Each was forced by something visible in the data (a 12% card-testing tail, a lognormal mean half the data's, an interval that collapsed to a point); all are listed under section 8 |
| 2026-10-25 | Experiments judged on the same bank built under two rule sets; protocol (V1 to V6) fixed before any outcome table was read | An A/B method is only trusted if it recovers a known effect and holds its error rate |
