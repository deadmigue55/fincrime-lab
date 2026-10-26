"""The governance pack quotes numbers; each one must still match the results file it came from."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
GOV = DOCS / "governance"


def _load(name: str) -> dict:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


FRAUD = _load("fraud-results/results.json")
ABLATION = _load("fraud-results/ablation.json")
BAF = _load("fraud-results/baf_results.json")
RISK = _load("risk-results/results.json")
AML = _load("aml-results/results.json")
A4 = AML["lookback"]["4"]


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}%"


def mxn_m(x: float) -> str:
    return f"{x / 1e6:.2f}M"


def _income_high() -> float:
    return next(
        g["false_alarm_rate"]
        for g in FRAUD["fairness"]
        if g["group"] == "income_band" and g["value"] == "high"
    )


def _baf_age(value: str) -> float:
    return next(g["false_alarm_rate"] for g in BAF["age_groups"] if g["value"] == value)


def _ibnr(method: str, target: str) -> dict:
    return next(
        r for r in RISK["worlds"]["ibnr"] if r["method"] == method and r["target"] == target
    )


# (document, the text it must contain, built from the results file)
CLAIMS = [
    # fraud scoring
    (
        "model-card-fraud.md",
        pct(FRAUD["headline"]["final"]["fpr"]["0.005"][0])
        + " | "
        + f"{100 * FRAUD['headline']['final']['fpr']['0.005'][1]:.1f} to {100 * FRAUD['headline']['final']['fpr']['0.005'][2]:.1f}",
    ),
    ("model-card-fraud.md", pct(FRAUD["incumbent"]["rule_recall"][0])),
    ("model-card-fraud.md", pct(FRAUD["by_type"]["all"]["model_pesos"])),
    ("model-card-fraud.md", pct(FRAUD["by_type"]["all"]["incumbent_flagged_pesos"])),
    ("model-card-fraud.md", f"{FRAUD['headline']['final']['ap']:.3f}"),
    ("model-card-fraud.md", f"{FRAUD['headline']['final']['auc']:.4f}"),
    ("model-card-fraud.md", "MXN " + mxn_m(FRAUD["cost"]["x1.0"]["saving_vs_incumbent"][0])),
    ("model-card-fraud.md", pct(BAF["test"]["tpr_at_fpr"]["0.05"][0])),
    ("model-card-fraud.md", pct(ABLATION["velocity"]["recall_at_0.5%"])),
    ("model-card-fraud.md", pct(_income_high(), 2)),
    ("model-card-fraud.md", pct(_baf_age("50 and over"))),
    ("model-card-fraud.md", pct(_baf_age("under 50"))),
    ("model-card-fraud.md", pct(FRAUD["drift"][0]["recall_fixed"])),
    ("model-card-fraud.md", pct(FRAUD["drift"][-1]["recall_fixed"])),
    ("model-card-fraud.md", f"{FRAUD['test_rows']:,}"),
    ("model-card-fraud.md", f"{FRAUD['test_fraud']:,}"),
    # fraud loss
    ("model-card-fraud-loss.md", f"{RISK['claims']['claims']:,}"),
    ("model-card-fraud-loss.md", f"{RISK['claims']['ever_reported']}"),
    (
        "model-card-fraud-loss.md",
        f"MXN {RISK['loss']['scenarios']['base']['point']['expected_loss'] / 1e6:.2f}M",
    ),
    (
        "model-card-fraud-loss.md",
        f"MXN {RISK['loss']['scenarios']['base']['point']['capital'] / 1e6:.2f}M",
    ),
    (
        "model-card-fraud-loss.md",
        f"{RISK['loss']['scenarios']['base']['interval']['capital'][0] / 1e6:.2f} to {RISK['loss']['scenarios']['base']['interval']['capital'][1] / 1e6:.2f}",
    ),
    ("model-card-fraud-loss.md", f"bias +{100 * _ibnr('M4', 'count')['bias']:.1f}%"),
    ("model-card-fraud-loss.md", f"coverage {100 * _ibnr('M4', 'count')['coverage']:.1f}%"),
    ("model-card-fraud-loss.md", f"coverage {100 * _ibnr('M2', 'amount')['coverage']:.0f}%"),
    (
        "model-card-fraud-loss.md",
        f"{RISK['out_of_time']['predicted'] / 1e6:.2f}M against {RISK['out_of_time']['realised'] / 1e6:.2f}M",
    ),
    ("model-card-fraud-loss.md", f"{100 * RISK['out_of_time']['relative_error']:.1f}%"),
    ("model-card-fraud-loss.md", f"{100 * RISK['worlds']['loss']['coverage_90']:.0f}% of banks"),
    ("model-card-fraud.md", pct(ABLATION["novelty"]["recall_at_0.5%"])),
    ("model-card-fraud.md", f"{ABLATION['novelty']['ap']:.2f}"),
    ("model-card-fraud.md", pct(FRAUD["by_type"]["all"]["incumbent_stopped_events"])),
    ("model-card-fraud.md", pct(FRAUD["by_type"]["all"]["incumbent_stopped_pesos"])),
    (
        "validation-report.md",
        f"{_income_high() / (sum(g['genuine'] * g['false_alarm_rate'] for g in FRAUD['fairness'] if g['group'] == 'income_band') / sum(g['genuine'] for g in FRAUD['fairness'] if g['group'] == 'income_band')):.2f})",
    ),
    ("validation-report.md", pct(_ibnr("M1", "amount")["coverage"])),
    ("validation-report.md", pct(_ibnr("M4", "amount")["coverage"])),
    ("validation-report.md", pct(RISK["worlds"]["loss"]["el_interval_covers_realised"])),
    ("model-card-fraud-loss.md", pct(RISK["worlds"]["loss"]["el_interval_covers_realised"])),
    (
        "model-card-aml.md",
        f"{100 * A4['budgets']['5']['M1']['refit_min']:.0f}% to {100 * A4['budgets']['5']['M1']['refit_max']:.0f}%",
    ),
    (
        "model-card-aml.md",
        f"M2 {100 * AML['lookback']['1']['budgets']['5']['M2']['recall']:.0f}%, stable",
    ),
    # AML
    ("model-card-aml.md", f"{A4['natural']['M0']['cases_per_week']:.1f} (1.7 investigators)"),
    ("model-card-aml.md", f"**{100 * A4['budgets']['5']['M1']['recall']:.0f}%**"),
    (
        "model-card-aml.md",
        f"{100 * A4['budgets']['5']['M2']['recall']:.0f}% (refits {100 * A4['budgets']['5']['M2']['refit_min']:.0f}% to {100 * A4['budgets']['5']['M2']['refit_max']:.0f}%)",
    ),
    ("model-card-aml.md", f"{100 * A4['budgets']['10']['M2']['recall']:.0f}% |"),
    (
        "model-card-aml.md",
        f"{100 * A4['budgets']['20']['M2']['recall']:.0f}% (refits {100 * A4['budgets']['20']['M2']['refit_min']:.0f}% to",
    ),
    ("model-card-aml.md", f"{100 * A4['budgets']['100']['M2']['forwarding']:.0f}%"),
    ("model-card-aml.md", f"{100 * A4['budgets']['100']['M1']['forwarding']:.0f}%"),
    (
        "model-card-aml.md",
        f"from {AML['amlworld']['HI-Small']['models']['TR']['pr_auc']:.2f} to {AML['amlworld']['HI-Small']['models']['G']['pr_auc']:.2f}",
    ),
    (
        "model-card-aml.md",
        f"{100 * AML['amlworld']['HI-Small']['models']['G']['flag']['0.005']['recall']:.0f}%",
    ),
    ("model-card-aml.md", f"{A4['episodes']} laundering episodes"),
]


@pytest.mark.parametrize(("doc", "text"), CLAIMS)
def test_card_number_matches_results(doc, text):
    body = (GOV / doc).read_text(encoding="utf-8")
    assert text in body, f"{doc} should contain {text!r}"


def test_findings_register_summary_matches_its_table():
    body = (GOV / "validation-report.md").read_text(encoding="utf-8")
    rows = [line.split("|") for line in body.splitlines() if re.match(r"\| \d+ \|", line)]
    sev = Counter(r[4].strip() for r in rows)
    status = Counter(r[6].strip().split(":")[0].split(" ")[0] for r in rows)
    summary = (
        f"{len(rows)} findings: {sev['High']} High, {sev['Medium']} Medium, {sev['Low']} Low; "
        f"{status['Fixed']} Fixed, {status['Amended']} Amended, {status['Open']} Open, "
        f"{status['Finding']} Finding."
    )
    assert summary in body
    assert f"{len(rows)} findings" in (GOV / "README.md").read_text(encoding="utf-8")


def test_every_relative_link_in_the_pack_resolves():
    for md in GOV.glob("*.md"):
        for target in re.findall(r"\]\(([^)#]+\.md)", md.read_text(encoding="utf-8")):
            assert (md.parent / target).exists(), f"{md.name} links to missing {target}"
