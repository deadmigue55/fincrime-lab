# FinCrime Lab

Fraud detection, anti-money-laundering, experimentation and fraud-loss modelling for a
**simulated Mexican neobank**, built end to end on SQL, Python and statistics.

> Status: **step 2 of 11 done**: the synthetic bank is built, tested and reproducible.
> Next: public benchmarks (step 3) and the data platform (step 4).
> Follow progress in [docs/plan.md](docs/plan.md).

## Why this project

Fintech risk teams ask for the same things: SQL and Python at scale, fraud and AML analytics,
A/B testing, and models that can be explained to a validator. This repo covers each of them on
one shared warehouse, and tests every method on data where the answer is known.

## What is in it

| Module | What it shows | Status |
|---|---|---|
| `bank` | A simulated Mexican neobank with planted fraud and laundering, calibrated to public data | done |
| `platform` | Partitioned Parquet + DuckDB, daily incremental loads, data-quality contracts, scaling benchmark up to 180M rows | next |
| `fraud` | Time-based validation, delayed labels, fixed false-positive budgets, cost-optimal thresholds, fairness, drift | planned |
| `experiments` | A/B tests of fraud rules: power, sequential testing, CUPED, guardrails, selection bias | planned |
| `aml` | Rules vs graph analytics, typology coverage, alert workload | planned |
| `risk` | Chargeback maturation (IBNR-style), frequency-severity fraud losses, model cards | planned |

## The synthetic bank (step 2)

No Mexican transaction data is public, so the project builds a bank and calibrates it to what *is*
public: the 2020 census by state, INEGI/CNBV's ENIF 2024 survey (account holding by sex, app use),
and the system-wide SPEI amount mix. Everything else is a named assumption in
[docs/assumptions.md](docs/assumptions.md), and a test fails if one is undocumented.

A full build of **200,000 customers** takes about 105 seconds on a laptop and produces
**34.7 million transactions** (455 MB of Parquet):

```powershell
uv run fincrime bank build --customers 200000
uv run fincrime bank check --customers 200000 --report docs/bank-acceptance.md
uv run fincrime figures
```

![Customers and transactions over time](docs/img/bank_growth.png)

![Typical amount by transaction type](docs/img/bank_amounts.png)

### How it is built, and why that matters

- **Three layers.** *Latent truth* (who is a mule, which attempts are fraud, what a blocked attack
  would have cost), the bank's *policy* (real-time rules that approve, ask for extra verification
  or decline), and the *observed* data (what the bank sees, plus fraud reports that arrive late
  and only for some victims). Models only ever read the observed layer.
- **Same world, different policy.** All randomness of an attack is drawn before the rules are
  consulted, so the same seed under two rule sets gives the same victims and the same attempts;
  only the decisions differ. This is what makes clean A/B tests and selection-bias checks possible
  in step 8, and it is covered by a test.
- **Attackers adapt.** A blocked takeover is retried smaller, card testers give up after a run of
  declines, some takeovers clone the victim's own phone, some use seasoned mule accounts. Without
  this, rules written by the same author as the simulator would catch everything.
- **Five crime types**: account takeover, card testing, mule forwarding, scatter-gather and
  structuring, with mule rings that share phones and carry identity-document inconsistencies.

![What the rules stop and how late labels arrive](docs/img/bank_fraud.png)

On the 200,000-customer build the baseline rules stop 36% of account-takeover episodes outright
and block about 1.4 genuine transactions per 10,000 (customer friction, mostly extra verification
on very large transfers). No card-testing episode is stopped outright: the velocity rule trips
only after a handful of tiny charges have passed. These are properties of *this* baseline and
*these* attacker assumptions, not claims about any real bank.

### Checks

[docs/bank-acceptance.md](docs/bank-acceptance.md) holds the full report. On the 200,000-customer
build **18 of 18 checks pass**, for example:

| Check | Observed | Expected |
|---|---|---|
| State mix vs census times adoption tier | max 2.2 standard deviations | under 5 |
| Female share of customers | 0.466 | 0.463 (from ENIF 2024) |
| SPEI transfers under 10,000 pesos | 0.946 | about 0.94 (published, to verify) |
| Account-takeover episodes vs declared rate | 516 | 531 plus or minus 92 |
| Median delay until card fraud is reported | 29 days | about 30 (assumed) |
| Orphan events, duplicate ids, events before the account opened | 0 | 0 |

Identifiers (CURP, CLABE) have the right format but invalid check digits and synthetic bank
codes on purpose, so none can belong to a real person or account.

## Data

- **Banco Sintético**: described above. Not a real institution.
- **Public benchmarks** (step 3): IBM AMLworld, Bank Account Fraud (NeurIPS 2022), IEEE-CIS,
  Sparkov, used as external validation so no result rests on the simulator alone.

See [docs/research.md](docs/research.md) for sources and how sure we are of each number, and
[docs/methodology.md](docs/methodology.md) for the evaluation protocol and what synthetic data can
and cannot prove.

## Quick start

```powershell
uv sync
uv run fincrime doctor
uv run fincrime bank build --customers 20000
uv run pytest
```

## Author

Miguel Vilchis, actuarial scientist. Companion to the
[Mortality Table Builder](https://github.com/deadmigue55/Mortality-Table-Builder).
