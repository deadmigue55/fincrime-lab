"""Command-line entry point: ``uv run fincrime <command>``."""

from __future__ import annotations

import argparse
import sys

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
