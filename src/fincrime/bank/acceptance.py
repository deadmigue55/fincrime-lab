"""Acceptance checks for a built synthetic bank (``methodology.md`` section 4).

Reads the Parquet files with DuckDB, so it works at any scale. Each check compares something
measured on the data with the input that produced it or with a published anchor, and says how
close is close enough. A check that cannot be sourced is marked ``info`` and never fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.identifiers import clabe_is_valid
from fincrime.bank.mexico import STATES, state_tier


@dataclass(frozen=True)
class AcceptanceCheck:
    """One measured check. ``kind`` is ``pass``/``fail`` or ``info`` (reported, never fails)."""

    name: str
    kind: str
    observed: str
    expected: str
    note: str = ""


def _q(con: duckdb.DuckDBPyConnection, sql: str):
    return con.sql(sql).fetchall()


def run_checks(a: Assumptions, folder: Path) -> list[AcceptanceCheck]:
    """Run every acceptance check on the bank in ``folder``."""
    f = folder.as_posix()
    con = duckdb.connect()
    for name in ("customers", "accounts", "episodes", "rings", "fraud_reports", "profiles"):
        con.sql(f"create view {name} as select * from '{f}/{name}.parquet'")
    con.sql(f"create view attempts as select * from '{f}/attempts/*.parquet'")
    out: list[AcceptanceCheck] = []

    def check(name: str, ok: bool, observed: str, expected: str, note: str = "") -> None:
        out.append(AcceptanceCheck(name, "pass" if ok else "fail", observed, expected, note))

    def info(name: str, observed: str, expected: str, note: str = "") -> None:
        out.append(AcceptanceCheck(name, "info", observed, expected, note))

    # --- who the customers are ---------------------------------------------------------------
    n = _q(con, "select count(*) from customers where fraud_role is null")[0][0]
    check("genuine customers", n == a.n_customers, f"{n:,}", f"{a.n_customers:,}")

    share = dict(
        _q(con, "select state, count(*) from customers where fraud_role is null group by 1")
    )
    w = {c: p * a.tier_multiplier[state_tier(c)] for c, (_, p) in STATES.items()}
    total = sum(w.values())
    worst = max(
        abs(share.get(c, 0) - n * v / total) / np.sqrt(n * v / total * (1 - v / total))
        for c, v in w.items()
    )
    check(
        "state mix vs census x adoption tier",
        worst < 5,
        f"max {worst:.1f} sd",
        "< 5 sd",
        "census is checked data; the tier multiplier is an assumption",
    )

    fem = _q(con, "select avg((sex = 'F')::int) from customers where fraud_role is null")[0][0]
    sd = np.sqrt(a.female_share * (1 - a.female_share) / n)
    check(
        "female share", abs(fem - a.female_share) < 4 * sd, f"{fem:.3f}", f"{a.female_share:.3f}",
        "derived from ENIF 2024 (58.6% vs 68.0% hold a savings account)",
    )  # fmt: skip

    lo, hi = _q(
        con,
        "select min(datediff('day', birth_date, onboarded_on)) / 365.25, "
        "max(datediff('day', birth_date, onboarded_on)) / 365.25 "
        "from customers where fraud_role is null",
    )[0]
    check("age at onboarding", lo >= 18 and hi < 71.5, f"{lo:.2f} to {hi:.2f}", "18 to 71")

    # --- identifiers -------------------------------------------------------------------------
    bad_shape = _q(
        con,
        r"select count(*) from customers "
        r"where not regexp_matches(curp, '^[A-Z]{4}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$')",
    )[0][0]
    dup_curp = _q(con, "select count(*) - count(distinct curp) from customers")[0][0]
    dup_clabe = _q(con, "select count(*) - count(distinct clabe) from accounts")[0][0]
    clabes = [r[0] for r in _q(con, "select clabe from accounts using sample 100000 rows")]
    valid = sum(clabe_is_valid(c) for c in clabes)
    check("CURP shape", bad_shape == 0, f"{bad_shape} malformed", "0")
    check(
        "identifiers unique", dup_curp == 0 and dup_clabe == 0, f"{dup_curp}/{dup_clabe} dups", "0"
    )
    check(
        "identifiers invalid by design", valid == 0, f"{valid} of {len(clabes):,} sampled CLABEs valid",
        "0", "no CLABE can belong to a real account",
    )  # fmt: skip

    # --- genuine behaviour vs anchors --------------------------------------------------------
    under = _q(
        con,
        "select avg((amount <= 10000)::int) from attempts "
        "where event_type = 'spei_out' and crime_class is null",
    )[0][0]
    check(
        "SPEI transfers under 10,000 pesos", 0.92 <= under <= 0.97, f"{under:.3f}", "about 0.94",
        "published system-wide share for 2025; reported by an opinion article citing Banxico (to verify)",
    )  # fmt: skip
    internal = _q(
        con,
        "select avg((counterparty_account is not null)::int) from attempts "
        "where event_type = 'spei_out' and crime_class is null",
    )[0][0]
    check(
        "internal share of outgoing transfers", abs(internal - a.internal_transfer_share) < 0.01,
        f"{internal:.3f}", f"{a.internal_transfer_share:.3f}",
    )  # fmt: skip
    ev = _q(con, "select count(*) from attempts where crime_class is null")[0][0]
    months = _q(
        con,
        f"select sum(datediff('day', onboarded_on, date '{a.end_date}') + 1) / 30.4375 "
        "from customers where fraud_role is null",
    )[0][0]
    info(
        "genuine events per active customer-month",
        f"{ev / months:.1f}",
        "none (no source)",
    )

    # --- planted crime: counts follow the declared rates -------------------------------------
    exposure = _q(
        con,
        f"select sum(greatest(datediff('day', onboarded_on, date '{a.end_date}') + 1, 0)) / 365.25 "
        "from customers where fraud_role is null",
    )[0][0]
    for kind, rate in (
        ("ato", a.ato_per_1000_per_year),
        ("card_testing", a.card_testing_per_1000_per_year),
    ):
        expected = rate * exposure / 1000
        got = _q(con, f"select count(*) from episodes where fraud_type = '{kind}'")[0][0]
        check(
            f"{kind} episodes vs declared rate", abs(got - expected) < 4 * np.sqrt(expected) + 3,
            f"{got:,}", f"{expected:,.0f} +- {4 * np.sqrt(expected):,.0f}",
            "episodes whose victim joined too late for the window are skipped",
        )  # fmt: skip
    n_rings = _q(con, "select count(*) from rings")[0][0]
    check(
        "mule rings", n_rings == round(a.mule_rings_per_100k * a.n_customers / 100_000),
        f"{n_rings}", f"{round(a.mule_rings_per_100k * a.n_customers / 100_000)}",
    )  # fmt: skip

    # --- reconciliation ----------------------------------------------------------------------
    dup_ids = _q(con, "select count(*) - count(distinct event_id) from attempts")[0][0]
    orphan = _q(
        con,
        "select count(*) from attempts e left join accounts a on a.account_id = e.account_id "
        "where a.account_id is null",
    )[0][0]
    orphan_cp = _q(
        con,
        "select count(*) from attempts e left join accounts a on a.account_id = e.counterparty_account "
        "where e.counterparty_account is not null and a.account_id is null",
    )[0][0]
    early = _q(
        con,
        "select count(*) from attempts e join accounts a on a.account_id = e.account_id "
        "where e.ts::date < a.opened_on and e.crime_class is null",
    )[0][0]
    check("event ids unique", dup_ids == 0, f"{dup_ids} duplicates", "0")
    check("every event has an account", orphan == 0, f"{orphan} orphans", "0")
    check("every counterparty account exists", orphan_cp == 0, f"{orphan_cp} orphans", "0")
    check("no genuine event before the account opened", early == 0, f"{early} early", "0")

    # --- what the bank sees ------------------------------------------------------------------
    blocked = _q(
        con,
        "select avg((status <> 'approved')::int) from attempts where crime_class is null",
    )[0][0]
    info(
        "genuine attempts blocked or stepped up and failed", f"{blocked * 1e4:.1f} per 10,000",
        "none (no source)", "customer friction of the baseline rules",
    )  # fmt: skip
    fr = _q(
        con,
        "select count(*) filter (where is_fraud and event_type <> 'spei_in'), count(*) from attempts",
    )[0]
    info("fraud attempts per 100,000 attempts", f"{fr[0] / fr[1] * 1e5:.1f}", "none (no source)",
         "prevalence is a named assumption; see sensitivity in the fraud module")  # fmt: skip
    med = _q(
        con,
        "select median(datediff('day', e.ts, r.reported_ts)) from fraud_reports r "
        "join attempts e using (event_id) where r.fraud_type = 'card_testing'",
    )[0][0]
    if med is not None:
        check(
            "card-testing report delay (median days)",
            0.7 * a.report_delay_median_days["card_testing"]
            <= med
            <= 1.3 * a.report_delay_median_days["card_testing"],
            f"{med:.0f}", f"about {a.report_delay_median_days['card_testing']:.0f}",
            "label lag; secondary source says 30-120 days for chargebacks",
        )  # fmt: skip
    return out


def render_markdown(checks: list[AcceptanceCheck], a: Assumptions) -> str:
    """The checks as a Markdown report."""
    n_pass = sum(c.kind == "pass" for c in checks)
    n_fail = sum(c.kind == "fail" for c in checks)
    lines = [
        "# Acceptance report of the synthetic bank",
        "",
        f"Build: {a.n_customers:,} customers, seed {a.seed}, "
        f"{a.start_date} to {a.end_date}. **{n_pass} checks pass, {n_fail} fail**, "
        f"{len(checks) - n_pass - n_fail} are reported for information.",
        "",
        "| Check | Result | Observed | Expected | Note |",
        "|---|---|---|---|---|",
    ]
    mark = {"pass": "pass", "fail": "**FAIL**", "info": "info"}
    lines += [
        f"| {c.name} | {mark[c.kind]} | {c.observed} | {c.expected} | {c.note} |" for c in checks
    ]
    return "\n".join(lines) + "\n"
