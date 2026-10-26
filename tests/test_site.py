"""The public report is written from the results files and must say what they say."""

import json
import re
from pathlib import Path

from fincrime import site

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_report_source_carries_the_results_and_a_parsable_data_block(tmp_path):
    src = site.build(DOCS, tmp_path, render=False)
    qmd = src.read_text(encoding="utf-8")
    block = re.search(r"^data = \((\{.*\})\)$", qmd, re.M).group(1)
    data = json.loads(block)
    aml = json.loads((DOCS / "aml-results/results.json").read_text(encoding="utf-8"))
    assert (
        data["aml"]["4"]["5"]["M1"]["recall"]
        == aml["lookback"]["4"]["budgets"]["5"]["M1"]["recall"]
    )
    assert [s["name"] for s in data["scenarios"]][1] == "Base"
    assert len(data["shares"]) == 7 and data["incumbent"]["recall"] < data["shares"][0]["recall"]
    for text in ("99.3%", "40.4%", "54.7%", "MXN 2.12M", "92% of 200 banks", "100% of laundering"):
        assert text in qmd, text
    assert "32-finding register" in qmd  # counted from the validation report, not typed
    assert (tmp_path / ".nojekyll").exists()
