"""Point-in-time correctness of the fraud feature store (step 5).

Three independent checks, because "no leakage" is the claim the whole fraud module rests on:

* an **oracle**: a slow, obvious Python implementation of the definition, compared with the SQL on
  every scored event of a small bank (including events that arrive late);
* **truncation**: delete every row from time T on and rebuild; no feature of an earlier event may
  move (the rule in docs/methodology.md);
* a **leak guard**: the query never touches ground truth, reports, or the incumbent policy's outcome.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.policy import RulePolicy
from fincrime.bank.simulate import build_bank
from fincrime.fraud import features
from fincrime.lake import landing
from fincrime.lake.loader import Lake

A = Assumptions(
    n_customers=2_500,
    start_date=date(2024, 1, 1),
    end_date=date(2024, 2, 5),
    ato_per_1000_per_year=150.0,
    card_testing_per_1000_per_year=200.0,
    mule_rings_per_100k=200.0,
)
WIN = {
    "10m": timedelta(minutes=10),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A small bank loaded into a lake, and its feature store."""
    root = tmp_path_factory.mktemp("features")
    build_bank(A, RulePolicy(), root / "bank")
    landing.export_landing(root / "bank", root / "landing")
    lake = Lake(root / "landing", root / "lake", None)
    lake.load_pending()
    lake.close()
    out = root / "store"
    info = features.build(root / "lake", root / "landing", out)
    return root, out, info


def read_store(out: Path, where: str = "true"):
    con = duckdb.connect()
    cur = con.execute(
        f"select * from read_parquet('{out.as_posix()}/*/*.parquet') where {where} order by event_id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def raw_events(root: Path):
    """Every event of the lake with the moment the scoring system could first see it."""
    con = features.connect(root / "lake", root / "landing")
    cur = con.execute(
        "select event_id, ts, event_day, account_id, direction, amount, status, device_id, "
        "coalesce(counterparty_account::varchar, counterparty_bank) as cpty, "
        "regexp_extract(filename, 'drop-([0-9-]+)-', 1)::date as arrival from lake.attempts"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    con.close()
    for r in rows:
        late = r["arrival"] > r["event_day"]
        r["eff"] = (
            datetime.combine(r["arrival"] + timedelta(days=1), datetime.min.time())
            if late
            else r["ts"]
        )
    return rows


def oracle(rows, f):
    """The definition, written the obvious slow way."""
    known = [
        e
        for e in rows
        if e["account_id"] == f["account_id"] and e["ts"] < f["ts"] and e["eff"] < f["ts"]
    ]
    out = {}
    for w, delta in WIN.items():
        inside = [e for e in known if e["ts"] >= f["ts"] - delta]
        out[f"n_att_{w}"] = len(inside)
        out[f"n_fail_{w}"] = sum(e["status"] != "approved" for e in inside)
        sent = [e for e in inside if e["status"] == "approved" and e["direction"] == "out"]
        got = [e for e in inside if e["status"] == "approved" and e["direction"] == "in"]
        out[f"out_amt_{w}"] = sum(e["amount"] for e in sent)
        out[f"n_out_{w}"] = len(sent)
        out[f"out_sq_{w}"] = sum(e["amount"] ** 2 for e in sent)
        out[f"in_amt_{w}"] = sum(e["amount"] for e in got)
        out[f"n_night_{w}"] = sum(0 <= e["ts"].hour <= 5 for e in inside)
    out["n_prior"] = len(known)
    out["n_prior_dev"] = (
        sum(e["device_id"] == f["device_id"] for e in known) if f["device_id"] is not None else None
    )
    out["n_prior_cpty"] = (
        sum(e["cpty"] == f["cpty"] for e in known) if f["cpty"] is not None else None
    )
    return out


def test_every_feature_equals_the_slow_definition(world):
    root, out, _ = world
    rows = raw_events(root)
    by_account = defaultdict(list)
    for e in rows:
        by_account[e["account_id"]].append(e)
    stored = {r["event_id"]: r for r in read_store(out)}
    scored = [e for e in rows if e["direction"] == "out"]
    assert set(stored) == {e["event_id"] for e in scored}  # one row per outgoing attempt, none lost
    assert len(scored) > 8_000
    # late events exist and are the reason the oracle needs arrival, so make sure we exercise them
    assert sum(e["eff"] > e["ts"] for e in rows) > 300

    # device sharing: other accounts whose first sighting on the device was already known
    first_seen = defaultdict(lambda: defaultdict(lambda: None))
    for e in rows:
        if e["device_id"] is not None:
            cur = first_seen[e["device_id"]][e["account_id"]]
            first_seen[e["device_id"]][e["account_id"]] = (
                e["eff"] if cur is None else min(cur, e["eff"])
            )

    mism = []
    for f in scored:
        mine = [e for e in by_account[f["account_id"]]]
        want = oracle(mine, f)
        got = stored[f["event_id"]]
        for k, v in want.items():
            name = {"n_out_30d": "n_out_30d"}.get(k, k)
            if name not in got:  # the store keeps only the windows the catalogue names
                continue
            if v is None:
                continue
            if not math.isclose(got[name], v, rel_tol=1e-9, abs_tol=1e-6):
                mism.append((f["event_id"], name, got[name], v))
        if f["device_id"] is not None:
            others = sum(
                1
                for a, t in first_seen[f["device_id"]].items()
                if a != f["account_id"] and t < f["ts"]
            )
            if got["n_other_accts_device"] != others:
                mism.append(
                    (f["event_id"], "n_other_accts_device", got["n_other_accts_device"], others)
                )
            assert got["is_new_device"] == int(want["n_prior_dev"] == 0)
        else:
            assert got["is_new_device"] is None
        if f["cpty"] is not None:
            assert got["is_new_cpty"] == int(want["n_prior_cpty"] == 0)
    assert not mism, mism[:10]


def test_nothing_moves_when_the_future_is_deleted(world):
    root, out, _ = world
    cut = datetime(2024, 1, 18, 13, 30)
    con = features.connect(root / "lake", root / "landing")
    con.execute(
        "create or replace view lake.attempts as select * from "
        f"read_parquet('{(root / 'lake').as_posix()}/attempts/*/*.parquet', hive_partitioning = true, filename = true) "
        f"where ts < timestamp '{cut}'"
    )
    cur = con.execute(f"select * from ({features.feature_sql()}) order by event_id")
    cols = [d[0] for d in cur.description]
    short = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    con.close()
    full = read_store(out, f"ts < timestamp '{cut}'")
    assert len(short) == len(full) > 5_000
    for a, b in zip(short, full, strict=True):
        for k, v in a.items():
            w = b[k]
            same = (
                (v is None and w is None)
                or (
                    isinstance(v, float)
                    and w is not None
                    and math.isclose(v, w, rel_tol=1e-9, abs_tol=1e-6)
                )
                or v == w
            )
            assert same, (a["event_id"], k, v, w)  # floats: sums add in a different order


def test_the_query_cannot_see_truth_reports_or_the_policy_outcome(world):
    sql = features.feature_sql().lower()
    for word in (
        "lake.truth",
        "lake.reports",
        "eval.",
        "is_fraud",
        "fraud_type",
        "episode_id",
        "ring_id",
    ):
        assert word not in sql
    # decision / rule / status of the event being scored are outcomes of the incumbent policy
    _, out, _ = world
    cols = set(read_store(out)[0])
    assert {"decision", "rule", "status"}.isdisjoint(cols)
    names = [f.name for f in features.catalogue()]
    assert len(names) == len(set(names))
    assert set(names) | {f.name for f in features.GROUPS} | set(features.KEYS) | {"month"} == cols


def test_the_features_carry_the_planted_signal(world):
    """Smoke test: card-testing bursts show up as velocity (takeover novelty is checked at full scale)."""
    root, out, _ = world
    rows = (
        duckdb.connect()
        .execute(
            f"""
        select t.fraud_type, avg(s.n_att_10m), count(*)
        from read_parquet('{out.as_posix()}/*/*.parquet') s
        left join read_parquet('{(root / "lake").as_posix()}/truth/*/*.parquet') t using (event_id)
        where s.event_type = 'card_online'
        group by 1
        """
        )
        .fetchall()
    )
    stats = {r[0]: r for r in rows}
    assert stats["card_testing"][2] > 100
    assert stats["card_testing"][1] > 5 * stats[None][1]
