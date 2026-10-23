"""Turn the raw CSV and text files into typed Parquet that DuckDB and Polars read fast.

AMLworld transactions have two columns both called "Account" and bank codes with leading zeros,
so they are read with explicit names and types. The ``*_Patterns.txt`` files list the planted
laundering schemes in blocks; they are parsed into a table because they are the ground truth
for the AML module (which pattern each laundering transaction belongs to).
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import polars as pl

from fincrime import config
from fincrime.ingest import manifest
from fincrime.ingest.kaggle_client import raw_path
from fincrime.ingest.sources import SOURCES

AML_TRANS_COLUMNS = {
    "ts": "TIMESTAMP",
    "from_bank": "VARCHAR",
    "from_account": "VARCHAR",
    "to_bank": "VARCHAR",
    "to_account": "VARCHAR",
    "amount_received": "DOUBLE",
    "receiving_currency": "VARCHAR",
    "amount_paid": "DOUBLE",
    "payment_currency": "VARCHAR",
    "payment_format": "VARCHAR",
    "is_laundering": "TINYINT",
}
AML_ACCOUNT_COLUMNS = {
    "bank_name": "VARCHAR",
    "bank_id": "VARCHAR",
    "account_number": "VARCHAR",
    "entity_id": "VARCHAR",
    "entity_name": "VARCHAR",
}
_BEGIN = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([^:]+?)\s*(?::\s*(.*))?$")


def _q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _copy(select_sql: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    con = duckdb.connect()
    con.sql(f"copy ({select_sql}) to '{_q(tmp)}' (format parquet, compression zstd)")
    con.close()
    tmp.replace(out)


def count_rows(parquet: Path) -> int:
    """Rows in a Parquet file."""
    return int(duckdb.sql(f"select count(*) from read_parquet('{_q(parquet)}')").fetchall()[0][0])


def csv_to_parquet(
    csv: Path, out: Path, columns: dict[str, str] | None = None, extra: str = ""
) -> None:
    """Convert a CSV. With ``columns`` the header is replaced by those names and types."""
    if columns:
        names = ", ".join(f"'{n}'" for n in columns)
        types = ", ".join(f"'{t}'" for t in columns.values())
        reader = (
            f"read_csv('{_q(csv)}', header=true, names=[{names}], types=[{types}], "
            "timestampformat='%Y/%m/%d %H:%M')"
        )
    else:
        reader = f"read_csv('{_q(csv)}', header=true, sample_size=-1)"
    _copy(f"select *{extra} from {reader}", out)


def parse_patterns(text: str) -> pl.DataFrame:
    """Parse an AMLworld ``Patterns.txt`` into one row per transaction of each scheme."""
    rows: list[dict] = []
    attempt = 0
    current: tuple[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if m := _BEGIN.match(line):
            attempt += 1
            current = (m.group(1).strip(), (m.group(2) or "").strip())
        elif line.startswith("END LAUNDERING ATTEMPT"):
            current = None
        elif current is not None:
            parts = line.split(",")
            if len(parts) != len(AML_TRANS_COLUMNS):
                raise ValueError(f"unexpected pattern row with {len(parts)} fields: {line[:80]}")
            rows.append(
                {
                    "attempt_id": attempt,
                    "pattern_type": current[0],
                    "pattern_detail": current[1],
                    **dict(zip(AML_TRANS_COLUMNS, parts, strict=True)),
                }
            )
    if not rows:
        raise ValueError("no laundering attempts found: is this a Patterns.txt file?")
    df = pl.DataFrame(rows)
    return df.with_columns(
        pl.col("ts").str.to_datetime("%Y/%m/%d %H:%M"),
        pl.col("amount_received").cast(pl.Float64),
        pl.col("amount_paid").cast(pl.Float64),
        pl.col("is_laundering").cast(pl.Int8),
    )


def convert_file(
    source_key: str, filename: str, force: bool = False
) -> manifest.ParquetRecord | None:
    """Convert one raw file to ``data/processed/<source>/``. Returns ``None`` if up to date."""
    source = SOURCES[source_key]
    paths = config.get_paths().ensure()
    src = raw_path(source_key, filename)
    if not src.exists():
        raise FileNotFoundError(
            f"{src} is missing: run `fincrime ingest download {source_key}` first"
        )
    stem = Path(filename).stem.replace(" ", "_")
    out = paths.processed / source.key / f"{stem}.parquet"
    log = paths.processed / "MANIFEST.csv"
    done = {r.file for r in manifest.read(log, manifest.ParquetRecord) if r.source == source.key}
    if out.exists() and filename in done and not force:
        return None

    if source_key == "amlworld" and filename.endswith("_Trans.csv"):
        csv_to_parquet(src, out, AML_TRANS_COLUMNS, extra=", row_number() over () as txn_id")
    elif source_key == "amlworld" and filename.endswith("_accounts.csv"):
        csv_to_parquet(src, out, AML_ACCOUNT_COLUMNS)
    elif source_key == "amlworld" and filename.endswith("_Patterns.txt"):
        out.parent.mkdir(parents=True, exist_ok=True)
        parse_patterns(src.read_text(encoding="utf-8")).write_parquet(out, compression="zstd")
    elif source_key == "baf":
        csv_to_parquet(src, out, extra=f", '{Path(filename).stem}' as variant")
    elif source_key == "sparkov":
        split = "train" if "Train" in filename else "test"
        csv_to_parquet(src, out, extra=f", '{split}' as split")
    else:
        csv_to_parquet(src, out)

    rows = count_rows(out)
    want = source.verified_rows.get(filename)
    flag = "not checked" if want is None else (f"{want} ok" if rows == want else f"{want} MISMATCH")
    record = manifest.ParquetRecord(
        source=source.key,
        file=filename,
        parquet=out.relative_to(paths.data).as_posix(),
        rows=rows,
        bytes=out.stat().st_size,
        expected_rows=flag,
        converted_at=manifest.now(),
    )
    manifest.upsert(log, record, key=("source", "file"))
    return record
