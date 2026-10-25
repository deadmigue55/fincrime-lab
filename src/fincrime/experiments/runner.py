"""Run every study of ``docs/methodology.md`` section 9 and keep the raw draws.

Each study is one task that loads the bench itself, so the tasks run in separate processes. The raw
effect and standard-error draws are saved next to the summaries, because a figure or a later check
(a different band, another quantile) should never need the hours of simulation again.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

from fincrime.experiments import outcomes, study, worlds

SEED = 2026
TREATMENTS = tuple(worlds.TREATMENTS)
AS_OF = date(2024, 12, 31)


def _bench(root: str, bank: str) -> outcomes.Bench:
    return outcomes.load(Path(root), Path(bank))


def task(args: tuple) -> tuple[str, dict]:
    """One study. Returns ``(key, payload)`` where payload holds arrays and plain summaries."""
    kind, root, bank, cfg = args
    bench = _bench(root, bank)
    t0 = time.time()
    if kind == "replicate":
        treatment, reps, seed = cfg
        r = study.replicate(bench, treatment, reps, seed)
        key = f"replicate:{treatment or 'aa'}"
        payload = {
            "effect": r.effect,
            "se": r.se,
            "truth": r.truth,
            "summary": r.summary(),
            "mde_note": "",
        }
    elif kind == "permutation":
        reps, shuffles, seed = cfg
        key = "permutation_aa"
        payload = {"summary": study.permutation_a_a(bench, [0, 1, 2, 3], reps, shuffles, seed)}
    elif kind == "srm":
        reps, seed = cfg
        key = "srm"
        payload = {"summary": study.srm(bench, reps, seed)}
    elif kind == "sequential":
        treatment, metric, reps, seed = cfg
        run = study.sequential_runs(bench, treatment, metric, reps, seed)
        key = f"sequential:{metric}:{treatment or 'aa'}"
        payload = {
            "effect": run["effect"],
            "se": run["se"],
            "truth": run["truth"],
            "tau": run["tau"],
            "first_" + "naive": run["first"]["naive"],
            "first_obf": run["first"]["obf"],
            "first_pocock": run["first"]["pocock"],
            "first_msprt": run["first"]["msprt"],
            "bounds_obf": run["bounds"]["obf"],
            "bounds_pocock": run["bounds"]["pocock"],
            "summary": study.sequential_summary(run),
            "power_fixed": study.fixed_horizon_power(run),
            "mde": study.mde(run["se"].mean(axis=0)),
        }
    elif kind == "delay":
        treatment, reps, seed = cfg
        fits, rates = outcomes.completion_inputs(Path(bank), AS_OF)
        n = bench.n
        # (control, treated), the order the look table expects
        w = np.stack(
            [
                outcomes.weighted_weekly(bench.arms["baseline"], fits, rates, n),
                outcomes.weighted_weekly(bench.arms[treatment], fits, rates, n),
            ]
        )
        out = study.delayed_readout(bench, treatment, w, reps, seed)
        key = f"delay:{treatment}"
        payload = {k: v for k, v in out.items()}
        payload["rates"] = {k: float(v) for k, v in rates.items()}
        payload["median_days"] = {k: float(f.median_days) for k, f in fits.items()}
    elif kind == "selection":
        treatment, metric, reps, seed = cfg
        key = f"selection:{treatment}:{metric}"
        payload = {"summary": study.selection(bench, treatment, metric, reps, seed)}
    elif kind == "rate_trap":
        (treatment,) = cfg
        key = f"rate_trap:{treatment}"
        payload = {"summary": study.rate_trap(bench, treatment)}
    else:
        raise ValueError(kind)
    payload["seconds"] = time.time() - t0
    return key, payload


def plan(root: Path, bank: Path, scale: float = 1.0) -> list[tuple]:
    """Every task, with the replication counts of section 9 (``scale`` shrinks them for a smoke run)."""
    r = lambda n: max(20, int(n * scale))  # noqa: E731
    root_s, bank_s = str(root), str(bank)
    tasks: list[tuple] = [("replicate", root_s, bank_s, (None, r(4000), SEED))]
    tasks += [
        ("replicate", root_s, bank_s, (t, r(2000), SEED + 1 + i)) for i, t in enumerate(TREATMENTS)
    ]
    tasks.append(("permutation", root_s, bank_s, (r(500), 999, SEED + 10)))
    tasks.append(("srm", root_s, bank_s, (r(300), SEED + 11)))
    for m_i, metric in enumerate(("loss", "friction")):
        for t_i, t in enumerate((None, *TREATMENTS)):
            tasks.append(
                ("sequential", root_s, bank_s, (t, metric, r(4000), SEED + 20 + 10 * m_i + t_i))
            )
    for i, t in enumerate(TREATMENTS):
        tasks.append(("delay", root_s, bank_s, (t, r(500), SEED + 40 + i)))
        tasks.append(("selection", root_s, bank_s, (t, "loss", r(2000), SEED + 50 + i)))
        tasks.append(("rate_trap", root_s, bank_s, (t,)))
    return tasks


def run(root: Path, bank: Path, out: Path, workers: int = 6, scale: float = 1.0) -> dict:
    """Run all tasks, save ``raw.npz`` and ``summary.json`` in ``out`` and return the summaries."""
    out.mkdir(parents=True, exist_ok=True)
    tasks = plan(root, bank, scale)
    arrays: dict[str, np.ndarray] = {}
    summaries: dict[str, object] = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for key, payload in pool.map(task, tasks):
            for name, value in payload.items():
                if isinstance(value, np.ndarray):
                    arrays[f"{key}|{name}"] = value
                else:
                    summaries.setdefault(key, {})[name] = value
            print(
                f"{key} done ({payload['seconds']:.0f}s, {time.time() - t0:.0f}s elapsed)",
                flush=True,
            )
    np.savez_compressed(out / "raw.npz", **arrays)
    (out / "summary.json").write_text(
        json.dumps(summaries, indent=1, default=float), encoding="utf-8"
    )
    return summaries
