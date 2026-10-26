"""Run every study of docs/methodology.md section 10 and keep the results as one JSON file.

Order: weekly tables of the main bank, one evaluation per lookback, the same at half and double the
planted ring rate (a full bank is rebuilt for each and deleted afterwards), the two AMLworld
datasets, and three example case summaries.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import numpy as np

from fincrime.aml import amlworld, build, cases, detect, evaluate
from fincrime.aml import snapshots as sn

START = np.datetime64(build.START)
LOOKBACKS = (1, 2, 4, 13)
MAIN = 4
PREVALENCE = (0.5, 2.0)


def one_world(bank_dir: Path, work: Path, k: int = MAIN):
    """Tables, scored frame and evaluation of one bank at lookback ``k`` weeks."""
    build.build(bank_dir, work)  # seconds; never reuse tables from another bank or an older build
    t = sn.Tables(work, bank_dir)
    df = detect.frame(t, bank_dir, k, START)
    res, cs = evaluate.evaluate(t, df, k)
    return t, df, res, cs


def run(bank_dir: Path, work: Path, amlworld_dir: Path, out: Path, prevalence: bool = True) -> dict:
    """Everything, written to ``out`` (JSON) and returned."""
    result: dict = {"lookback": {}, "prevalence": {}, "amlworld": {}}
    for k in LOOKBACKS:
        t, df, res, cs = one_world(bank_dir, work, k)
        result["lookback"][k] = res
        print(f"lookback {k} weeks done")
        if k == MAIN:
            result["examples"] = _examples(t, cs, k)
    if prevalence:
        from fincrime.bank.assumptions import Assumptions
        from fincrime.bank.policy import RulePolicy
        from fincrime.bank.simulate import build_bank

        base = Assumptions(n_customers=200_000)
        for f in PREVALENCE:
            tag = work / f"prev_{f}"
            a = dataclasses.replace(base, mule_rings_per_100k=base.mule_rings_per_100k * f)
            build_bank(a, RulePolicy(), tag / "bank")
            _, _, res, _ = one_world(tag / "bank", tag / "aml")
            result["prevalence"][str(f)] = res
            shutil.rmtree(tag, ignore_errors=True)
            print(f"ring rate x{f} done")
    for name in ("HI-Small", "LI-Small"):
        df = amlworld.features(amlworld_dir, name)
        result["amlworld"][name] = amlworld.evaluate(df)
        print(f"{name} done")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    return result


def _examples(t: sn.Tables, cs: dict, k: int) -> dict[str, str]:
    picks = cases.pick(t, cs["M2_0"])
    return {
        label: cases.summary(t, w, k, cs["M2_0"][w][j][0], START) for label, (w, j) in picks.items()
    }
