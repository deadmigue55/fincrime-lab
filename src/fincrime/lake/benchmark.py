"""Scaling benchmark: how query time and memory grow from 1M to 180M rows on this machine.

Protocol (so the numbers can be repeated and trusted):

* Data: IBM AMLworld HI-Large (about 180M transactions), sliced by transaction number, so each
  size is a prefix of the same time-ordered file.
* Each query runs ``REPEATS`` times in the same process; the first run is kept as ``cold`` (file
  cache and buffer pool not yet warm) and the median of all runs is the headline.
* Memory is the peak resident set of the process, sampled every 50 ms while the query runs.
* A query whose first run takes over ``SLOW_FIRST_RUN_S`` seconds is not repeated: its single
  run is both the cold and the median figure.
* A query that exceeds ``TIMEOUT_S`` seconds or runs out of memory is recorded as such, not
  retried with a different setting: a result that does not scale is a result.
* The machine (cores, memory, DuckDB version) is written next to the numbers.
"""

from __future__ import annotations

import csv
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

SIZES = (1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000, None)  # None = all
REPEATS = 3
TIMEOUT_S = 900
SLOW_FIRST_RUN_S = 120  # a query whose first run is slower than this is run once, not repeated

QUERIES: dict[str, str] = {
    "scan_aggregate": (
        "select payment_format, count(*), sum(amount_paid), avg(amount_paid) from {t} group by 1"
    ),
    "high_cardinality_groupby": (
        "select from_account, count(*) n, sum(amount_paid) s from {t} group by 1 order by s desc limit 10"
    ),
    "distinct_counts": (
        "select payment_format, count(distinct from_account), count(distinct to_account) from {t} group by 1"
    ),
    "edge_table": (
        "select count(*), max(n), sum(amount) from "
        "(select from_account, to_account, count(*) n, sum(amount_paid) amount from {t} group by 1, 2)"
    ),
    "join_dimension": (
        "select a.bank_name, count(*) from {t} t join read_parquet('{accounts}') a "
        "on a.account_number = t.from_account and a.bank_id = t.from_bank group by 1 order by 2 desc limit 10"
    ),
    "rolling_24h_velocity": (
        "select max(c) from (select count(*) over (partition by from_account order by ts "
        "range between interval 1 day preceding and current row) c from {t})"
    ),
    "one_day_slice": "select count(*), sum(amount_paid) from {t} where ts >= timestamp '2022-09-05' and ts < timestamp '2022-09-06'",
}


@dataclass(frozen=True)
class Measurement:
    """One query at one size."""

    rows: int
    query: str
    status: str  # ok, timeout, error
    cold_s: float | None
    median_s: float | None
    rows_per_s: float | None
    peak_mb: float | None
    detail: str = ""


class _MemorySampler:
    """Peak resident memory of this process, sampled in a background thread."""

    def __init__(self) -> None:
        import psutil

        self._proc = psutil.Process()
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, self._proc.memory_info().rss)
            time.sleep(0.05)

    def __enter__(self) -> _MemorySampler:
        self.peak = self._proc.memory_info().rss
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


def machine() -> dict[str, str]:
    """What the numbers were measured on."""
    import os
    import platform

    import psutil

    return {
        "cpu_threads": str(os.cpu_count()),
        "memory_gb": f"{psutil.virtual_memory().total / 1e9:.0f}",
        "os": platform.platform(),
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
    }


def run_query(
    con: duckdb.DuckDBPyConnection, sql: str, repeats: int = REPEATS, timeout: float = TIMEOUT_S
):
    """Time a query ``repeats`` times. Returns ``(status, timings, peak_bytes, detail)``."""
    timings: list[float] = []
    status, detail = "ok", ""
    with _MemorySampler() as mem:
        for _ in range(repeats):
            timer = threading.Timer(timeout, con.interrupt)
            timer.start()
            t0 = time.perf_counter()
            try:
                con.execute(sql).fetchall()
            except duckdb.InterruptException:
                status, detail = "timeout", f"over {timeout:.0f}s"
                break
            except duckdb.OutOfMemoryException as err:
                status, detail = "out_of_memory", str(err)[:120]
                break
            except Exception as err:
                status, detail = "error", f"{type(err).__name__}: {str(err)[:120]}"
                break
            finally:
                timer.cancel()
            timings.append(time.perf_counter() - t0)
            if timings[0] > SLOW_FIRST_RUN_S:
                break  # too slow to repeat: the single run is reported (noted in the docs)
    return status, timings, mem.peak, detail


def default_memory_limit_gb() -> int:
    """Half of the machine's memory: leaves room for the OS and lets DuckDB spill to disk instead."""
    import psutil

    return max(2, int(psutil.virtual_memory().total * 0.5 / 1e9))


def benchmark(
    transactions: Path,
    accounts: Path,
    sizes: tuple[int | None, ...] = SIZES,
    queries: dict[str, str] | None = None,
    repeats: int = REPEATS,
    timeout: float = TIMEOUT_S,
    progress=print,
    csv_path: Path | None = None,
    resume: bool = False,
    memory_limit_gb: int | None = None,
    temp_dir: Path | None = None,
) -> list[Measurement]:
    """Run every query on every size and return the measurements.

    Safe for long runs: DuckDB is capped at ``memory_limit_gb`` (default half the machine) and
    spills to ``temp_dir`` beyond it, each measurement is appended to ``csv_path`` as soon as it
    exists, and with ``resume`` the (size, query) pairs already in that file are skipped.
    """
    queries = queries or QUERIES
    con = duckdb.connect()
    limit = memory_limit_gb or default_memory_limit_gb()
    con.execute(f"set memory_limit = '{limit}GB'")
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"set temp_directory = '{temp_dir.as_posix()}'")
    progress(f"DuckDB memory limit {limit} GB")
    done: dict[tuple[int, str], Measurement] = {}
    if resume and csv_path is not None and csv_path.exists():
        done = {(m.rows, m.query): m for m in read_csv(csv_path)}
    total = con.execute(
        f"select count(*) from read_parquet('{transactions.as_posix()}')"
    ).fetchall()[0][0]
    out: list[Measurement] = []
    for size in sizes:
        n = total if size is None or size >= total else size
        con.execute(
            "create or replace view bench as select * from read_parquet("
            f"'{transactions.as_posix()}') where txn_id <= {n}"
        )
        for name, template in queries.items():
            if (n, name) in done:
                out.append(done[(n, name)])
                progress(f"{n:>12,} {name:<26} already measured")
                continue
            sql = template.format(t="bench", accounts=accounts.as_posix())
            status, timings, peak, detail = run_query(con, sql, repeats, timeout)
            med = statistics.median(timings) if timings else None
            m = Measurement(
                rows=n,
                query=name,
                status=status,
                cold_s=timings[0] if timings else None,
                median_s=med,
                rows_per_s=(n / med) if med else None,
                peak_mb=peak / 1e6,
                detail=detail,
            )
            out.append(m)
            if csv_path is not None:
                append_csv(csv_path, m)
            progress(
                f"{n:>12,} {name:<26} {status:<13} "
                + (
                    f"{med:8.2f}s median {m.cold_s:8.2f}s first {peak / 1e9:6.2f} GB"
                    if med
                    else detail
                )
            )
    return out


def append_csv(path: Path, m: Measurement) -> None:
    """Add one measurement to the results file, writing the header if the file is new."""
    fields = list(Measurement.__dataclass_fields__)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if new:
            writer.writeheader()
        writer.writerow({f: getattr(m, f) for f in fields})


def read_csv(path: Path) -> list[Measurement]:
    """Measurements saved by :func:`append_csv`."""

    def num(v: str) -> float | None:
        return float(v) if v not in ("", "None") else None

    with path.open(newline="", encoding="utf-8") as fh:
        return [
            Measurement(
                rows=int(r["rows"]),
                query=r["query"],
                status=r["status"],
                cold_s=num(r["cold_s"]),
                median_s=num(r["median_s"]),
                rows_per_s=num(r["rows_per_s"]),
                peak_mb=num(r["peak_mb"]),
                detail=r["detail"],
            )
            for r in csv.DictReader(fh)
        ]


def write_csv(path: Path, measurements: list[Measurement]) -> None:
    """Save the measurements (replaces the file)."""
    path.unlink(missing_ok=True)
    for m in measurements:
        append_csv(path, m)


def layout_comparison(transactions: Path, work: Path, n: int = 50_000_000) -> list[dict]:
    """The same one-day query on three layouts of the same ``n`` rows.

    * ``sorted file``: one Parquet file in time order, so row-group statistics let the reader skip
      almost everything;
    * ``shuffled file``: the same rows in random order, so every row group might hold the day;
    * ``day partitions``: one folder per day, so the reader opens only that day's files.
    """
    con = duckdb.connect()
    work.mkdir(parents=True, exist_ok=True)
    sorted_file, shuffled_file, parted = (
        work / "sorted.parquet",
        work / "shuffled.parquet",
        work / "by_day",
    )
    src = f"read_parquet('{transactions.as_posix()}')"
    con.execute(
        f"copy (select * from {src} where txn_id <= {n} order by ts) "
        f"to '{sorted_file.as_posix()}' (format parquet, compression zstd)"
    )
    con.execute(
        f"copy (select * from {src} where txn_id <= {n} order by hash(txn_id)) "
        f"to '{shuffled_file.as_posix()}' (format parquet, compression zstd)"
    )
    con.execute(
        f"copy (select *, ts::date as day from {src} where txn_id <= {n}) to '{parted.as_posix()}' "
        "(format parquet, compression zstd, partition_by (day), overwrite_or_ignore)"
    )
    flt = "ts >= timestamp '2022-09-05' and ts < timestamp '2022-09-06'"
    cases = {
        "sorted file": f"select count(*), sum(amount_paid) from read_parquet('{sorted_file.as_posix()}') where {flt}",
        "shuffled file": f"select count(*), sum(amount_paid) from read_parquet('{shuffled_file.as_posix()}') where {flt}",
        "day partitions": (
            f"select count(*), sum(amount_paid) from read_parquet('{parted.as_posix()}/*/*.parquet', "
            "hive_partitioning = true) where day = date '2022-09-05'"
        ),
    }
    rows = []
    for label, sql in cases.items():
        status, timings, _, _ = run_query(con, sql, repeats=3)
        median = statistics.median(timings) if timings else None
        rows.append({"layout": label, "status": status, "median_s": median, "size_mb": None})
    for r, path in zip(rows, (sorted_file, shuffled_file, parted), strict=True):
        files = [path] if path.is_file() else list(path.rglob("*.parquet"))
        r["size_mb"] = sum(f.stat().st_size for f in files) / 1e6
    return rows
