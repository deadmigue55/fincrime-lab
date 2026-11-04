"""A fingerprint of every table, computed with plain SQL so DuckDB and Databricks can both run it.

Row count, and per column: nulls, plus sum/min/max (numbers), distinct and total length (text),
true count (booleans), min/max (dates and timestamps). Two copies of a table with equal
fingerprints agree on every one of those; ``compare`` lists each difference.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

Run = Callable[[str], Sequence[Sequence[Any]]]

_CATEGORY = {
    "TINYINT": "int", "SMALLINT": "int", "INTEGER": "int", "BIGINT": "int", "HUGEINT": "int",
    "DOUBLE": "float", "FLOAT": "float", "VARCHAR": "text", "BOOLEAN": "bool",
    "DATE": "date", "TIMESTAMP": "ts",
}  # fmt: skip
FLOAT_TOLERANCE = 1e-9  # engines add doubles in different orders


def category(duckdb_type: str) -> str:
    """Map a DuckDB column type to the comparison family."""
    try:
        return _CATEGORY[duckdb_type.upper()]
    except KeyError as exc:  # a new type must be handled on purpose, not skipped
        raise ValueError(f"no manifest rule for column type {duckdb_type}") from exc


def _aggregates(col: str, cat: str) -> list[tuple[str, str]]:
    nulls = ("nulls", f"count(*) - count({col})")
    if cat == "int":
        return [nulls, ("sum", f"sum(cast({col} as decimal(38,0)))"), ("min", f"min({col})"),
                ("max", f"max({col})"), ("distinct", f"count(distinct {col})")]  # fmt: skip
    if cat == "float":
        return [nulls, ("sum", f"sum({col})"), ("min", f"min({col})"), ("max", f"max({col})")]
    if cat == "text":
        return [nulls, ("distinct", f"count(distinct {col})"),
                ("length", f"sum(cast(length({col}) as decimal(38,0)))")]  # fmt: skip
    if cat == "bool":
        return [nulls, ("true", f"sum(case when {col} then 1 else 0 end)")]
    return [nulls, ("min", f"cast(min({col}) as string)"), ("max", f"cast(max({col}) as string)")]


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def fingerprint(run: Run, relation: str, columns: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """One scan of ``relation``; ``columns`` is ``[(name, category), ...]``."""
    parts = [("n", "count(*)")]
    plan: list[tuple[str, str]] = []
    for col, cat in columns:
        for stat, expr in _aggregates(col, cat):
            plan.append((col, stat))
            parts.append((f"{col}__{stat}", expr))
    sql = "select " + ", ".join(f"{expr} as {alias}" for alias, expr in parts) + f" from {relation}"
    row = run(sql)[0]
    out: dict[str, Any] = {"rows": int(row[0]), "columns": {c: {"category": k} for c, k in columns}}
    for (col, stat), value in zip(plan, row[1:], strict=True):
        out["columns"][col][stat] = _plain(value)
    return out


def _same(a: Any, b: Any, cat: str, stat: str) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if cat in {"date", "ts"} and stat in {"min", "max"}:
        return datetime.fromisoformat(str(a)) == datetime.fromisoformat(str(b))
    if cat == "float":
        return math.isclose(float(a), float(b), rel_tol=FLOAT_TOLERANCE, abs_tol=1e-6)
    return a == b


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Every difference between two fingerprints of the same table (empty list = identical)."""
    diffs = []
    if before["rows"] != after["rows"]:
        diffs.append(f"rows: {before['rows']} vs {after['rows']}")
    for col, stats in before["columns"].items():
        other = after["columns"].get(col)
        if other is None:
            diffs.append(f"{col}: missing")
            continue
        for stat, value in stats.items():
            if stat != "category" and not _same(value, other.get(stat), stats["category"], stat):
                diffs.append(f"{col}.{stat}: {value} vs {other.get(stat)}")
    return diffs


def save(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
