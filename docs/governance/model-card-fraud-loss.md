# Model card: fraud loss, IBNR and capital (step 7)

Full evidence: [../fraud-loss.md](../fraud-loss.md). Protocol: [../methodology.md](../methodology.md)
section 8. Results file: `docs/risk-results/results.json`.

## 1. Purpose and use

- **Decision:** how many fraud claims are still to be reported (IBNR), what annual fraud loss to
  expect, and how much capital to hold against a bad year.
- **Users:** finance and risk, as an input to a reserve and a capital figure that a person signs.
- **Out of scope:** pricing, credit loss, operational loss other than fraud, any real bank's
  capital without refitting.

## 2. Data

1,102 fraud claims (332 account takeovers, 770 card-testing episodes) in 18 months of the synthetic
bank, 893 ever reported. The delay and report rates are *planted by us*, which is what lets the
method be checked against the truth. Two targets are never mixed: the reported ultimate (what the
bank can observe) and the true loss (reported or not), joined by an explicit gross-up for never-
reported claims at factors 0.5, 1 and 2.

## 3. Method

| Part | Method |
|---|---|
| Reporting delay | Right-truncated lognormal by fraud type (a plain fit is biased towards fast reports) |
| IBNR | Completion factors from the delay model (method M4) |
| Frequency | Negative binomial per fraud type, months screened by the delay model's completion |
| Severity | Spliced distribution with a generalised Pareto tail, shape at least 0 (chosen against a lognormal on held-out claims) |
| Annual loss | Exact FFT convolution on a MXN 50 grid, parametric bootstrap for parameter uncertainty |
| Capital | VaR 99.5% minus expected loss |

## 4. Performance

| Test | Result |
|---|---|
| Delay recovered (main bank) | median 1.95 days (95% CI 1.79 to 2.13) against 2 planted; 30.5 days (28.1 to 33.1) against 30 |
| Delay across 200 banks | location interval covers the planted value in 93.5% of banks |
| IBNR across 200 banks (counts) | M4 bias +3.7%, 95% interval coverage 94.0% |
| IBNR method chosen by the protocol | **M2 (chain ladder by type): the worst of four on pesos bias and error in 200 banks, pesos interval coverage 69% (95% promised; M1 67.5%)**; the model uses M4, whose pesos bias is +20% with coverage 88.5% |
| Out of time, main bank (January to June 2025) | predicted MXN 2.62M against 2.66M realised (-1.6%), inside the 90% interval 1.94 to 3.46M |
| Out of time, 200 banks | 90% interval covered the realised loss in 92% of banks (the expected-loss interval, without process noise, in 87.5%; mean PIT 0.56) |
| Expected annual loss (reported basis: 3.39M account-months, onboarding continues at the last six months' mean) | MXN 9.02M (90% interval 7.44 to 11.13) |
| **Capital (VaR 99.5% minus EL)** | **MXN 2.12M (90% interval 1.14 to 3.77)** |
| Known-answer test | Taylor-Ashe triangle: chain-ladder reserve 18,680,856 reproduced to the peso |

## 5. Sensitivities

Using a lognormal severity instead of the chosen one moves capital from 2.12M to 0.97M, more than
a factor of two: **severity model risk is the largest uncertainty after the sampling interval**.
Onboarding and the never-reported share move capital by 2% to 13%. Other bases: no new accounts (2.4M
account-months) EL 6.40M, capital 1.84M; true-loss basis (gross-up 1) EL 10.26M, capital 2.23M. The 18
months of history hold about MXN 3.2M of claims a year, so read 9.02M with its growth assumption.

## 6. Limits (the ones that matter)

1. **Capital rests on the tail.** About 30 takeover and 60 card-testing claims lie above the 90th
   percentile and drive the tail; the fitted tail shape is at its boundary (zero, an exponential tail)
   because the unconstrained fit went negative, and the constrained splice was added after seeing that
   failure. The lognormal alternative was rejected on held-out claims (it understates the
   card-testing mean by 44%), so the 0.97M is a bound from a known misfit and not a likely value;
   a negative-shape tail is not shown. A 1-in-200 year has never been observed. The cap of MXN 1M a
   claim hides any single catastrophic event.
2. **The method works when its assumptions hold** (the data are drawn from them). No regime
   change, no inflation of claim sizes, constant attack mix.
3. **Independence assumptions:** delay and amount are tested independent; the two fraud types are
   modelled independently although their monthly rates correlate at -0.52 (one bank, a short
   series).
4. **The trend rule cannot act:** a log-linear trend is tested and none was found, but the forecast
   has no trend branch.
5. M1 and M2 intervals are the weakest (the bootstrap holds the tail factor fixed).
6. One figure is unverified: the Taylor-Ashe total standard error differs by 523 from a number
   recalled from memory that could not be confirmed online.

## 7. Monitoring and re-validation

Monthly: actual against expected reports by cohort; refit delay parameters quarterly; refit severity
and recompute capital when claims since the last fit rise by 15% (amber) or 25% (red), or when a single
claim exceeds the 99th percentile of the fitted severity (see the plan in
[validation-report.md](validation-report.md) section 6).

## 8. Risk rating and conditions of use

**Fit for demonstration; not validated for production.** The method passes a 200-bank test with a
known answer. Before real use: replace planted delay assumptions with the bank's own report
history, state the never-reported share from investigation data, and treat the capital figure as an
input to judgement with its interval, not as a point value.

## 9. Change control

Every change after the protocol was fixed is an amendment with the reason in
[../methodology.md](../methodology.md) (section 8 and the decision log); the findings register in
[validation-report.md](validation-report.md) lists them against this model. A change to the delay
model, the severity family or the capital definition is a re-validation trigger.
