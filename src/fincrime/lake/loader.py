"""Incremental, idempotent loading of daily drops into the lake and the marts.

Guarantees, each covered by a test:

* **Idempotent**: loading a drop twice gives the same lake and the same marts; a drop whose
  files are unchanged is skipped.
* **Correctable**: if a source re-delivers a drop with different content, its old lake files are
  replaced and the marts for every affected event day are rebuilt.
* **Late data**: a drop may contain events from earlier days; only those event days are rebuilt.
* **Safe**: a drop that fails an ``error`` contract is quarantined and publishes nothing.
* **Resumable**: files are written to a staging folder and moved into place, and the ledger row
  is written last, so a crash leaves a ``started`` drop that the next run simply redoes.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from fincrime.ingest import manifest
from fincrime.lake import contracts, landing, warehouse

KINDS = warehouse.LAKE_KINDS


@dataclass
class DropResult:
    """What happened to one drop."""

    drop_id: str
    status: str  # loaded, skipped, quarantined
    rows: int = 0
    event_days: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    held_back: list[str] = field(default_factory=list)  # reports not loaded because they failed


def _q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Lake:
    """A landing zone, a Parquet lake and a DuckDB warehouse, loaded drop by drop."""

    def __init__(self, landing_dir: Path, lake_dir: Path, db: Path | None):
        self.landing, self.lake, self.db = landing_dir, lake_dir, db
        self.con = warehouse.connect(db, lake_dir)

    def close(self) -> None:
        """Close the warehouse connection."""
        self.con.close()

    # ---- discovery --------------------------------------------------------------------
    def _files(self, kind: str, drop_id: str) -> list[Path]:
        return sorted((self.landing / kind / f"arrival={drop_id}").glob("*.parquet"))

    def _fingerprint(self, drop_id: str) -> str:
        parts = []
        for kind in KINDS:
            for f in self._files(kind, drop_id):
                parts.append(f"{kind}/{f.name}:{manifest.sha256_of(f)}")
        import hashlib

        return hashlib.sha256(chr(10).join(parts).encode()).hexdigest()

    def ledger(self, drop_id: str) -> tuple | None:
        """The ledger row of a drop: ``(status, source_sha256, event_days)``."""
        rows = self.con.execute(
            "select status, source_sha256, event_days from ops.load_ledger where drop_id = ?",
            [drop_id],
        ).fetchall()
        return rows[0] if rows else None

    def pending(self) -> list[str]:
        """Drops that are new, changed since they were loaded, or were interrupted."""
        out = []
        for drop_id in landing.drops(self.landing):
            row = self.ledger(drop_id)
            if row is None or row[0] != "loaded" or row[1] != self._fingerprint(drop_id):
                out.append(drop_id)
        return out

    # ---- loading ----------------------------------------------------------------------
    def _stage(self, drop_id: str) -> dict[str, list[Path]]:
        files = {k: self._files(k, drop_id) for k in KINDS}
        for kind in KINDS:  # never let a previous drop's staging view leak into this one
            self.con.execute(f"drop view if exists stg_{kind}")
        for kind, paths in files.items():
            if paths:
                listing = ", ".join(f"'{_q(p)}'" for p in paths)
                self.con.execute(
                    f"create or replace temp view stg_{kind} as "
                    f"select * from read_parquet([{listing}], hive_partitioning = false)"
                )
        if not files["attempts"]:
            # a drop with only fraud reports: give the contracts an empty attempts view to join to
            self.con.execute(
                "create or replace temp view stg_attempts as select 0::bigint as event_id where false"
            )
        return files

    def _remove_published(self, drop_id: str) -> None:
        for kind in KINDS:
            for old in (self.lake / kind).glob(f"*/drop-{drop_id}-*.parquet"):
                old.unlink()

    def _publish(self, drop_id: str, files: dict[str, list[Path]]) -> None:
        stage = self.lake / "_staging" / drop_id
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        if files["attempts"]:
            self.con.execute(
                f"copy (select *, ts::date as event_day from stg_attempts) to '{_q(stage / 'attempts')}' "
                f"(format parquet, compression zstd, partition_by (event_day), "
                f"filename_pattern 'drop-{drop_id}-{{i}}', overwrite_or_ignore)"
            )
        if files["truth"]:
            self.con.execute(
                f"copy (select t.*, a.ts::date as event_day from stg_truth t join stg_attempts a using (event_id)) "
                f"to '{_q(stage / 'truth')}' (format parquet, compression zstd, partition_by (event_day), "
                f"filename_pattern 'drop-{drop_id}-{{i}}', overwrite_or_ignore)"
            )
        if files["reports"]:
            target = stage / "reports" / f"arrival={drop_id}"
            target.mkdir(parents=True)
            self.con.execute(
                f"copy (select * from stg_reports) to '{_q(target / f'drop-{drop_id}-0.parquet')}' "
                "(format parquet, compression zstd)"
            )
        self._remove_published(drop_id)
        for staged in stage.rglob("*.parquet"):
            dest = self.lake / staged.relative_to(stage)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
        shutil.rmtree(stage, ignore_errors=True)

    def _set_ledger(self, drop_id: str, status: str, **fields: object) -> None:
        row = self.con.execute(
            "select started_at from ops.load_ledger where drop_id = ?", [drop_id]
        ).fetchall()
        started = row[0][0] if row else _now()
        self.con.execute("delete from ops.load_ledger where drop_id = ?", [drop_id])
        self.con.execute(
            "insert into ops.load_ledger values (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                drop_id,
                status,
                fields.get("rows"),
                fields.get("sha"),
                fields.get("event_days"),
                fields.get("message"),
                started,
                _now() if status != "started" else None,
            ],
        )

    def load_drop(self, drop_id: str, force: bool = False) -> DropResult:
        """Validate and load one drop. Safe to call again."""
        files = {k: self._files(k, drop_id) for k in KINDS}
        if not any(files.values()):
            raise FileNotFoundError(f"no landing files for drop {drop_id}")
        sha = self._fingerprint(drop_id)
        before = self.ledger(drop_id)
        if before and before[0] == "loaded" and before[1] == sha and not force:
            return DropResult(drop_id, "skipped")

        self._set_ledger(drop_id, "started", sha=sha)
        files = self._stage(drop_id)
        n_rows = self.con.execute(
            "select count(*) from stg_attempts"
            if files["attempts"]
            else "select count(*) from stg_reports"
        ).fetchall()[0][0]
        results = contracts.run(
            self.con,
            drop_id,
            n_rows,
            has_attempts=bool(files["attempts"]),
            has_reports=bool(files["reports"]),
        )
        bad = contracts.blocking(results)
        warnings = [r.contract for r in results if r.severity == "warn" and not r.passed]
        bad_attempts = [r for r in bad if r.kind == "attempts"]
        if bad_attempts:
            names = [f"{r.contract} ({r.violations})" for r in bad_attempts]
            self._set_ledger(drop_id, "quarantined", rows=n_rows, sha=sha, message="; ".join(names))
            return DropResult(drop_id, "quarantined", n_rows, failed=names, warnings=warnings)
        # A bad reports file holds back only the reports: the transactions are not punished for it.
        held_back = [f"{r.contract} ({r.violations})" for r in bad if r.kind == "reports"]
        if held_back:
            files = {**files, "reports": []}

        self._publish(drop_id, files)
        self.con.execute("delete from ops.event_registry where drop_id = ?", [drop_id])
        if files["attempts"]:
            self.con.execute(
                "insert into ops.event_registry select event_id, ts::date, ? from stg_attempts",
                [drop_id],
            )
        warehouse.refresh_views(self.con, self.lake)
        days: set[str] = set(before[2].split(",")) if before and before[2] else set()
        if files["attempts"]:
            days |= {
                str(r[0])
                for r in self.con.execute("select distinct ts::date from stg_attempts").fetchall()
            }
        day_list = sorted(d for d in days if d)
        warehouse.rebuild_marts(self.con, [date.fromisoformat(d) for d in day_list])
        note = ("reports held back: " + "; ".join(held_back)) if held_back else None
        self._set_ledger(
            drop_id, "loaded", rows=n_rows, sha=sha, event_days=",".join(day_list), message=note
        )
        return DropResult(
            drop_id, "loaded", n_rows, event_days=day_list, warnings=warnings, held_back=held_back
        )

    def load_pending(self, limit: int | None = None, refresh_dims: bool = True) -> list[DropResult]:
        """Load every pending drop in arrival order (optionally only the first ``limit``)."""
        if refresh_dims and (self.landing / "dim" / "accounts.parquet").exists():
            warehouse.refresh_dims(self.con, self.landing)
        results = []
        for drop_id in self.pending()[:limit]:
            results.append(self.load_drop(drop_id))
        return results
