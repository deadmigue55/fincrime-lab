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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
