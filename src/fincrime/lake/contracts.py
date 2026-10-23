"""Data contracts: what a daily drop must satisfy before it may enter the lake.

Each contract is a SQL query that counts violating rows over the staged drop (views
``stg_attempts``, ``stg_truth`` and ``stg_reports``), plus the dimension and the lake. An
``error`` contract blocks the drop (it is quarantined and nothing is published); a ``warn``
contract is recorded and lets the drop through. Results are kept in ``ops.dq_results``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

import duckdb

from fincrime.lake import warehouse

STATUSES = "('approved', 'declined', 'stepup_failed')"
DECISIONS = "('approve', 'step_up', 'decline')"
DIRECTIONS = "('in', 'out')"
EVENT_TYPES = "('spei_out', 'spei_in', 'card_pos', 'card_online', 'cash_in', 'atm', 'codi', 'bill_pay', 'payroll')"


@dataclass(frozen=True)
class Contract:
    """One check. ``sql`` returns a single number: how many rows violate it."""

    name: str
    severity: str  # "error" or "warn"
    sql: str
    description: str
    needs: tuple[str, ...] = ()  # lake views that must exist, or the check is skipped


ATTEMPT_CONTRACTS: tuple[Contract, ...] = (
    Contract(
        "keys_not_null",
        "error",
        "select count(*) from stg_attempts where event_id is null or ts is null or account_id is null "
        "or event_type is null or amount is null or status is null or decision is null",
        "required columns are never null",
    ),
    Contract(
        "event_id_unique_in_drop",
        "error",
        "select count(*) - count(distinct event_id) from stg_attempts",
        "an event appears once per drop",
    ),
    Contract(
        "event_id_unique_in_lake",
        "error",
        "select count(*) from stg_attempts s join lake.attempts l on l.event_id = s.event_id "
        "and l.event_day between (select min(ts)::date from stg_attempts) and (select max(ts)::date from stg_attempts) "
        "where l.filename not like '%drop-{drop}-%'",
        "an event already loaded by another drop is not loaded again",
        needs=("attempts",),
    ),
    Contract(
        "amount_positive",
        "error",
        "select count(*) from stg_attempts where amount <= 0",
        "amounts are strictly positive",
    ),
    Contract(
        "allowed_values",
        "error",
        f"select count(*) from stg_attempts where status not in {STATUSES} or decision not in {DECISIONS} "
        f"or direction not in {DIRECTIONS} or event_type not in {EVENT_TYPES}",
        "status, decision, direction and event type come from the agreed vocabularies",
    ),
    Contract(
        "decision_matches_status",
        "error",
        "select count(*) from stg_attempts where (decision = 'decline' and status <> 'declined') "
        "or (decision = 'approve' and status <> 'approved') "
        "or (decision = 'step_up' and status not in ('approved', 'stepup_failed'))",
        "the recorded outcome is possible given the decision",
    ),
    Contract(
        "account_exists",
        "error",
        "select count(*) from stg_attempts s left join dim.accounts a using (account_id) where a.account_id is null",
        "every event belongs to a known account",
    ),
    Contract(
        "not_before_account_opened",
        "error",
        "select count(*) from stg_attempts s join dim.accounts a using (account_id) where s.ts::date < a.opened_on",
        "no activity before the account opened",
    ),
    Contract(
        "not_from_the_future",
        "error",
        "select count(*) from stg_attempts where ts::date > '{drop}'::date",
        "a drop cannot contain events later than its arrival date",
    ),
    Contract(
        "truth_matches_attempts",
        "error",
        "select (select count(*) from stg_attempts s anti join stg_truth t using (event_id)) "
        "+ (select count(*) from stg_truth t anti join stg_attempts s using (event_id))",
        "truth and attempts describe exactly the same events",
    ),
    Contract(
        "late_arrival_share",
        "warn",
        "select case when count(*) filter (where ts::date < '{drop}'::date) > 0.10 * count(*) "
        "then count(*) filter (where ts::date < '{drop}'::date) else 0 end from stg_attempts",
        "more than 10% of a drop is late data (source delays?)",
    ),
)

REPORT_CONTRACTS: tuple[Contract, ...] = (
    Contract(
        "report_references_known_event",
        "error",
        "select count(*) from stg_reports r where r.event_id not in "
        "(select event_id from stg_attempts union all select event_id from lake.attempts)",
        "a fraud report refers to an event the platform has",
        needs=("attempts",),
    ),
)


@dataclass(frozen=True)
class Result:
    """Outcome of one contract on one drop."""

    contract: str
    severity: str
    violations: int
    passed: bool


def volume_check(con: duckdb.DuckDBPyConnection, drop_id: str, rows: int) -> Result | None:
    """Warn when a drop is less than half or more than double the median of the last 7 loads."""
    past = [
        r[0]
        for r in con.execute(
            "select source_rows from ops.load_ledger where status = 'loaded' and drop_id < ? "
            "order by drop_id desc limit 7",
            [drop_id],
        ).fetchall()
    ]
    if len(past) < 3:
        return None
    ratio = rows / max(median(past), 1)
    bad = not 0.5 <= ratio <= 2.0
    return Result("volume_vs_recent_median", "warn", 1 if bad else 0, not bad)


def run(
    con: duckdb.DuckDBPyConnection, drop_id: str, rows: int, has_attempts: bool, has_reports: bool
) -> list[Result]:
    """Run every applicable contract against the staged drop and record the results."""
    contracts = (list(ATTEMPT_CONTRACTS) if has_attempts else []) + (
        list(REPORT_CONTRACTS) if has_reports else []
    )
    results: list[Result] = []
    for c in contracts:
        if any(not warehouse.has_lake(con, kind) for kind in c.needs):
            if c.name == "report_references_known_event":
                # first drop: the only events a report can point at are in this very drop
                sql = "select count(*) from stg_reports r where r.event_id not in (select event_id from stg_attempts)"
            else:
                continue
        else:
            sql = c.sql
        n = int(con.execute(sql.replace("{drop}", drop_id)).fetchall()[0][0])
        results.append(Result(c.name, c.severity, n, n == 0))
    if has_attempts and (vol := volume_check(con, drop_id, rows)) is not None:
        results.append(vol)
    now = datetime.now(UTC).replace(tzinfo=None)
    con.execute("delete from ops.dq_results where drop_id = ?", [drop_id])
    for r in results:
        con.execute(
            "insert into ops.dq_results values (?, ?, ?, ?, ?, ?)",
            [drop_id, r.contract, r.severity, r.violations, r.passed, now],
        )
    return results


def blocking(results: list[Result]) -> list[Result]:
    """The failed ``error`` contracts."""
    return [r for r in results if r.severity == "error" and not r.passed]
