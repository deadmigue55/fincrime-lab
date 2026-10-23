# FinCrime Lab

Fraud detection, anti-money-laundering, experimentation and fraud-loss modelling for a
**simulated Mexican neobank**, built end to end on SQL, Python and statistics.

> Status: **step 2 in progress** (2a customers, 2b genuine transactions done). Next: fraud and laundering attacks, policy layer.
> Follow progress in [docs/plan.md](docs/plan.md).

## Why this project

Fintech risk teams ask for the same things: SQL and Python at scale, fraud and AML analytics,
A/B testing, and models that can be explained to a validator. This repo covers each of them on
one shared warehouse, and tests every method on data where the answer is known.

## What is in it

| Module | What it shows |
|---|---|
| `platform` | Partitioned Parquet + DuckDB, daily incremental loads, data-quality contracts, scaling benchmark up to 180M rows |
| `fraud` | Time-based validation, delayed labels, fixed false-positive budgets, cost-optimal thresholds, fairness, drift |
| `experiments` | A/B tests of fraud rules: power, sequential testing, CUPED, guardrails, selection bias |
| `aml` | Rules vs graph analytics, typology coverage, alert workload |
| `risk` | Chargeback maturation (IBNR-style), frequency-severity fraud losses, model cards |

## Data

- **Banco Sintético**: a synthetic Mexican neobank calibrated to published aggregates (SPEI, ENIF,
  CONDUSEF, UIF). Fraud and laundering are planted with known truth. Not a real institution.
- **Public benchmarks**: IBM AMLworld, Bank Account Fraud (NeurIPS 2022), IEEE-CIS, Sparkov.

See [docs/research.md](docs/research.md) for sources and how sure we are of each number, and
[docs/methodology.md](docs/methodology.md) for the evaluation protocol and what synthetic data can
and cannot prove.

## Quick start

```powershell
uv sync
uv run fincrime doctor
```

## Author

Miguel Vilchis, actuarial scientist. Companion to the
[Mortality Table Builder](https://github.com/deadmigue55/Mortality-Table-Builder).
