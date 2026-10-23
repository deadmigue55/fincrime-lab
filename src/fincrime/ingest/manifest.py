"""A log of every file downloaded or converted: what, from where, when, and its checksum."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RawRecord:
    """A downloaded file."""

    source: str
    file: str
    url: str
    license: str
    bytes: int
    sha256: str
    downloaded_at: str


@dataclass(frozen=True)
class ParquetRecord:
    """A converted file."""

    source: str
    file: str
    parquet: str
    rows: int
    bytes: int
    expected_rows: str
    converted_at: str


def now() -> str:
    """UTC timestamp for the log."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path, chunk: int = 1 << 24) -> str:
    """SHA-256 of a file, read in 16 MB pieces so huge files do not fill memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def read(path: Path, record_type: type) -> list:
    """All records in a manifest file (empty if it does not exist)."""
    if not path.exists():
        return []
    names = [f.name for f in fields(record_type)]
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        values = {k: r[k] for k in names}
        for f in fields(record_type):
            if f.type in (int, "int"):
                values[f.name] = int(values[f.name])
        out.append(record_type(**values))
    return out


def upsert(path: Path, record: RawRecord | ParquetRecord, key: tuple[str, ...]) -> None:
    """Add a record, replacing any earlier one with the same key columns."""
    record_type = type(record)
    existing = [
        r
        for r in read(path, record_type)
        if tuple(getattr(r, k) for k in key) != tuple(getattr(record, k) for k in key)
    ]
    existing.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(record_type)])
        writer.writeheader()
        writer.writerows(asdict(r) for r in existing)
