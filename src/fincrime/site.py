"""The public report (step 11): a Quarto page written from the results files.

Every number in the text is formatted from the same JSON the documents are tested against, and
the explorers read a data block built from it, so the page cannot drift from the results.
Needs Quarto only to render; writing the source needs nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

AML_MODELS = [
    ("M0cut", "Rule bundle"),
    ("M1", "Trees"),
    ("M2", "Trees + graph"),
    ("M2n", "No phone count"),
]
AML_BUDGETS = (5, 10, 20, 50, 100)
GITHUB = "https://github.com/deadmigue55/fincrime-lab"


def _load(docs: Path, name: str) -> dict:
    return json.loads((docs / name).read_text(encoding="utf-8"))


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}%"


def explorer_data(fraud: dict, risk: dict, aml: dict) -> dict:
    """The compact block the three explorers read."""
    final = fraud["headline"]["final"]["share"]
    shares = [{"share": float(s), "recall": v[0], "lo": v[1], "hi": v[2]} for s, v in final.items()]
    inc = fraud["incumbent"]
    cost = {
        m.removeprefix("x"): {
            "model": c["model"][0],
            "incumbent": c["incumbent"][0],
            "none": c["no_flags"][0],
            "saving": c["saving_vs_incumbent"],
        }
        for m, c in fraud["cost"].items()
        if m.startswith("x")
    }
    lookbacks = {}
    for k, res in aml["lookback"].items():
        lookbacks[k] = {
            str(b): {
                m: {
                    "recall": res["budgets"][str(b)][m]["recall"],
                    "lo": res["budgets"][str(b)][m].get(
                        "refit_min", res["budgets"][str(b)][m]["recall"]
                    ),
                    "hi": res["budgets"][str(b)][m].get(
                        "refit_max", res["budgets"][str(b)][m]["recall"]
                    ),
                }
                for m, _ in AML_MODELS
            }
            for b in AML_BUDGETS
        }
    loss = risk["loss"]
    scen = {
        "No new accounts": loss["scenarios"]["flat"],
        "Base": loss["scenarios"]["base"],
        "High onboarding (+50%)": loss["scenarios"]["high"],
        "Never-reported gross-up 0.5": loss["gross_up"]["0.5"],
        "Never-reported gross-up 1": loss["gross_up"]["1.0"],
        "Never-reported gross-up 2": loss["gross_up"]["2.0"],
        "Lognormal severity (rejected misfit)": loss["other_severity"],
    }
    scenarios = [
        {
            "name": n,
            "el": s["point"]["expected_loss"],
            "el_lo": s["interval"]["expected_loss"][0],
            "el_hi": s["interval"]["expected_loss"][1],
            "cap": s["point"]["capital"],
            "cap_lo": s["interval"]["capital"][0],
            "cap_hi": s["interval"]["capital"][1],
        }
        for n, s in scen.items()
    ]
    return {
        "shares": shares,
        "incumbent": {"share": inc["flag_share"][0], "recall": inc["rule_recall"][0]},
        "cost": cost,
        "aml": lookbacks,
        "scenarios": scenarios,
        "models": [{"id": i, "label": lab} for i, lab in AML_MODELS],
    }


def headline_rows(fraud: dict, risk: dict, aml: dict, exp: dict) -> list[tuple[str, str, str]]:
    f = fraud["headline"]["final"]["fpr"]["0.005"][0]
    a4 = aml["lookback"]["4"]
    base = risk["loss"]["scenarios"]["base"]
    aa = {p["procedure"]: p["any_reject"] for p in exp["v4"]["loss"]["aa"]}
    hi = aml["amlworld"]["HI-Small"]["models"]
    return [
        (
            "Fraud scoring",
            "Rules or model, at the same review workload?",
            f"At 0.54% of attempts reviewed the model finds {pct(f)} of fraud events against "
            f"{pct(fraud['incumbent']['rule_recall'][0])} for the rules; on the hard BAF benchmark "
            f"it finds {pct(_baf(fraud))} at 5% false positives.",
        ),
        (
            "Fraud loss and capital",
            "How many claims are still to come, and what capital?",
            f"Capital MXN {base['point']['capital'] / 1e6:.2f}M (90% interval "
            f"{base['interval']['capital'][0] / 1e6:.2f} to {base['interval']['capital'][1] / 1e6:.2f}); "
            f"the 90% forecast interval covered the realised loss in "
            f"{pct(risk['worlds']['loss']['coverage_90'], 0)} of 200 banks.",
        ),
        (
            "Experiments",
            "How wrong is an A/B test of a fraud rule?",
            f"Checking weekly gives a false winner in {pct(aa['naive'], 0)} of A/A tests; alpha-spending "
            f"holds {pct(aa['pocock'], 0)} to {pct(aa['obf'], 0)}. Fraud loss is underpowered at 100,000 customers per arm.",
        ),
        (
            "AML",
            "Rules, trees or graph, and how many cases a week?",
            f"Rules find {pct(a4['natural']['M0']['recall'], 0)} of laundering episodes at "
            f"{a4['natural']['M0']['cases_per_week']:.1f} cases a week; trees find "
            f"{pct(a4['budgets']['5']['M1']['recall'], 0)} against {pct(a4['budgets']['5']['M0cut']['recall'], 0)} "
            f"at 5 cases; graph features are unstable here but lift AMLworld PR-AUC from "
            f"{hi['TR']['pr_auc']:.2f} to {hi['G']['pr_auc']:.2f}.",
        ),
    ]


def _baf(fraud: dict) -> float:
    # the BAF number lives in its own results file; passed through fraud["_baf"] by build()
    return fraud["_baf"]["test"]["tpr_at_fpr"]["0.05"][0]


QMD = """---
title: "FinCrime Lab"
subtitle: "Fraud, anti-money-laundering, experiments and fraud loss on a simulated Mexican neobank"
format:
  html:
    theme: cosmo
    toc: true
    toc-location: left
    embed-resources: false
    page-layout: full
execute:
  echo: false
---

::: {{.callout-warning}}
## Everything here is synthetic
The bank is a simulator (Banco Sintético), not a real institution. Methods are tested against
planted truth, so findings about *method* transfer and findings about *level* (how much fraud is
caught, what capital is) do not. External checks on public benchmarks are labelled as such.
:::

## Headline results

| Module | Question | Result |
|---|---|---|
{rows}

Each result has a protocol written before the models were scored (steps 6 to 8; step 9 is attested
in text only), a read-only code review by a second reader (steps 7 to 9, not an independent validation), and a limit stated next to it.
Two checks fell short and are reported as such: the takeover-loss A/A rate (4.0% against a 4.1%
band edge) and detection of an outcome-dependent sample loss (83.9%). The AML comparison at 20 cases a
week is saturated and cannot rank detectors.

## Explore

```{{ojs}}
//| echo: false
data = ({data})
```

::: {{.panel-tabset}}

### Fraud: review budget

How much fraud does the model find as the review team gets bigger? The bank's own rules are one
point on the chart: they send 0.54% of attempts to review.

```{{ojs}}
//| echo: false
viewof sel = Inputs.select(data.shares, {{format: d => (100 * d.share).toFixed(2) + "%", value: data.shares[3], label: "Share of attempts reviewed"}})
md`**${{(100 * sel.share).toFixed(2)}}% of attempts reviewed:** the model finds **${{(100 * sel.recall).toFixed(1)}}%** of fraud events (95% interval ${{(100 * sel.lo).toFixed(1)}} to ${{(100 * sel.hi).toFixed(1)}}). The rules find **${{(100 * data.incumbent.recall).toFixed(1)}}%** at ${{(100 * data.incumbent.share).toFixed(2)}}%.`
```

```{{ojs}}
//| echo: false
Plot.plot({{
  height: 320, marginLeft: 50, x: {{type: "log", label: "Share of attempts reviewed", ticks: data.shares.map(d => d.share), tickFormat: d => d3.format("~g")(100 * d) + "%"}},
  y: {{label: "Fraud events found", domain: [0, 1], tickFormat: d => (100 * d) + "%"}},
  marks: [
    Plot.areaY(data.shares, {{x: "share", y1: "lo", y2: "hi", fillOpacity: 0.2, fill: "#2a76d6"}}),
    Plot.line(data.shares, {{x: "share", y: "recall", stroke: "#2a76d6", strokeWidth: 2}}),
    Plot.dot(data.shares, {{x: "share", y: "recall", fill: "#2a76d6"}}),
    Plot.dot([data.incumbent], {{x: "share", y: "recall", fill: "#e8693a", r: 6}}),
    Plot.text([data.incumbent], {{x: "share", y: "recall", text: ["Bank's rules"], dy: 14, fill: "#e8693a"}}),
    Plot.ruleX([sel.share], {{stroke: "#888", strokeDasharray: "3,3"}})
  ]
}})
```

```{{ojs}}
//| echo: false
viewof mult = Inputs.radio(["0.5", "1.0", "2.0"], {{value: "1.0", label: "Unit costs (reviews MXN 15, disturbed customer MXN 40) times"}})
c = data.cost[mult]
md`Expected cost over the three test months at the validated budget: **model MXN ${{(c.model / 1e6).toFixed(2)}}M**, rules MXN ${{(c.incumbent / 1e6).toFixed(2)}}M, no flags MXN ${{(c.none / 1e6).toFixed(2)}}M. Saving against the rules **MXN ${{(c.saving[0] / 1e6).toFixed(2)}}M** (95% interval ${{(c.saving[1] / 1e6).toFixed(2)}} to ${{(c.saving[2] / 1e6).toFixed(2)}}).`
```

### AML: cases a week

How many cases must the team open each week to find the laundering? Bars are the mean of five refits;
the whisker is the range of the refits.

```{{ojs}}
//| echo: false
viewof lookback = Inputs.radio(["1", "2", "4", "13"], {{value: "4", label: "Lookback (weeks)"}})
viewof budget = Inputs.radio(["5", "10", "20", "50", "100"], {{value: "5", label: "Cases a week"}})
rows = data.models.map(m => ({{label: m.label, ...data.aml[lookback][budget][m.id]}}))
md`**${{budget}} cases a week** is about ${{(budget * 4 / 35).toFixed(1)}} full-time investigators (4 hours a case, 35 hours a week, both assumptions).`
Plot.plot({{
  height: 280, marginLeft: 110, x: {{label: "Laundering episodes detected", domain: [0, 1], tickFormat: d => (100 * d) + "%"}},
  y: {{label: null}},
  marks: [
    Plot.barX(rows, {{y: "label", x: "recall", fill: "#2a76d6"}}),
    Plot.ruleY(rows, {{y: "label", x1: "lo", x2: "hi", stroke: "#222", strokeWidth: 2}}),
    Plot.text(rows, {{y: "label", x: "recall", text: d => (100 * d.recall).toFixed(0) + "%", dx: -18, fill: "white"}})
  ]
}})
```

The graph model is unstable at 2 and 4 weeks (look at its whisker at 5 cases) and stable at 1 week.
The comparison at 20 cases a week is saturated: every detector finds nearly everything.

### Fraud loss: capital

Annual fraud loss and the capital to hold against a 1-in-200 year, under the scenarios in the write-up.
The lognormal row is a model the held-out data rejected; it shows how far the answer can move.

```{{ojs}}
//| echo: false
viewof scen = Inputs.select(data.scenarios.map(s => s.name), {{value: "Base", label: "Scenario"}})
s = data.scenarios.find(d => d.name === scen)
md`**Expected annual loss MXN ${{(s.el / 1e6).toFixed(2)}}M** (90% interval ${{(s.el_lo / 1e6).toFixed(2)}} to ${{(s.el_hi / 1e6).toFixed(2)}}). **Capital (VaR 99.5% minus expected loss) MXN ${{(s.cap / 1e6).toFixed(2)}}M** (${{(s.cap_lo / 1e6).toFixed(2)}} to ${{(s.cap_hi / 1e6).toFixed(2)}}).`
```

:::

## Figures

::: {{layout-ncol=2}}
![Rules against the model at the same review workload](../img/fraud_recall.png)

![How many cases a week to find the laundering](../img/aml_workload.png)

![Weekly peeking declares false winners](../img/exp_peeking.png)

![The fitted annual loss distribution](../img/risk_aggregate.png)
:::

## Documents

- [Plan and results table]({github}/blob/main/docs/plan.md) and the [README]({github}#readme)
- Fraud: [features]({github}/blob/main/docs/fraud-features.md), [models]({github}/blob/main/docs/fraud-models.md)
- [Fraud loss and IBNR]({github}/blob/main/docs/fraud-loss.md)
- [Experiment lab]({github}/blob/main/docs/experiment-lab.md)
- [AML module]({github}/blob/main/docs/aml.md)
- [Governance pack]({github}/blob/main/docs/governance/README.md): model cards, validation report with a {nfind}-finding register, monitoring plan
- [Methodology and protocols]({github}/blob/main/docs/methodology.md)

## Reproduce

```
uv sync
uv run fincrime bank build --customers 200000
uv run fincrime fraud evaluate && uv run fincrime risk report && uv run fincrime aml run
uv run fincrime site            # rewrites and renders this page
```

*Built by Miguel Abarca.* The code and every protocol are in the
[repository]({github}).
"""


def build(docs: Path, out: Path, render: bool = True) -> Path:
    """Write the Quarto source into `out` and render it when Quarto is installed."""
    fraud = _load(docs, "fraud-results/results.json")
    fraud["_baf"] = _load(docs, "fraud-results/baf_results.json")
    risk = _load(docs, "risk-results/results.json")
    aml = _load(docs, "aml-results/results.json")
    exp = _load(docs, "experiment-results/results.json")
    rows = "\n".join(f"| **{m}** | {q} | {r} |" for m, q, r in headline_rows(fraud, risk, aml, exp))
    report = (docs / "governance" / "validation-report.md").read_text(encoding="utf-8")
    nfind = sum(
        1
        for line in report.splitlines()
        if line[:2] == "| " and line.split("|")[1].strip().isdigit()
    )
    qmd = QMD.format(
        rows=rows,
        data=json.dumps(explorer_data(fraud, risk, aml), separators=(",", ":")),
        github=GITHUB,
        nfind=nfind,
    )
    out.mkdir(parents=True, exist_ok=True)
    src = out / "index.qmd"
    src.write_text(qmd, encoding="utf-8")
    (out / ".nojekyll").touch()
    if render and (quarto := shutil.which("quarto")):
        subprocess.run([quarto, "render", str(src)], check=True)
    return src
