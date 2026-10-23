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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="fincrime", description=__doc__)
    parser.add_argument("--version", action="version", version=f"fincrime {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check engines, disk space and credentials").set_defaults(
        func=cmd_doctor
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
