"""Command-line entry point: ``uv run fincrime <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fincrime import __version__
from fincrime.config import get_paths


def cmd_doctor(_: argparse.Namespace) -> int:
    """Check that the environment is ready: paths, engines, disk and credentials."""
    from fincrime import doctor  # imported here so `--version` stays fast

    paths = get_paths()
    print(f"FinCrime Lab v{__version__}")
    print(f"  Python      {sys.version.split()[0]}")
    print(f"  Project     {paths.root}")
    print(f"  Data        {paths.data}")

    checks = doctor.run_all(paths)
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        print(f"  {mark} {c.name:<12} {c.detail}")

    failed = [c for c in checks if c.required and not c.ok]
    print("\nAll set." if not failed else "\nSome required items need attention (see above).")
    return 1 if failed else 0


def cmd_bank_build(args: argparse.Namespace) -> int:
    """Generate the synthetic bank and write it to Parquet."""
    import time
    from pathlib import Path

    from fincrime.bank.assumptions import Assumptions
    from fincrime.bank.policy import RulePolicy
    from fincrime.bank.simulate import build_bank

    paths = get_paths().ensure()
    out = Path(args.out) if args.out else paths.processed / "bank"
    assumptions = Assumptions(n_customers=args.customers, seed=args.seed)
    print(f"building {args.customers:,} customers (seed {args.seed}) into {out} ...")
    t0 = time.time()
    s = build_bank(assumptions, RulePolicy(), out)
    print(f"done in {time.time() - t0:.0f}s")
    print(f"  customers          {s.n_customers:>12,}  (+ {s.n_mules:,} mule accounts)")
    print(f"  attempts           {s.n_attempts:>12,}  ({s.n_genuine:,} genuine)")
    print(f"  genuine blocked    {s.genuine_blocked:>12,}  (customer friction)")
    print(f"  fraud attempts     {s.n_fraud_attempts:>12,}")
    print(f"  laundering events  {s.n_laundering_events:>12,}")
    print(f"  fraud lost         {s.fraud_lost_amount:>12,.0f} MXN")
    print(f"  fraud blocked      {s.fraud_blocked_amount:>12,.0f} MXN")
    print(f"  fraud reports      {s.n_reports:>12,}")
    return 0


def cmd_bank_check(args: argparse.Namespace) -> int:
    """Run the acceptance checks on a built bank and print or write the report."""
    from pathlib import Path

    from fincrime.bank import acceptance
    from fincrime.bank.assumptions import Assumptions

    folder = Path(args.dir) if args.dir else get_paths().processed / "bank"
    a = Assumptions(n_customers=args.customers, seed=args.seed)
    checks = acceptance.run_checks(a, folder)
    report = acceptance.render_markdown(checks, a)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"wrote {args.report}")
    for c in checks:
        print(f"  {c.kind:<4} {c.name:<48} {c.observed}  (expected {c.expected})")
    failed = [c for c in checks if c.kind == "fail"]
    print(f"{len(failed)} checks failed" if failed else "All acceptance checks pass.")
    return 1 if failed else 0


def cmd_figures(args: argparse.Namespace) -> int:
    """Draw the README figures from a built bank."""
    from pathlib import Path

    from fincrime import figures

    folder = Path(args.dir) if args.dir else get_paths().processed / "bank"
    for path in figures.make_figures(folder, Path(args.out)):
        print(f"wrote {path}")
    return 0


def cmd_lake_export(args: argparse.Namespace) -> int:
    """Split a built bank into daily landing drops."""
    from pathlib import Path

    from fincrime.lake import landing

    paths = get_paths().ensure()
    bank = Path(args.bank) if args.bank else paths.processed / "bank"
    counts = landing.export_landing(bank, paths.landing)
    for kind, n in counts.items():
        print(f"  {kind:<9} {n:>12,} rows")
    print(f"{len(landing.drops(paths.landing))} daily drops in {paths.landing}")
    return 0


def cmd_lake_load(args: argparse.Namespace) -> int:
    """Load pending drops into the lake and refresh the marts."""
    import time

    from fincrime.lake.loader import Lake

    paths = get_paths().ensure()
    lake = Lake(paths.landing, paths.lake, paths.database)
    t0 = time.time()
    try:
        todo = lake.pending()
        if args.force:
            from fincrime.lake import landing

            todo = landing.drops(paths.landing)
        print(f"{len(todo)} drops to load")
        quarantined = 0
        from fincrime.lake import warehouse

        warehouse.refresh_dims(lake.con, paths.landing)
        for i, drop_id in enumerate(todo[: args.limit], 1):
            r = lake.load_drop(drop_id, force=args.force)
            quarantined += r.status == "quarantined"
            if (
                r.status == "quarantined"
                or r.warnings
                or i % 25 == 0
                or i == len(todo[: args.limit])
            ):
                note = f"  FAILED: {', '.join(r.failed)}" if r.failed else ""
                note += f"  warnings: {', '.join(r.warnings)}" if r.warnings else ""
                print(
                    f"[{i}/{len(todo[: args.limit])}] {drop_id}: {r.status} {r.rows:,} rows{note}"
                )
        print(f"done in {time.time() - t0:.0f}s, {quarantined} quarantined")
        return 1 if quarantined else 0
    finally:
        lake.close()


def cmd_lake_benchmark(args: argparse.Namespace) -> int:
    """Time canonical queries from 1M to 180M rows and write the results."""
    from pathlib import Path

    from fincrime.lake import benchmark

    paths = get_paths()
    base = paths.processed / "amlworld"
    trans, accts = base / f"{args.dataset}_Trans.parquet", base / f"{args.dataset}_accounts.parquet"
    for f in (trans, accts):
        if not f.exists():
            print(f"missing {f}: run `fincrime ingest convert amlworld --sizes large --kinds HI`")
            return 2
    sizes = tuple(args.sizes) if args.sizes else benchmark.SIZES
    queries = {k: v for k, v in benchmark.QUERIES.items() if not args.queries or k in args.queries}
    info = benchmark.machine()
    print("machine:", info)
    out = Path(args.out)
    if out.exists() and not args.resume:
        out.unlink()  # a fresh run starts a fresh file; use --resume to continue one
    benchmark.benchmark(
        trans,
        accts,
        sizes,
        queries,
        repeats=args.repeats,
        timeout=args.timeout,
        csv_path=out,
        resume=args.resume,
        memory_limit_gb=args.memory_limit_gb,
        temp_dir=paths.processed / "duckdb_tmp",
    )
    import json

    out.with_suffix(".machine.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    if args.layouts:
        rows = benchmark.layout_comparison(
            trans, paths.processed / "layout_test", n=args.layout_rows
        )
        for r in rows:
            print(f"layout {r['layout']:<16} {r['median_s']:.3f}s  {r['size_mb']:.0f} MB")
        import json

        Path(args.out).with_suffix(".layouts.json").write_text(
            json.dumps({"machine": info, "layouts": rows}, indent=2)
        )
    return 0


def cmd_lake_benchmark_report(args: argparse.Namespace) -> int:
    """Draw the benchmark figures and write the report."""
    from pathlib import Path

    from fincrime.lake import bench_report

    for path in bench_report.build(Path(args.csv), Path(args.out), Path(args.img)):
        print(f"wrote {path}")
    return 0


def cmd_lake_status(_: argparse.Namespace) -> int:
    """Summarise the load ledger and the data-quality results."""
    from fincrime.lake import warehouse

    paths = get_paths()
    con = warehouse.connect(paths.database, paths.lake)
    print(
        "ledger:",
        con.execute(
            "select status, count(*), sum(source_rows) from ops.load_ledger group by 1"
        ).fetchall(),
    )
    print(
        "contracts failed:",
        con.execute(
            "select contract, severity, count(*) from ops.dq_results where not passed group by 1, 2 order by 3 desc"
        ).fetchall(),
    )
    print(
        "marts:", con.execute("select count(*), min(day), max(day) from mart.daily_kpis").fetchall()
    )
    print("reports still waiting for their event:", warehouse.orphan_reports(con))
    return 0


def cmd_fraud_features(args: argparse.Namespace) -> int:
    """Build the point-in-time fraud feature store from the lake."""
    from fincrime.fraud import features

    paths = get_paths()
    out = Path(args.out) if args.out else paths.processed / "features" / "fraud_events"
    info = features.build(
        paths.lake,
        paths.landing,
        out,
        first_day=args.first_day,
        last_day=args.last_day,
        memory_limit_gb=args.memory_limit_gb,
    )
    print(f"wrote {info['rows']:,} rows to {out} in {info['seconds']} s")
    return 0


def cmd_fraud_evaluate(args: argparse.Namespace) -> int:
    """Fit and score the fraud models under the fixed protocol (docs/methodology.md section 7)."""
    from fincrime.fraud import evaluate

    paths = get_paths()
    store = paths.processed / "features" / "fraud_events"
    out = Path(args.out) if args.out else paths.processed / "fraud_results"
    summary = evaluate.run(store, paths.lake, out)
    print(
        f"chosen {summary['chosen']} of {summary['variants_tried']} candidates, {summary['seconds']} s"
    )
    return 0


def cmd_fraud_report(args: argparse.Namespace) -> int:
    """Metrics, intervals, cost, fairness, drift and figures from the saved scores."""
    from fincrime.fraud import report

    paths = get_paths()
    results = Path(args.results) if args.results else paths.processed / "fraud_results"
    out = report.build(results, Path(args.img))
    print(
        f"wrote {results / 'results.json'}; {out['test_rows']:,} test attempts, {out['test_fraud']} fraud"
    )
    return 0


def cmd_fraud_baf(args: argparse.Namespace) -> int:
    """The same pipeline on the BAF benchmark (external check)."""
    from fincrime.fraud import baf

    paths = get_paths()
    out = Path(args.results) if args.results else paths.processed / "fraud_results"
    summary = baf.run(paths.processed / "baf" / "Base.parquet", out)
    print(f"chosen {summary['chosen']}; TPR at 5% FPR {summary['test']['tpr_at_fpr'][0.05][0]:.3f}")
    return 0


def cmd_fraud_ablation(args: argparse.Namespace) -> int:
    """Leave-one-feature-family-out on the validation block."""
    import json

    from fincrime.fraud import evaluate

    paths = get_paths()
    out = Path(args.results) if args.results else paths.processed / "fraud_results"
    chosen = json.loads((out / "selection.json").read_text(encoding="utf-8"))["chosen"]
    evaluate.ablation(paths.processed / "features" / "fraud_events", paths.lake, out, chosen)
    return 0


def cmd_risk_worlds(args: argparse.Namespace) -> int:
    """Run the calibration across independent simulated banks (docs/methodology.md section 8)."""
    from fincrime.risk import simcheck

    paths = get_paths()
    out = Path(args.out) if args.out else paths.processed / "risk_results" / "worlds.jsonl"
    done = simcheck.run(
        out, range(1, args.n + 1), args.customers, args.reps_ibnr, args.reps_loss, args.workers
    )
    print(f"{len(done)} worlds in {out}")
    return 0


def cmd_risk_report(args: argparse.Namespace) -> int:
    """Run the step 7 protocol on the main bank, write results.json and draw the figures."""
    from fincrime.risk import report

    paths = get_paths()
    worlds = paths.processed / "risk_results" / "worlds.jsonl"
    res, plot = report.analyse(paths.processed / "bank", worlds, reps=args.reps)
    report.write(res, Path(args.results))
    report.figures(res, plot, Path(args.img))
    print(f"wrote {args.results} and the figures in {args.img}")
    return 0


def cmd_experiments_build(args: argparse.Namespace) -> int:
    """Build the counterfactual banks (one per rule change) and keep their outcome tables."""
    import time

    from fincrime.experiments import worlds

    paths = get_paths().ensure()
    out = Path(args.out) if args.out else paths.processed / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "baseline" / "fraud.parquet").exists():
        worlds.extract_baseline(paths.processed / "bank", out)
        print("baseline tables written")
    for name, policy in worlds.TREATMENTS.items():
        if (out / name / "fraud.parquet").exists() and not args.force:
            print(f"{name}: already built")
            continue
        t0 = time.time()
        worlds.build_and_extract(name, policy, out)
        print(f"{name}: built in {time.time() - t0:.0f}s")
    return 0


def cmd_experiments_run(args: argparse.Namespace) -> int:
    """Run every validation study of docs/methodology.md section 9 and keep the raw draws."""
    from fincrime.experiments import runner

    paths = get_paths()
    root = paths.processed / "experiments"
    out = Path(args.out) if args.out else paths.processed / "experiments_results"
    runner.run(root, paths.processed / "bank", out, args.workers, args.scale)
    print(f"raw draws and summaries in {out}")
    return 0


def cmd_experiments_report(args: argparse.Namespace) -> int:
    """Score every criterion of docs/methodology.md section 9 and draw the figures."""
    import json

    from fincrime.experiments import labels, outcomes, report

    paths = get_paths()
    results = Path(args.results) if args.results else paths.processed / "experiments_results"
    summary, raw = report.load(results)
    bench = outcomes.load(paths.processed / "experiments", paths.processed / "bank")
    res = report.analyse(summary, raw, bench)
    labels_file = results / "labels.jsonl"
    if not labels_file.exists():
        print(f"{labels_file} is missing: run `fincrime experiments labels` first")
        return 1
    rows = [json.loads(x) for x in labels_file.read_text(encoding="utf-8").splitlines()]
    res["labels"] = labels.summarise(rows)
    report.write(res, Path(args.out))
    report.figures(res, raw, Path(args.img), rows)
    print(f"wrote {args.out} and the figures in {args.img}")
    return 0


def cmd_experiments_labels(args: argparse.Namespace) -> int:
    """Read an experiment through delayed labels in many independent banks."""
    from fincrime.experiments import labels

    paths = get_paths()
    out = Path(args.out) if args.out else paths.processed / "experiments_results" / "labels.jsonl"
    rows = labels.run(out, args.treatment, range(1, args.n + 1), args.customers, args.workers)
    print(f"{len(rows)} banks in {out}")
    return 0


def cmd_ingest_list(_: argparse.Namespace) -> int:
    """Show every source, its licence, and what is downloaded and converted."""
    from fincrime.ingest import manifest
    from fincrime.ingest.sources import SOURCES

    paths = get_paths()
    raw = {(r.source, r.file) for r in manifest.read(paths.manifest, manifest.RawRecord)}
    conv = {
        (r.source, r.file): r
        for r in manifest.read(paths.processed / "MANIFEST.csv", manifest.ParquetRecord)
    }
    for s in SOURCES.values():
        print(f"{s.key}  [{s.license}]")
        print(f"  publish rule: {s.publish_rule}")
        for f in s.files:
            state = (
                "converted" if (s.key, f) in conv else ("downloaded" if (s.key, f) in raw else "-")
            )
            extra = (
                f"  {conv[(s.key, f)].rows:,} rows ({conv[(s.key, f)].expected_rows})"
                if state == "converted"
                else ""
            )
            print(f"    {state:<10} {f}{extra}")
    return 0


def cmd_ingest_report(args: argparse.Namespace) -> int:
    """Write a fact sheet of the benchmark datasets on disk."""
    from pathlib import Path

    from fincrime.ingest.report import render_markdown

    text = render_markdown()
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def _ingest_files(args: argparse.Namespace):
    from fincrime.ingest.kaggle_client import select_files
    from fincrime.ingest.sources import SOURCES

    source = SOURCES[args.source]
    return source, select_files(source, args.files, args.all, args.sizes, args.kinds)


def cmd_ingest_download(args: argparse.Namespace) -> int:
    """Download a source's files from Kaggle (resumable, checksummed)."""
    from fincrime.ingest.kaggle_client import RulesNotAccepted, download

    source, files = _ingest_files(args)
    try:
        for f in files:
            r = download(source.key, f, force=args.force)
            note = "already downloaded" if r.skipped else f"{r.bytes / 1e6:,.1f} MB"
            print(f"[{source.key}] {f}: {note}")
    except RulesNotAccepted as err:
        print(f"ERROR: {err}")
        return 2
    return 0


def cmd_ingest_convert(args: argparse.Namespace) -> int:
    """Convert downloaded files to Parquet and verify row counts where known."""
    from fincrime.ingest.convert import convert_file

    source, files = _ingest_files(args)
    bad = 0
    for f in files:
        rec = convert_file(source.key, f, force=args.force)
        if rec is None:
            print(f"[{source.key}] {f}: already converted")
            continue
        print(f"[{source.key}] {f}: {rec.rows:,} rows ({rec.expected_rows})")
        bad += "MISMATCH" in rec.expected_rows
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="fincrime", description=__doc__)
    parser.add_argument("--version", action="version", version=f"fincrime {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check engines, disk space and credentials").set_defaults(
        func=cmd_doctor
    )

    bank = sub.add_parser("bank", help="the synthetic Mexican bank")
    bank_sub = bank.add_subparsers(dest="action", required=True)
    build = bank_sub.add_parser("build", help="generate the bank and write Parquet")
    build.add_argument("--customers", type=int, default=50_000, help="genuine customers")
    build.add_argument("--seed", type=int, default=2026)
    build.add_argument("--out", default=None, help="output folder (default: data/processed/bank)")
    build.set_defaults(func=cmd_bank_build)
    check = bank_sub.add_parser("check", help="acceptance checks on a built bank")
    check.add_argument("--customers", type=int, default=50_000, help="customers used in the build")
    check.add_argument("--seed", type=int, default=2026)
    check.add_argument("--dir", default=None, help="folder of the build")
    check.add_argument("--report", default=None, help="write a Markdown report to this path")
    check.set_defaults(func=cmd_bank_check)

    figs = sub.add_parser("figures", help="draw the README figures from a built bank")
    figs.add_argument("--dir", default=None, help="folder of the build")
    figs.add_argument("--out", default="docs/img", help="output folder (default: docs/img)")
    figs.set_defaults(func=cmd_figures)

    lake = sub.add_parser("lake", help="the data platform: landing, lake, marts")
    lake_sub = lake.add_subparsers(dest="action", required=True)
    exp = lake_sub.add_parser("export-landing", help="split a built bank into daily drops")
    exp.add_argument("--bank", default=None, help="folder of the build")
    exp.set_defaults(func=cmd_lake_export)
    load = lake_sub.add_parser("load", help="load pending drops, run contracts, refresh marts")
    load.add_argument("--limit", type=int, default=None, help="load at most this many drops")
    load.add_argument("--force", action="store_true", help="reload every drop")
    load.set_defaults(func=cmd_lake_load)
    lake_sub.add_parser("status", help="ledger and contract summary").set_defaults(
        func=cmd_lake_status
    )
    bench = lake_sub.add_parser("benchmark", help="scaling benchmark on AMLworld")
    bench.add_argument("--dataset", default="HI-Large")
    bench.add_argument("--sizes", type=int, nargs="*", help="row counts (default 1M to full file)")
    bench.add_argument("--queries", nargs="*", help="only these queries")
    bench.add_argument("--repeats", type=int, default=3)
    bench.add_argument("--timeout", type=float, default=900)
    bench.add_argument("--out", default="docs/benchmark-results.csv")
    bench.add_argument("--resume", action="store_true", help="skip pairs already in the CSV")
    bench.add_argument(
        "--memory-limit-gb", type=int, default=None, help="DuckDB cap (default: half of RAM)"
    )
    bench.add_argument("--layouts", action="store_true", help="also compare file layouts")
    bench.add_argument("--layout-rows", type=int, default=50_000_000)
    bench.set_defaults(func=cmd_lake_benchmark)
    brep = lake_sub.add_parser("benchmark-report", help="figures and report from the benchmark CSV")
    brep.add_argument("--csv", default="docs/benchmark-results.csv")
    brep.add_argument("--out", default="docs/benchmark-scaling.md")
    brep.add_argument("--img", default="docs/img")
    brep.set_defaults(func=cmd_lake_benchmark_report)

    fraud = sub.add_parser("fraud", help="fraud detection: features, labels, models")
    fraud_sub = fraud.add_subparsers(dest="action", required=True)
    feat = fraud_sub.add_parser("features", help="build the point-in-time feature store")
    feat.add_argument(
        "--out", default=None, help="folder (default: data/processed/features/fraud_events)"
    )
    feat.add_argument(
        "--first-day", default=None, help="score events from this day (history is always complete)"
    )
    feat.add_argument("--last-day", default=None, help="score events up to this day")
    feat.add_argument(
        "--memory-limit-gb", type=int, default=None, help="DuckDB cap (spills to disk)"
    )
    feat.set_defaults(func=cmd_fraud_features)
    ev = fraud_sub.add_parser("evaluate", help="fit and score the models under the fixed protocol")
    ev.add_argument("--out", default=None, help="folder (default: data/processed/fraud_results)")
    ev.set_defaults(func=cmd_fraud_evaluate)
    rp = fraud_sub.add_parser("report", help="results, intervals, fairness, drift and figures")
    rp.add_argument("--results", default=None, help="folder of the saved scores")
    rp.add_argument("--img", default="docs/img", help="figure folder")
    rp.set_defaults(func=cmd_fraud_report)
    ab = fraud_sub.add_parser("ablation", help="leave-one-feature-family-out on validation")
    ab.add_argument("--results", default=None, help="folder of the saved scores")
    ab.set_defaults(func=cmd_fraud_ablation)
    bf = fraud_sub.add_parser("baf", help="the same pipeline on the BAF benchmark")
    bf.add_argument("--results", default=None, help="folder for baf_results.json")
    bf.set_defaults(func=cmd_fraud_baf)

    risk = sub.add_parser("risk", help="chargeback maturation (IBNR) and the fraud loss model")
    risk_sub = risk.add_subparsers(dest="action", required=True)
    rw = risk_sub.add_parser("worlds", help="calibration across independent simulated banks")
    rw.add_argument("-n", type=int, default=200, help="number of banks (seeds 1 to n)")
    rw.add_argument("--customers", type=int, default=200_000)
    rw.add_argument("--reps-ibnr", type=int, default=200)
    rw.add_argument("--reps-loss", type=int, default=100)
    rw.add_argument("--workers", type=int, default=5)
    rw.add_argument("--out", default=None, help="JSON lines file (resumable)")
    rw.set_defaults(func=cmd_risk_worlds)
    rr = risk_sub.add_parser(
        "report", help="run the protocol on the main bank; results and figures"
    )
    rr.add_argument("--reps", type=int, default=1000)
    rr.add_argument("--results", default="docs/risk-results/results.json")
    rr.add_argument("--img", default="docs/img")
    rr.set_defaults(func=cmd_risk_report)

    exp = sub.add_parser("experiments", help="A/B tests of fraud rules on a bank with known truth")
    exp_sub = exp.add_subparsers(dest="action", required=True)
    eb = exp_sub.add_parser("build", help="build the counterfactual banks (about 2 minutes each)")
    eb.add_argument("--out", default=None)
    eb.add_argument("--force", action="store_true")
    eb.set_defaults(func=cmd_experiments_build)
    er = exp_sub.add_parser("run", help="run the validation studies (about 15 minutes)")
    er.add_argument("--out", default=None)
    er.add_argument("--workers", type=int, default=6)
    er.add_argument(
        "--scale", type=float, default=1.0, help="shrink replication counts (smoke run)"
    )
    er.set_defaults(func=cmd_experiments_run)
    el = exp_sub.add_parser("labels", help="delayed-label readout across independent banks")
    el.add_argument("-n", type=int, default=60)
    el.add_argument("--treatment", default="tight_all")
    el.add_argument("--customers", type=int, default=200_000)
    el.add_argument("--workers", type=int, default=5)
    el.add_argument("--out", default=None)
    el.set_defaults(func=cmd_experiments_labels)
    ep = exp_sub.add_parser("report", help="score the criteria and draw the figures")
    ep.add_argument("--results", default=None, help="folder written by `experiments run`")
    ep.add_argument("--out", default="docs/experiment-results/results.json")
    ep.add_argument("--img", default="docs/img")
    ep.set_defaults(func=cmd_experiments_report)

    ing = sub.add_parser("ingest", help="public benchmark datasets from Kaggle")
    ing_sub = ing.add_subparsers(dest="action", required=True)
    ing_sub.add_parser("list", help="sources, licences and status").set_defaults(
        func=cmd_ingest_list
    )
    rep = ing_sub.add_parser("report", help="fact sheet of the converted datasets")
    rep.add_argument("--out", default="docs/benchmarks.md")
    rep.set_defaults(func=cmd_ingest_report)
    for name, fn, helptext in (
        ("download", cmd_ingest_download, "download files"),
        ("convert", cmd_ingest_convert, "convert downloaded files to Parquet"),
    ):
        sp = ing_sub.add_parser(name, help=helptext)
        sp.add_argument("source", choices=["amlworld", "baf", "sparkov", "ieee_cis"])
        sp.add_argument("--files", nargs="*", help="exact file names")
        sp.add_argument("--all", action="store_true", help="every file of the source")
        sp.add_argument("--sizes", nargs="*", help="AMLworld sizes: small medium large")
        sp.add_argument("--kinds", nargs="*", help="AMLworld kinds: HI LI")
        sp.add_argument("--force", action="store_true")
        sp.set_defaults(func=fn)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
