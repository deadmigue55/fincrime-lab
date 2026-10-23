# Research notes

What the job market asks for, which public data exists, and what we know about the Mexican
context. Written 2026-10-22 before any code. Every claim is tagged:

- **[checked]** read in a source during the research, link given
- **[memory]** from the author's background knowledge, to be verified when the data is downloaded
- **[to verify]** a secondary or conflicting source, do not quote until checked against the primary one

## 1. What employers ask for

Fraud and risk roles at Mexican fintechs (Stori, Kueski, PayJoy, albo, Incode, Félix, 2024-2026 postings):

- SQL and Python plus a BI tool (Tableau, Looker, QuickSight) in almost every posting. **[checked]**
- A/B testing and statistical modelling explicitly in the Stori fraud analyst posting, with fraud
  metrics (false positive rate, recall, model evaluation). **[checked]**
- Dashboards, alert monitoring and case investigation as day-to-day duties (albo, Incode). **[checked]**
- Identity fraud themes: identity theft, account takeover, synthetic identities (Incode). **[checked]**
- Credit approval and fraud strategy from portfolio behaviour (Kueski, PayJoy). **[checked]**

AML data science roles at banks:

- Finding new laundering typologies and anomalous behaviour with supervised and unsupervised methods. **[checked]**
- Threshold calibration and lookback studies; forensic transaction analysis in SQL. **[checked]**
- Network analysis of accounts and customer relationships (preferred, not required). **[checked]**
- Analytics that support SAR narratives; model documentation that survives validation and audit. **[checked]**

Not found: postings from Nu, Clara, Mercado Pago or Klar. Their requirements are unknown. **[to verify]**

## 2. Public datasets

| Dataset | Facts | Role here |
|---|---|---|
| IBM AMLworld (HI/LI x Small/Medium/Large) | Large files ~180M (HI) and ~176M (LI) transactions, ~2.1M accounts; laundering ~1 in 807 (HI) and ~1 in 1,750 (LI) **[checked]**. Published on Kaggle under a CDLA licence; patterns include fan-in/out, cycle, scatter-gather **[memory]** | Scale benchmark and AML module |
| BAF, Bank Account Fraud (NeurIPS 2022) | Built to capture imbalance, bias and time dynamics **[checked]**. 6 variants of 1M rows, ~1% fraud, 8 months, age as protected attribute **[memory]** | Fraud modelling, fairness, drift |
| IEEE-CIS (Vesta) | Real e-commerce card-not-present, 590,540 rows, 3.5% fraud, anonymised features **[checked]** | Real-data robustness check |
| Sparkov | Synthetic card fraud with customers, merchants and geography; size differs by version (1.3M to 1.85M rows) **[checked]**, so record the version used | SQL features, geography |
| ULB credit-card (2013) | 284,807 rows, 492 frauds, PCA features **[checked]** | Not used: every portfolio has it |

Sources: [AMLworld paper](https://arxiv.org/pdf/2306.16424),
[Fraud Dataset Benchmark](https://arxiv.org/pdf/2208.14417),
[network analytics for AML review](https://arxiv.org/pdf/2405.19383).

## 3. Mexican context, for calibrating the synthetic bank

No Mexican transaction-level data is public. The generator (step 2) is therefore calibrated to
published aggregates. Numbers below are the anchors and their confidence.

| Anchor | Value | Status |
|---|---|---|
| SPEI transfers in 2025 | > 7,300 million, +36.8% vs 2024, said by Banxico's governor to the Senate | **[checked]**, press report of the Senate statement |
| SPEI amount in 2025 | ~600 trillion pesos (STP), mean ~82,000 pesos per transfer (our division) | **[to verify]** against Banxico payment-system tables |
| SPEI share under 10,000 pesos | 62% of operations in 2014, 94% in 2025 | **[to verify]** opinion article citing Banxico |
| Default user transfer limit (MTU) | 12,500 pesos if the customer sets none | **[to verify]**, year unclear |
| Adults 18-70 with a formal financial product | 76.5% in 2024 (ENIF, INEGI/CNBV) | **[checked]** INEGI press release |
| Fraud claims, H1 2025 | 2.484M claims, MXN 10,714M claimed, MXN 2,556M refunded (about a quarter) | **[checked]** press reports of CONDUSEF |
| Fraud claims, Jan-Sep 2025 | 3.82M claims (72% of all), MXN 16,678M | **[to verify]** attributed to the Buro de Entidades Financieras |
| Mule accounts blocked 2014-2024 | 44,899 accounts of 6,941 people (UIF) | **[checked]** press report |
| CNBV fraud-prevention rules | in force 2024 with later delays; per-customer transaction limit; typology and information-sharing duties | **[to verify]** dates conflict between sources |
| UIF typology structure | method, technique, instrument, case, alert signal, trend | **[checked]** |

Things we did not find and must not invent: the reporting thresholds for unusual and relevant
operations, the national risk assessment, fraud losses by channel for 2025. Where the generator
needs a number we cannot source, it is a named parameter in `docs/assumptions.md`, marked as an
assumption, and the sensitivity of every result to it is reported.

Sources: [Banxico/Senate figures via dplnews](https://dplnews.com/?p=326663),
[STP figures](https://marketing4ecommerce.mx/spei-sigue-sin-ganarle-al-efectivo-la-paradoja-de-los-pagos-en-tiempo-real-en-mexico/),
[INEGI ENIF 2024 release](https://www.inegi.org.mx/contenidos/saladeprensa/boletines/2025/enif/ENIF2024_CP.pdf),
[CNBV fraud rules, Feedzai summary](https://www.feedzai.com/blog/latam-financial-regulations/),
[BBVA Research on CNBV delays](https://www.bbvaresearch.com/en/publicaciones/mexico-financial-regulation-march-2025),
[mule accounts, UIF](https://www.yucatan.com.mx/mexico/2025/08/25/cuentas-mulas-en-mexico-asi-es-el-lavado-de-dinero-que-se-expande-en-bancos.html),
[UIF typology framework](https://www.veritas.org.mx/Ambito-universitario/Ambito-universitario/Tipologias-emitidas-por-la-uif-en-Mexico).

## 4. Pitfalls that separate production work from Kaggle work

- Chargeback labels arrive 30-120 days late **[checked, practitioner guide]**, so the newest data
  is the least labelled. Models trained without accounting for it miss new patterns. **[checked]**
- Declined transactions produce no label, so a model only ever learns from what it allowed
  (selection bias, "reject inference"). **[checked]**
- Compare models on cohorts with equal label maturity, at the operating threshold the business uses. **[checked]**

## 5. Where the author's actuarial background gives an edge

- Chargeback maturation is the same problem as claims lag (IBNR): completion factors by report lag.
- Fraud loss = frequency x severity: compound loss distributions, capital, stop-loss, as in the
  reinsurance step of the Mortality Table Builder.
- Credibility weighting for small segments, planted-effect validation of methods (the experience
  study recovered 15 of 15 planted effects), and the habit of documenting every assumption.
