"""Run every drill on the local DuckDB and on Databricks, check the answers agree, and write answers.md.

    uv run --extra databricks python drills/compare.py [drill numbers...]

A drill with a ``.databricks.sql`` file uses it on Databricks; the others run unchanged on both engines.
"""

import math
import sys
import time
from datetime import date, datetime
from pathlib import Path

import duckdb

from fincrime.config import get_paths
from fincrime.warehouse import databricks

HERE = Path(__file__).parent
SHOWN = 5


def norm(v):
    """Make the two engines' values comparable: dates as text, floats to a relative 1e-9."""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if type(v).__name__ == "Decimal":
        return float(v)
    return v


def table(rows):
    return sorted(
        (tuple(norm(v) for v in r) for r in rows),
        key=lambda r: repr([round(v, 6) if isinstance(v, float) else v for v in r]),
    )


def close(a, b):
    """Same rows; floats may differ in the last digits (sums add in a different order on each engine)."""
    return len(a) == len(b) and all(
        len(x) == len(y)
        and all(
            math.isclose(u, v, rel_tol=1e-9, abs_tol=1e-9)
            if isinstance(u, float) and isinstance(v, float)
            else u == v
            for u, v in zip(x, y, strict=True)
        )
        for x, y in zip(a, b, strict=True)
    )


def main(only: set[int]):
    con = duckdb.connect(str(get_paths().database), read_only=True)
    out = [
        "# Answers on the full dataset\n",
        "Every drill returns the same answer in DuckDB and in Databricks (floats compared to a relative 1e-9).",
        "The first rows of each result are shown here.\n",
    ]
    with databricks.session() as run:
        for path in sorted(HERE.glob("[0-9][0-9]_*.sql")):
            if path.name.endswith(".databricks.sql"):
                continue
            n = int(path.name[:2])
            if only and n not in only:
                continue
            dbx_path = path.with_suffix(".databricks.sql")
            t0 = time.time()
            rel = con.sql(path.read_text(encoding="utf-8"))
            local, cols = rel.fetchall(), rel.columns
            t1 = time.time()
            remote = run((dbx_path if dbx_path.exists() else path).read_text(encoding="utf-8"))
            t2 = time.time()
            same = close(table(local), table(remote))
            print(
                f"{n:02d} {path.stem[3:]:<42} rows {len(local):>5} duckdb {t1 - t0:5.1f}s databricks {t2 - t1:5.1f}s {'same' if same else 'DIFFERENT'}"
            )
            if not same:
                print("   duckdb    :", table(local)[:3])
                print("   databricks:", table(remote)[:3])
            title = path.read_text(encoding="utf-8").splitlines()[0].split("|", 2)[2].strip()
            out.append(f"## {n:02d} {title}\n")
            out.append("| " + " | ".join(cols) + " |")
            out.append("|" + "---|" * len(cols))
            for r in local[:SHOWN]:
                out.append(
                    "| "
                    + " | ".join(
                        f"{v:.6g}" if isinstance(v, float) else str(v) for v in map(norm, r)
                    )
                    + " |"
                )
            out.append(
                f"\n{len(local)} row(s). {'Same result in Databricks.' if same else 'DIFFERENT IN DATABRICKS.'}\n"
            )
    if not only:
        (HERE / "answers.md").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main({int(a) for a in sys.argv[1:]})
