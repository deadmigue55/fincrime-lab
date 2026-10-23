# Plan

One repo, one shared warehouse, five modules. Each module can stand alone as a portfolio card,
and every roadmap step ends in a commit.

## Goal

Show, with evidence a hiring manager can check in five minutes, that the author can:

1. move and query data at scale with SQL and Python (data engineering),
2. detect fraud the way production teams do (time-based validation, delayed labels, fixed alert budgets),
3. design and analyse experiments on fraud rules (A/B testing, power, sequential tests),
4. find money laundering with rules and graphs, and size the investigator workload,
5. put an actuarial lens on financial crime (loss distributions, chargeback maturation as IBNR),
6. document models so a validator or auditor could sign off.

## The data: two layers

**Layer A, a synthetic Mexican neobank ("Banco Sintético").** No Mexican transaction data is public,
so the generator builds one, calibrated to published aggregates (see `research.md` section 3):
customers by state and income band, accounts with CLABE-style identifiers, SPEI transfers, card
payments by merchant category, cash deposits at convenience stores, CoDi/DiMo payments, devices,
onboarding records. Fraud and laundering are **planted with known ground truth**: account takeover,
card testing, synthetic identities, mule accounts, structuring (pitufeo), scatter-gather, rapid
pass-through. Because the truth is known, every method can be validated by recovering it, the same
way the mortality experience study recovered 15 of 15 planted effects. It is a simulator, not a
static table: latent truth, a policy layer where rules can approve or decline, and what the bank
observes, so experiments and selection bias can be tested against the counterfactual. All parameters live in
`docs/assumptions.md`; nothing claims to describe a real institution.

**Layer B, public benchmarks**, used as external validation so no result rests on our own simulator
alone: IBM AMLworld (scale and AML), BAF (fraud, fairness, drift), IEEE-CIS (real card data),
Sparkov (geography and merchants).

## Modules

| Module | Question it answers | Headline output |
|---|---|---|
| `platform` | Can it move and query hundreds of millions of rows daily, reliably? | Partitioned Parquet lake, DuckDB marts, idempotent daily loads, data-quality contracts, scaling curve 1M to 180M rows |
| `fraud` | Which transactions to stop, at what cost? | Rules vs model at a fixed false-positive budget, time-based validation, delayed-label handling, cost-optimal threshold, fairness and drift monitoring |
| `experiments` | Did the new rule actually help? | A/A and A/B on fraud rules with power analysis, sequential testing, CUPED, guardrails, selection-bias correction, validated on planted effects |
| `aml` | Who is laundering, and how many alerts can investigators handle? | Rules vs graph features on AMLworld and the synthetic bank, typology coverage, threshold calibration and lookback, SAR-style case summaries |
| `risk` | What does fraud cost, and how sure are we? | Chargeback completion factors (IBNR), frequency-severity loss model, capital at 99.5%, model cards and validation report |

A Streamlit dashboard (daily KPIs, alert queue, model health) and a Quarto report sit on top.

## Roadmap

Each step is one or more commits, pushed in blocks.

| Step | Content | Done when |
|---|---|---|
| 0 | Research, plan, README | docs reviewed |
| 1 | Package scaffold: uv, config, `doctor`, lint, tests, CI | `uv run fincrime doctor` passes, CI green |
| 2 | **Done.** Banco Sintético simulator (latent, policy and observed layers) and `assumptions.md` | 18 of 18 acceptance checks pass on a 200,000-customer build, 37 tests, deterministic |
| 3 | **Mostly done.** Ingest public benchmarks (Kaggle) with manifest and checksums | BAF, Sparkov and AMLworld Small/Medium loaded and row-counted; AMLworld HI-Large downloading; IEEE-CIS waits for the user to accept the Kaggle rules |
| 4 | **Nearly done.** Data platform: partitions, marts, daily incremental loads, quality contracts, scaling benchmark | platform built and tested (16 tests, 597-drop run); final 180M-row timings pending a capped rerun |
| 5 | Fraud features in SQL (velocity, windows, device and geo) | feature store table with point-in-time correctness tests |
| 6 | Fraud models, rules, thresholds, fairness, drift | fixed-FPR report on BAF and the synthetic bank |
| 7 | Chargeback maturation and fraud loss model | completion factors and capital figures with intervals |
| 8 | Experiment lab | planted effects recovered within CIs, false-positive rate of A/A tests at 5% |
| 9 | AML module | typology coverage table and workload curve |
| 10 | Governance pack | model cards, validation report |
| 11 | Dashboard, Quarto report, portfolio card, release 1.0.0 | live URL, tagged release |

Highest-value cut if time is short: steps 1 to 6, then 8. That already covers data engineering,
fraud modelling and experimentation. Priority order of modules: `platform`, `fraud`,
`experiments`, `risk`, `aml`. A module is not started until the previous one has a README
section with a figure, an assumptions list, tests and a commit (see `methodology.md` section 5).

The rules for splits, metrics, uncertainty and what synthetic data can and cannot prove are in
[methodology.md](methodology.md); sources and licences in [research.md](research.md).

## Principles carried over from the mortality project

- Raw data is never committed; every download is logged in a manifest.
- Every claim in a README has a figure or table behind it and an assumption listed.
- Method validation on planted truth before trusting results on real data.
- Synthetic results are always labelled as synthetic on the figure.
- Compare variants of a rule as a smoke test: identical results from two rules means one is not running.
- Commits are small and named for what they do; no AI attribution lines in commit messages.

## Open questions

- Kaggle API credentials are needed for step 3 (the user creates a token at kaggle.com, Settings, API).
- Which exact Banxico and CONDUSEF tables to anchor on: to be settled in step 2 against primary sources.
- Whether to publish the dashboard on Streamlit Community Cloud (as before) or elsewhere.
