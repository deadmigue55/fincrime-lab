"""Point-in-time fraud features, computed in SQL from the lake.

One row per scored event (every outgoing attempt, approved or not: the model scores *before*
the decision). A feature of event ``f`` uses only events ``e`` of the same account that were
**known** when ``f`` happened:

    e.ts < f.ts   and   e.eff < f.ts

``eff`` is when the scoring system could first see ``e``. Most events are seen the moment they
happen (``eff = ts``); the ~3% that arrive late (the platform's late drops) are seen at the end of
their arrival day (``eff = arrival day + 1``), a conservative assumption. So the store is
correct in *event time* (no future event leaks) and in *knowledge time* (no event is used before
it reached the bank's systems).

How: every window is computed over **all** events by event time (a RANGE frame that stops one
microsecond before ``f``), then the late events that had not yet arrived are subtracted. Counts
and sums can be subtracted exactly, which is why the catalogue only contains counts and sums
(means, ratios and z-scores are derived from them at the end).

Only observable fields are read: the decision, the rule and the status of the event being scored
are never features (they are the outcome of the incumbent policy), and nothing from the ``eval``
schema or the fraud reports is touched; `tests/test_fraud_features.py` checks that.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from fincrime.lake import warehouse

WINDOWS = {"10m": "10 minutes", "1h": "1 hour", "24h": "24 hours", "7d": "7 days", "30d": "30 days"}

# per-event quantities that windows add up
MEASURES = {
    "m_att": "1",
    "m_fail": "(status <> 'approved')::int",
    "m_out": "(status = 'approved' and direction = 'out')::int",
    "m_out_amt": "case when status = 'approved' and direction = 'out' then amount else 0 end",
    "m_out_sq": "case when status = 'approved' and direction = 'out' then amount * amount else 0 end",
    "m_in_amt": "case when status = 'approved' and direction = 'in' then amount else 0 end",
    "m_night": "(hour(ts) between 0 and 5)::int",
}


@dataclass(frozen=True)
class Feature:
    """One column of the store: its name, family and a sentence a reviewer can read."""

    name: str
    family: str
    doc: str


# (name, measure, window): trailing sums over the known events of the same account
WINDOWED = [
    ("n_att_10m", "m_att", "10m", "velocity", "attempts of the account in the last 10 minutes"),
    ("n_att_1h", "m_att", "1h", "velocity", "attempts in the last hour"),
    ("n_att_24h", "m_att", "24h", "velocity", "attempts in the last 24 hours"),
    ("n_att_7d", "m_att", "7d", "velocity", "attempts in the last 7 days"),
    ("n_att_30d", "m_att", "30d", "velocity", "attempts in the last 30 days"),
    (
        "n_fail_1h",
        "m_fail",
        "1h",
        "friction",
        "declined or failed step-up attempts in the last hour",
    ),
    (
        "n_fail_24h",
        "m_fail",
        "24h",
        "friction",
        "declined or failed step-up attempts in the last 24 hours",
    ),
    ("n_out_30d", "m_out", "30d", "baseline", "approved outgoing payments in the last 30 days"),
    ("out_amt_1h", "m_out_amt", "1h", "money", "pesos sent (approved) in the last hour"),
    ("out_amt_24h", "m_out_amt", "24h", "money", "pesos sent in the last 24 hours"),
    ("out_amt_7d", "m_out_amt", "7d", "money", "pesos sent in the last 7 days"),
    ("out_amt_30d", "m_out_amt", "30d", "baseline", "pesos sent in the last 30 days"),
    (
        "out_sq_30d",
        "m_out_sq",
        "30d",
        "baseline",
        "sum of squared amounts sent in 30 days (for the z-score)",
    ),
    ("in_amt_24h", "m_in_amt", "24h", "money", "pesos received (approved) in the last 24 hours"),
    ("in_amt_7d", "m_in_amt", "7d", "money", "pesos received in the last 7 days"),
    (
        "n_night_30d",
        "m_night",
        "30d",
        "baseline",
        "attempts between 00:00 and 05:59 in the last 30 days",
    ),
]
# (name, partition key, doc): counts over the whole known history
CUMULATIVE = [
    ("n_prior", "account_id", "history", "events the account has ever had before this one"),
    (
        "n_prior_dev",
        "account_id, device_id",
        "novelty",
        "earlier events of the account on this device (0 = new device)",
    ),
    (
        "n_prior_cpty",
        "account_id, cpty",
        "novelty",
        "earlier events of the account with this counterparty (0 = new payee)",
    ),
]
DERIVED = [
    Feature("mean_out_30d", "baseline", "average approved payment in the last 30 days"),
    Feature("amount_vs_mean_30d", "baseline", "this amount divided by the 30-day average payment"),
    Feature("amount_z_30d", "baseline", "z-score of this amount against the 30-day payments"),
    Feature("is_new_device", "novelty", "1 if the account never used this device before"),
    Feature("is_new_cpty", "novelty", "1 if the account never paid this counterparty before"),
    Feature(
        "n_other_accts_device", "device_sharing", "other accounts that have used this device before"
    ),
    Feature("night_share_30d", "baseline", "share of the last 30 days' attempts made at night"),
]
STATIC = [
    Feature("amount", "event", "pesos of this attempt"),
    Feature("hour", "event", "hour of day"),
    Feature("dow", "event", "day of week, Monday = 1"),
    Feature("dom", "event", "day of month (pay days)"),
    Feature("is_night", "event", "1 between 00:00 and 05:59"),
    Feature("event_type", "event", "spei_out, card_online, card_pos, atm, codi or bill_pay"),
    Feature("channel", "event", "channel the attempt came through"),
    Feature("mcc_group", "event", "merchant category group for card payments"),
    Feature("counterparty_kind", "event", "person, merchant or bank account"),
    Feature("has_device", "event", "1 if the attempt carries a device id"),
    Feature("account_age_days", "account", "days since the account opened"),
]
# kept for audits, never fed to a model
GROUPS = [
    Feature("grp_state", "group", "state of residence"),
    Feature("grp_income_band", "group", "income band"),
    Feature("grp_age_band", "group", "age band at the time of the event"),
    Feature("grp_sex", "group", "sex as recorded"),
]
KEYS = ("event_id", "ts", "event_day", "account_id")


def catalogue() -> list[Feature]:
    """Every feature column, with its family and description, in output order."""
    return [
        *STATIC,
        *(Feature(n, fam, d) for n, _, _, fam, d in WINDOWED),
        *(Feature(n, fam, d) for n, _, fam, d in CUMULATIVE),
        *DERIVED,
    ]


def model_columns() -> list[str]:
    """Names a model may use (groups and keys excluded)."""
    return [f.name for f in catalogue()]


def _q(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _frame(window: str | None) -> str:
    start = f"interval '{WINDOWS[window]}' preceding" if window else "unbounded preceding"
    return f"range between {start} and interval '1 microsecond' preceding"


def feature_sql(first_day: str | None = None, last_day: str | None = None) -> str:
    """The whole feature query. ``first_day`` / ``last_day`` limit the *scored* rows only; the
    history behind each row is always complete."""
    wins = [
        f"sum({m}) over (partition by account_id order by ts {_frame(w)}) as b_{n}"
        for n, m, w, *_ in WINDOWED
    ]
    wins += [
        f"count(*) over (partition by {key} order by ts {_frame(None)}) as b_{n}"
        for n, key, *_ in CUMULATIVE
    ]
    pend = [
        f"sum(l.{m} * ((f.ts - l.ts) <= interval '{WINDOWS[w]}')::int) as p_{n}"
        for n, m, w, *_ in WINDOWED
    ]
    pend += [
        "count(*) as p_n_prior",
        "count(*) filter (where l.device_id = f.device_id) as p_n_prior_dev",
        "count(*) filter (where l.cpty = f.cpty) as p_n_prior_cpty",
    ]
    known = [f"coalesce(f.b_{n}, 0) - coalesce(p.p_{n}, 0) as {n}" for n, *_ in WINDOWED]
    known += [f"coalesce(f.b_{n}, 0) - coalesce(p.p_{n}, 0) as {n}" for n, *_ in CUMULATIVE]
    days = ""
    if first_day:
        days += f" and event_day >= date '{first_day}'"
    if last_day:
        days += f" and event_day <= date '{last_day}'"
    arrival = "regexp_extract(filename, 'drop-([0-9-]+)-', 1)::date"
    return f"""
with ev as (
    select event_id, ts, event_day, account_id, event_type, channel, direction, amount, status,
           counterparty_kind, device_id, mcc_group,
           coalesce(counterparty_account::varchar, counterparty_bank) as cpty,
           case when {arrival} > event_day then ({arrival} + 1)::timestamp else ts end as eff,
           {", ".join(f"{expr} as {name}" for name, expr in MEASURES.items())}
    from lake.attempts
),
scored as (
    select * from (select ev.*, {", ".join(wins)} from ev)
    where direction = 'out'{days}
),
pending as (  -- late events that had happened but not yet reached the bank when f was scored
    select f.event_id, {", ".join(pend)}
    from scored f
    join ev l on l.account_id = f.account_id and l.eff > l.ts and f.ts > l.ts and f.ts <= l.eff
    group by f.event_id
),
dev_first as (  -- per (device, account): when the scoring system first saw the device on the account
    select device_id, account_id, min(eff) as first_eff from ev where device_id is not null group by 1, 2
),
dev_count as (  -- accounts known on each device as time goes by
    select distinct device_id, first_eff,
           count(*) over (partition by device_id order by first_eff range between unbounded preceding and current row) as n_accts
    from dev_first
),
core as (
    select f.event_id, f.ts, f.event_day, f.account_id,
           f.amount, hour(f.ts) as hour, isodow(f.ts) as dow, day(f.ts) as dom,
           (hour(f.ts) <= 5)::int as is_night,
           f.event_type, f.channel, f.mcc_group, f.counterparty_kind, (f.device_id is not null)::int as has_device,
           date_diff('day', a.opened_on, f.event_day) as account_age_days,
           {", ".join(known)},
           coalesce(d.n_accts, 0) as dev_accts_known,
           f.device_id is not null as dev_present, f.cpty is not null as cpty_present,
           a.state as grp_state, a.income_band as grp_income_band, a.sex as grp_sex,
           case when date_diff('year', a.birth_date, f.event_day) < 25 then '<25'
                when date_diff('year', a.birth_date, f.event_day) < 40 then '25-39'
                when date_diff('year', a.birth_date, f.event_day) < 60 then '40-59' else '60+' end as grp_age_band
    from scored f
    left join pending p using (event_id)
    left join dim.accounts a on a.account_id = f.account_id
    asof left join dev_count d on d.device_id = f.device_id and f.ts > d.first_eff
)
select event_id, ts, event_day, account_id,
       amount, hour, dow, dom, is_night, event_type, channel, mcc_group, counterparty_kind, has_device,
       account_age_days,
       {", ".join(n for n, *_ in WINDOWED)},
       n_prior, n_prior_dev, n_prior_cpty,
       out_amt_30d / nullif(n_out_30d, 0) as mean_out_30d,
       amount / nullif(out_amt_30d / nullif(n_out_30d, 0), 0) as amount_vs_mean_30d,
       (amount - out_amt_30d / nullif(n_out_30d, 0))
         / nullif(sqrt(greatest(out_sq_30d / nullif(n_out_30d, 0) - power(out_amt_30d / nullif(n_out_30d, 0), 2), 0)), 0)
         as amount_z_30d,
       case when dev_present then (n_prior_dev = 0)::int end as is_new_device,
       case when cpty_present then (n_prior_cpty = 0)::int end as is_new_cpty,
       case when dev_present then greatest(dev_accts_known - (n_prior_dev > 0)::int, 0) end as n_other_accts_device,
       n_night_30d / nullif(n_att_30d, 0) as night_share_30d,
       grp_state, grp_income_band, grp_age_band, grp_sex
from core
"""


def connect(lake: Path, landing_dir: Path, memory_limit_gb: int | None = None):
    """An in-memory DuckDB session with the lake views and the account dimension."""
    con = duckdb.connect(":memory:")
    if memory_limit_gb:
        con.execute(f"set memory_limit = '{memory_limit_gb}GB'")
    tmp = lake.parent / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"set temp_directory = '{_q(tmp)}'")
    warehouse.refresh_views(con, lake)
    con.execute("create schema if not exists dim")
    con.execute(
        "create or replace table dim.accounts as "
        f"select * from read_parquet('{_q(landing_dir / 'dim' / 'accounts.parquet')}')"
    )
    return con


def build(
    lake: Path,
    landing_dir: Path,
    out: Path,
    first_day: str | None = None,
    last_day: str | None = None,
    memory_limit_gb: int | None = None,
) -> dict:
    """Write the feature store as Parquet under ``out``, one folder per event month."""
    import shutil

    t0 = time.time()
    con = connect(lake, landing_dir, memory_limit_gb)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    sql = feature_sql(first_day, last_day)
    con.execute(
        f"copy (select *, strftime(event_day, '%Y-%m') as month from ({sql})) to '{_q(out)}' "
        "(format parquet, compression zstd, partition_by (month), overwrite_or_ignore)"
    )
    n = con.execute(f"select count(*) from read_parquet('{_q(out)}/*/*.parquet')").fetchall()[0][0]
    con.close()
    return {"rows": n, "seconds": round(time.time() - t0)}
