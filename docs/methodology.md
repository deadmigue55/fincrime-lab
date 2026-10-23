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

## 6. Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-10-22 | One repo, five modules | Shared warehouse; one story |
| 2026-10-22 | Synthetic Mexican bank plus public benchmarks | No Mexican transaction data is public; benchmarks guard against circularity |
| 2026-10-22 | Generator built as simulator with a policy layer | A/B tests and reject inference need counterfactuals |
| 2026-10-22 | Protocol fixed before modelling | Avoid tuning the protocol to the results |
