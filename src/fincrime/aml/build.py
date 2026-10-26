"""Reduce the bank's events to the weekly tables that anti-money-laundering monitoring reads.

One DuckDB pass over the approved account-to-account and cash events (about 10 million of the
34.7 million attempts) writes five small tables. Everything a detector sees is a function of events
up to the end of the week it is scored in; the truth table is read only by the evaluation.

* ``aw``: per account and week, the counts and pesos in and out, cash-ins and cash-ins just under
  the review line.
* ``edges``: per week, who sent money to whom inside the bank.
* ``dev``: which accounts used which phone in the week.
* ``rules``: the three daily rules (R1 to R3 of docs/methodology.md section 10), as the weeks in
  which each fired. R4 (shared device) needs the lookback and is computed in ``snapshots``.
* ``truth``: the laundering events (type, episode, ring), for scoring only.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

START = "2024-01-01"
LINE = 15_000.0  # the illustrative internal review line (docs/assumptions.md)
NEAR = (0.8, 1.0)  # R1 looks at cash-ins between 80% and 100% of the line
RULES = {"r1": 3, "r2_in": 50_000.0, "r2_share": 0.8, "r3_cp": 4, "r3_amt": 100_000.0}
FLOW_TYPES = ("spei_in", "spei_out", "cash_in")


def _q(p: Path) -> str:
    return p.as_posix().replace("'", "''")


def build(bank_dir: Path, out: Path) -> dict[str, int]:
    """Write the five tables to ``out`` and return their row counts."""
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    src = f"read_parquet('{_q(bank_dir)}/attempts/**/*.parquet', hive_partitioning = false)"
    lo, hi = NEAR
    con.execute(
        f"""
        create temp table ev as
        select account_id, (ts::date - date '{START}') as day, (ts::date - date '{START}') // 7 as w,
               event_type, direction, amount, counterparty_account as cp, device_id,
               (event_type = 'cash_in' and amount >= {lo * LINE} and amount <= {hi * LINE}) as near
        from {src}
        where status = 'approved' and event_type in {FLOW_TYPES}
        """
    )
    n_ev = con.execute("select count(*) from ev").fetchone()[0]
    con.execute(
        """
        create temp table aw as
        select account_id, w,
               count(*) filter (where direction = 'in') as in_n,
               coalesce(sum(amount) filter (where direction = 'in'), 0) as in_amt,
               count(*) filter (where direction = 'out') as out_n,
               coalesce(sum(amount) filter (where direction = 'out'), 0) as out_amt,
               count(*) filter (where event_type = 'cash_in') as cash_n,
               coalesce(sum(amount) filter (where event_type = 'cash_in'), 0) as cash_amt,
               count(*) filter (where near) as near_n,
               count(*) filter (where cp is not null and direction = 'out') as int_out_n,
               coalesce(sum(amount) filter (where cp is not null and direction = 'out'), 0) as int_out_amt
        from ev group by 1, 2
        """
    )
    con.execute(
        """
        create temp table edges as
        select w, account_id as src, cp as dst, count(*) as n, sum(amount) as amt
        from ev where cp is not null and direction = 'out' group by 1, 2, 3
        """
    )
    con.execute(
        """
        create temp table dev as
        select distinct w, device_id, account_id from ev where device_id is not null
        """
    )
    # Daily rules. A record on day x counts for the days x and x+1 (two-day window) or x..x+6
    # (R1's week), so a flag on day d uses only events up to day d.
    r = RULES
    con.execute(
        f"""
        create temp table r1 as
        select account_id, (day + k)::int as day, count(*) as near_n
        from ev, generate_series(0, 6) as t(k) where near group by 1, 2
        having count(*) >= {r["r1"]}
        """
    )
    con.execute(
        f"""
        create temp table r2 as
        select account_id, day,
               sum(amount) filter (where direction = 'in') as in_amt,
               sum(amount) filter (where direction = 'out') as out_amt
        from (select account_id, (day + k)::int as day, direction, amount
              from ev, generate_series(0, 1) as t(k))
        group by 1, 2
        having sum(amount) filter (where direction = 'in') >= {r["r2_in"]}
           and sum(amount) filter (where direction = 'out')
               >= {r["r2_share"]} * sum(amount) filter (where direction = 'in')
        """
    )
    con.execute(
        f"""
        create temp table r3 as
        select account_id, day, count(distinct cp) as n_cp, sum(amount) as amt
        from (select account_id, (day + k)::int as day, cp, amount
              from ev, generate_series(0, 1) as t(k)
              where cp is not null and direction = 'out')
        group by 1, 2
        having count(distinct cp) >= {r["r3_cp"]} and sum(amount) >= {r["r3_amt"]}
        """
    )
    con.execute(
        """
        create temp table rules as
        select account_id, w, bool_or(f = 1) as r1, bool_or(f = 2) as r2, bool_or(f = 3) as r3
        from (
            select account_id, day // 7 as w, 1 as f from r1
            union all select account_id, day // 7, 2 from r2
            union all select account_id, day // 7, 3 from r3
        ) group by 1, 2
        """
    )
    con.execute(
        f"""
        create temp table truth as
        select account_id, (ts::date - date '{START}') // 7 as w, ts, event_type, direction, amount,
               fraud_type, episode_id, ring_id
        from {src} where crime_class = 'laundering' and status = 'approved'
        """
    )
    counts = {}
    for name in ("aw", "edges", "dev", "rules", "truth"):
        con.execute(f"copy {name} to '{_q(out / (name + '.parquet'))}' (format parquet)")
        counts[name] = con.execute(f"select count(*) from {name}").fetchone()[0]
    counts["events"] = n_ev
    return counts
