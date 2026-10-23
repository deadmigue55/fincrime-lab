"""Planted fraud and laundering, with the bank's rules in the loop.

Five crime types:

* ``ato``: account takeover. An attacker on a new phone drains a victim to a mule account.
* ``card_testing``: tiny online charges to find live cards, then a large purchase.
* ``mule_forward``: a mule moves stolen money on to another bank (laundering).
* ``scatter_gather`` and ``structuring``: laundering inside a mule ring, which the fraud rules
  do not block (anti-money-laundering works after the fact, in module ``aml``).

Fraud attempts go through the :class:`~fincrime.bank.policy.RulePolicy` one at a time, and the
attacker reacts: a blocked takeover is retried smaller, a card tester gives up after a run of
declines. Every attempt is kept with its true label, blocked or not, so the *counterfactual*
(what a blocked attack would have cost) is known. Fraud is only *reported* by a fraction of
victims and only after a delay; that is what the bank observes.

**Same world, different policy.** All randomness of an episode (who, when, how much, which
dice an extra verification or a retry will roll) is drawn *before* the policy is consulted, from
streams that do not depend on outcomes, and event ids are derived from the episode id. So the
same seed under two policies gives the same victims, the same attempts planned and the same
dice; only the policy's decisions differ. That is what lets step 8 run a clean A/B comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from fincrime.bank.assumptions import Assumptions
from fincrime.bank.customers import rng_for
from fincrime.bank.events import ATTACK_OFFSET, EVENT_SCHEMA
from fincrime.bank.policy import RulePolicy
from fincrime.bank.rings import ATTACKER_DEVICE_BASE, ring_device

ATTEMPT_SCHEMA: dict[str, pl.DataType] = {
    **EVENT_SCHEMA,
    "decision": pl.String,
    "rule": pl.String,
    "status": pl.String,
}
REPORT_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Int64,
    "episode_id": pl.Int64,
    "fraud_type": pl.String,
    "reported_ts": pl.Datetime("us"),
}
EPISODE_SCHEMA: dict[str, pl.DataType] = {
    "episode_id": pl.Int64,
    "fraud_type": pl.String,
    "crime_class": pl.String,
    "ring_id": pl.Int64,
    "victim_account": pl.Int64,
    "start_ts": pl.Datetime("us"),
    "n_attempts": pl.Int64,
    "attempted_amount": pl.Float64,
    "lost_amount": pl.Float64,
    "blocked_amount": pl.Float64,
}

EPISODE_ID_SPACE = 4096  # events per episode; ids are ATTACK_OFFSET + episode * 4096 + sequence
AGING_OFFSET = ATTACK_OFFSET + (1 << 45)  # cover activity of mules, outside any episode
_MAX_CASHOUT = 6  # most large purchases a card tester can try after the tests


@dataclass
class AttackResult:
    """Everything the attack simulation produced."""

    attempts: pl.DataFrame
    reports: pl.DataFrame
    episodes: pl.DataFrame


class _Builder:
    """Collects event rows and gives each a stable id."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._seq: dict[int, int] = {}
        self._aging = 0

    def add(self, **kw: object) -> int:
        ep = kw.get("episode_id")
        if ep is None:
            eid = AGING_OFFSET + self._aging
            self._aging += 1
        else:
            n = self._seq.get(int(ep), 0)
            self._seq[int(ep)] = n + 1
            eid = ATTACK_OFFSET + int(ep) * EPISODE_ID_SPACE + n
        row = {
            "event_id": eid,
            "channel": None,
            "counterparty_kind": None,
            "counterparty_account": None,
            "counterparty_bank": None,
            "mcc_group": None,
            "device_id": None,
            "is_fraud": False,
            "fraud_type": None,
            "episode_id": None,
            "crime_class": None,
            "ring_id": None,
            "decision": "approve",
            "rule": None,
            "status": "approved",
        }
        row.update(kw)
        self.rows.append(row)
        return eid

    def frame(self) -> pl.DataFrame:
        if not self.rows:
            return pl.DataFrame(schema=ATTEMPT_SCHEMA)
        return pl.DataFrame(self.rows, schema=ATTEMPT_SCHEMA)


def _days_since_start(frame: pl.DataFrame, column: str, start: object) -> np.ndarray:
    return frame.select((pl.col(column) - pl.lit(start)).dt.total_days()).to_series().to_numpy()


def simulate_attacks(
    a: Assumptions,
    customers: pl.DataFrame,
    profiles: pl.DataFrame,
    rings: pl.DataFrame,
    policy: RulePolicy,
) -> AttackResult:
    """Plant all fraud and laundering and run the fraud attempts through ``policy``."""
    start = datetime.combine(a.start_date, datetime.min.time())
    days = (a.end_date - a.start_date).days + 1
    banks = [f"B{i:02d}" for i in range(1, a.n_external_banks + 1)]
    bank_p = 1.0 / np.arange(1, len(banks) + 1)
    bank_p /= bank_p.sum()
    bank_cdf = np.cumsum(bank_p)

    def bank_at(u: float) -> str:
        return banks[min(int(np.searchsorted(bank_cdf, u)), len(banks) - 1)]

    genuine = customers.filter(pl.col("fraud_role").is_null()).join(profiles, on="customer_id")
    g_ids = genuine["customer_id"].to_numpy()
    g_income = genuine["monthly_income"].to_numpy()
    g_onb = _days_since_start(genuine, "onboarded_on", a.start_date)
    exposure_days = np.maximum(days - g_onb, 0).astype(float)
    exposure_years = exposure_days.sum() / 365.25
    victim_p = exposure_days / exposure_days.sum()

    mules = customers.filter(pl.col("fraud_role") == "mule")
    m_ids = mules["customer_id"].to_numpy()
    m_ring = mules["ring_id"].to_numpy()
    m_onb = _days_since_start(mules, "onboarded_on", a.start_date)

    online_mcc = list(a.mcc_mix_online)
    online_p = np.array(list(a.mcc_mix_online.values()))
    online_p = online_p / online_p.sum()

    b = _Builder()
    reports: list[dict] = []
    episodes: list[dict] = []
    episode_no = 0

    def stamp(day: int, hour: int, extra_seconds: float = 0.0) -> datetime:
        return start + timedelta(days=int(day), hours=int(hour), seconds=float(extra_seconds))

    def maybe_report(
        kind: str, approved: list[tuple[int, datetime]], ep: int, u: float, z: float
    ) -> None:
        if not approved or u >= a.report_prob[kind]:
            return
        delay = a.report_delay_median_days[kind] * np.exp(a.report_delay_sigma * z)
        when = min(t for _, t in approved) + timedelta(days=float(delay))
        reports.extend(
            {"event_id": eid, "episode_id": ep, "fraud_type": kind, "reported_ts": when}
            for eid, _ in approved
        )

    def attempt(
        ts: datetime,
        device_seen_since: datetime,
        history: list[datetime],
        u_step: float,
        **row: object,
    ) -> tuple[int, str]:
        """Run one attacker attempt through the policy and record it."""
        recent = sum(1 for t in history if 0 <= (ts - t).total_seconds() <= 600)
        decision, rule = policy.decide_one(
            event_type=row["event_type"],
            amount=row["amount"],
            hour=ts.hour,
            device_age_minutes=int((ts - device_seen_since).total_seconds() // 60),
            velocity_10m=recent,
            cp_age_days=row.pop("cp_age_days", None),
        )
        status = "approved"
        if decision == "decline":
            status = "declined"
        elif decision == "step_up" and u_step >= a.stepup_pass_attacker:
            status = "stepup_failed"
        history.append(ts)
        eid = b.add(ts=ts, decision=decision, rule=rule, status=status, **row)
        return eid, status

    # ---- account takeover ---------------------------------------------------------------
    r = rng_for(a.seed, "attacks-ato")
    k_max = a.max_attempts_ato
    for _ in range(r.poisson(a.ato_per_1000_per_year * exposure_years / 1000.0)):
        # everything below is drawn first, whatever the policy will decide
        i = int(r.choice(len(g_ids), p=victim_p))
        u_night, h_night, h_day = r.random(), int(r.integers(0, 6)), int(r.integers(6, 24))
        seconds, u_aged, u_mule = r.uniform(0, 3600), r.random(), r.random()
        k_dev, u_clone, bank_u = int(r.integers(0, a.ring_device_pool)), r.random(), r.random()
        income_u = r.uniform(*a.ato_income_range)
        fwd_min = int(r.integers(*a.mule_forward_minutes))
        fwd_bank_u = r.random()
        gaps, u_retry, u_step = r.exponential(8.0, k_max), r.random(k_max), r.random(k_max)
        u_report, z_delay = r.random(), r.normal()
        lo = int(g_onb[i]) + 14
        if lo >= days:
            continue
        day = int(r.integers(lo, days))
        episode_no += 1
        ep = episode_no
        hour = h_night if u_night < 0.55 else h_day
        ts = stamp(day, hour, seconds)
        avail = np.nonzero(m_onb <= day - 3)[0]
        aged = avail[m_onb[avail] <= day - 15] if len(avail) else avail
        if len(aged) and u_aged < a.aged_mule_preference:
            avail = aged  # ring operators "season" accounts to dodge young-beneficiary rules
        if len(avail):
            j = int(avail[int(u_mule * len(avail))])
            mule, ring = int(m_ids[j]), int(m_ring[j])
            device = ring_device(ring, k_dev)
            mule_age = day - int(m_onb[j])
        else:
            j, mule, ring, mule_age = -1, None, None, None
            device = ATTACKER_DEVICE_BASE + ep
        first_ts, history, seen_since = ts, [], ts
        if u_clone < a.ato_clone_device_share:
            device = int(g_ids[i]) * 10 + 1  # a clone of the victim's own phone: not "new"
            seen_since = stamp(int(g_onb[i]), 12)
        amount0 = float(g_income[i] * income_u)
        victim = int(g_ids[i])
        approved: list[tuple[int, datetime]] = []
        n_try, attempted, lost, blocked = 0, 0.0, 0.0, 0.0
        for k in range(k_max):
            amount = round(max(amount0 * 0.5**k, 50.0), 2)
            n_try += 1
            attempted += amount
            eid, status = attempt(
                ts,
                seen_since,
                history,
                float(u_step[k]),
                event_type="spei_out",
                account_id=victim,
                channel="app",
                direction="out",
                amount=amount,
                counterparty_kind="customer" if mule else "person",
                counterparty_account=mule,
                counterparty_bank=None if mule else bank_at(bank_u),
                device_id=device,
                is_fraud=True,
                fraud_type="ato",
                episode_id=ep,
                crime_class="fraud",
                ring_id=ring,
                cp_age_days=mule_age,
            )
            if status == "approved":
                lost += amount
                approved.append((eid, ts))
                if mule:
                    b.add(
                        ts=ts,
                        account_id=mule,
                        event_type="spei_in",
                        channel="app",
                        direction="in",
                        amount=amount,
                        counterparty_kind="customer",
                        counterparty_account=victim,
                        is_fraud=True,
                        fraud_type="ato",
                        episode_id=ep,
                        crime_class="fraud",
                        ring_id=ring,
                    )
                    attempt(
                        ts + timedelta(minutes=float(fwd_min)),
                        stamp(int(m_onb[j]), 12),
                        [],
                        0.5,  # a fixed die: step-up on a mule forward passes half the time
                        event_type="spei_out",
                        account_id=mule,
                        channel="app",
                        direction="out",
                        amount=round(amount * (1 - a.mule_skim), 2),
                        counterparty_kind="person",
                        counterparty_bank=bank_at(fwd_bank_u),
                        device_id=ring_device(ring, 0),
                        fraud_type="mule_forward",
                        episode_id=ep,
                        crime_class="laundering",
                        ring_id=ring,
                    )
                break
            blocked += amount
            if u_retry[k] >= a.retry_prob:
                break
            ts = ts + timedelta(minutes=float(gaps[k]))
        maybe_report("ato", approved, ep, u_report, z_delay)
        episodes.append(
            _episode(ep, "ato", "fraud", ring, victim, first_ts, n_try, attempted, lost, blocked)
        )

    # ---- card testing -------------------------------------------------------------------
    r = rng_for(a.seed, "attacks-card-testing")
    n_slots = a.card_test_count_range[1] + _MAX_CASHOUT
    for _ in range(r.poisson(a.card_testing_per_1000_per_year * exposure_years / 1000.0)):
        i = int(r.choice(len(g_ids), p=victim_p))
        u_night, h_night, h_day = r.random(), int(r.integers(0, 6)), int(r.integers(6, 24))
        seconds = r.uniform(0, 3600)
        n_test = int(r.integers(a.card_test_count_range[0], a.card_test_count_range[1] + 1))
        t_amount = r.uniform(*a.card_test_amount_range, n_slots)
        t_gap = r.uniform(20, 90, n_slots)
        cash_u, cash_gap = r.uniform(*a.cashout_income_range), r.uniform(2, 10, n_slots)
        n_cash = int(r.integers(1, 4)) + 2
        mcc_i = r.choice(len(online_mcc), size=n_slots, p=online_p)
        u_step, u_retry = r.random(n_slots), r.random(n_slots)
        u_report, z_delay = r.random(), r.normal()
        lo = int(g_onb[i]) + 5
        if lo >= days:
            continue
        day = int(r.integers(lo, days))
        episode_no += 1
        ep = episode_no
        hour = h_night if u_night < 0.3 else h_day
        ts = stamp(day, hour, seconds)
        first_ts, history = ts, []
        device = ATTACKER_DEVICE_BASE + ep
        victim = int(g_ids[i])
        tally = {"n": 0, "attempted": 0.0, "lost": 0.0, "blocked": 0.0}
        approved = []
        cursor = {"i": 0}

        def card_attempt(
            when: datetime,
            amount: float,
            *,
            victim: int = victim,
            episode: int = ep,
            fingerprint: int = device,
            t0: datetime = first_ts,
            seen: list[datetime] = history,
            hits: list[tuple[int, datetime]] = approved,
            counts: dict[str, float] = tally,
            pos: dict[str, int] = cursor,
            dice: np.ndarray = u_step,
            mcc: np.ndarray = mcc_i,
        ) -> str:
            s = pos["i"]
            pos["i"] += 1
            counts["n"] += 1
            counts["attempted"] += amount
            eid, status = attempt(
                when,
                t0,
                seen,
                float(dice[s]),
                event_type="card_online",
                account_id=victim,
                channel="card_online",
                direction="out",
                amount=amount,
                counterparty_kind="merchant",
                mcc_group=online_mcc[int(mcc[s])],
                device_id=fingerprint,
                is_fraud=True,
                fraud_type="card_testing",
                episode_id=episode,
                crime_class="fraud",
            )
            if status == "approved":
                counts["lost"] += amount
                hits.append((eid, when))
            else:
                counts["blocked"] += amount
            return status

        any_passed, consecutive, aborted = False, 0, False
        for t in range(n_test):
            status = card_attempt(ts, round(float(t_amount[t]), 2))
            any_passed |= status == "approved"
            consecutive = 0 if status == "approved" else consecutive + 1
            if consecutive >= a.abort_after_declines:
                aborted = True
                break
            ts = ts + timedelta(seconds=float(t_gap[t]))
        if any_passed and not aborted:
            amount = float(g_income[i] * cash_u)
            for c in range(n_cash):
                ts = ts + timedelta(minutes=float(cash_gap[c]))
                status = card_attempt(ts, round(max(amount, 100.0), 2))
                if status == "approved":
                    amount *= 0.8  # keeps spending while the card works
                elif u_retry[c] < a.retry_prob:
                    amount *= 0.5
                else:
                    break
        maybe_report("card_testing", approved, ep, u_report, z_delay)
        episodes.append(
            _episode(
                ep, "card_testing", "fraud", None, victim, first_ts,
                int(tally["n"]), tally["attempted"], tally["lost"], tally["blocked"],
            )
        )  # fmt: skip

    # ---- mule upkeep and laundering inside rings (never depends on the policy) ----------
    r = rng_for(a.seed, "attacks-laundering")
    members: dict[int, list[int]] = {}
    for j, ring_of in enumerate(m_ring):
        members.setdefault(int(ring_of), []).append(j)

    for j in range(len(m_ids)):  # cover activity so mules do not look dormant
        for _ in range(int(r.integers(3, 9))):
            day = int(m_onb[j]) + int(r.integers(1, 21))
            hour, sec, amt = int(r.integers(9, 21)), r.uniform(0, 3600), r.uniform(80, 600)
            if day >= days:
                continue
            b.add(
                ts=stamp(day, hour, sec),
                account_id=int(m_ids[j]),
                event_type="card_pos",
                channel="card_pos",
                direction="out",
                amount=round(float(amt), 2),
                counterparty_kind="merchant",
                mcc_group="grocery",
                fraud_type="mule_aging",
                crime_class="laundering",
                ring_id=int(m_ring[j]),
            )

    def pick_bank() -> str:
        return bank_at(r.random())

    def internal_move(
        ts: datetime, src: int, dst: int, amount: float, ep: int, ring: int, kind: str
    ) -> None:
        device = ring_device(ring, int(r.integers(0, a.ring_device_pool)))
        common = {
            "fraud_type": kind,
            "episode_id": ep,
            "crime_class": "laundering",
            "ring_id": ring,
        }
        b.add(
            ts=ts, account_id=src, event_type="spei_out", channel="app", direction="out",
            amount=amount, counterparty_kind="customer", counterparty_account=dst,
            device_id=device, **common,
        )  # fmt: skip
        b.add(
            ts=ts, account_id=dst, event_type="spei_in", channel="app", direction="in",
            amount=amount, counterparty_kind="customer", counterparty_account=src, **common,
        )  # fmt: skip

    def external_out(ts: datetime, src: int, amount: float, ep: int, ring: int, kind: str) -> None:
        b.add(
            ts=ts, account_id=src, event_type="spei_out", channel="app", direction="out",
            amount=amount, counterparty_kind="person", counterparty_bank=pick_bank(),
            device_id=ring_device(ring, int(r.integers(0, a.ring_device_pool))),
            fraud_type=kind, episode_id=ep, crime_class="laundering", ring_id=ring,
        )  # fmt: skip

    skim = a.mule_skim
    for ring, idx in members.items():
        ring_day = int(m_onb[idx].max()) + 7
        if ring_day >= days - 3 or len(idx) < 4:
            continue
        accounts = [int(m_ids[j]) for j in idx]

        for _ in range(r.poisson(a.scatter_gather_per_ring)):
            episode_no += 1
            ep = episode_no
            day, hour = int(r.integers(ring_day, days - 2)), int(r.integers(8, 20))
            t0 = stamp(day, hour, r.uniform(0, 3600))
            chosen = r.permutation(accounts)
            k = int(min(len(chosen) - 2, r.integers(2, 6)))
            src, sink, mids = int(chosen[0]), int(chosen[1]), [int(x) for x in chosen[2 : 2 + k]]
            total = float(a.laundering_amount_median * np.exp(0.6 * r.normal()))
            b.add(
                ts=t0, account_id=src, event_type="spei_in", channel="app", direction="in",
                amount=round(total, 2), counterparty_kind="person", counterparty_bank=pick_bank(),
                fraud_type="scatter_gather", episode_id=ep, crime_class="laundering", ring_id=ring,
            )  # fmt: skip
            part = total / k
            t_last = t0
            for m in mids:
                t1 = t0 + timedelta(hours=float(r.uniform(1, 4)))
                internal_move(t1, src, m, round(part * (1 - skim), 2), ep, ring, "scatter_gather")
                t2 = t1 + timedelta(hours=float(r.uniform(1, 6)))
                internal_move(
                    t2, m, sink, round(part * (1 - skim) ** 2, 2), ep, ring, "scatter_gather"
                )
                t_last = max(t_last, t2)
            t3 = t_last + timedelta(hours=float(r.uniform(4, 24)))
            external_out(
                t3, sink, round(total * (1 - skim) ** 2 * 0.98, 2), ep, ring, "scatter_gather"
            )
            episodes.append(
                _episode(ep, "scatter_gather", "laundering", ring, None, t0, 0, total, 0.0, 0.0)
            )

        for _ in range(r.poisson(a.structuring_per_ring)):
            episode_no += 1
            ep = episode_no
            d0 = int(r.integers(ring_day, max(ring_day + 1, days - 20)))
            span = int(r.integers(5, 16))
            chosen = r.permutation(accounts)
            n_smurf = int(min(len(chosen) - 1, r.integers(3, 7)))
            sink, smurfs = int(chosen[0]), [int(x) for x in chosen[1 : 1 + n_smurf]]
            totals = dict.fromkeys(smurfs, 0.0)
            for d in range(d0, min(d0 + span, days)):
                for s in smurfs:
                    if r.random() < 0.6:
                        for _n in range(int(r.integers(1, 3))):
                            amount = round(
                                a.structuring_threshold_mxn * float(r.uniform(0.70, 0.98)), 2
                            )
                            totals[s] += amount
                            b.add(
                                ts=stamp(d, int(r.integers(9, 19)), r.uniform(0, 3600)),
                                account_id=s, event_type="cash_in", channel="store", direction="in",
                                amount=amount, counterparty_kind="store",
                                fraud_type="structuring", episode_id=ep, crime_class="laundering",
                                ring_id=ring,
                            )  # fmt: skip
            end = stamp(min(d0 + span, days - 1), 10)
            gathered = 0.0
            for s in smurfs:
                if totals[s] > 0:
                    amt = round(totals[s] * 0.95, 2)
                    when = end + timedelta(hours=float(r.uniform(1, 30)))
                    internal_move(when, s, sink, amt, ep, ring, "structuring")
                    gathered += amt
            if gathered > 0:
                when = end + timedelta(hours=float(r.uniform(30, 60)))
                external_out(when, sink, round(gathered * 0.97, 2), ep, ring, "structuring")
            episodes.append(
                _episode(
                    ep, "structuring", "laundering", ring, None, stamp(d0, 9), 0,
                    sum(totals.values()), 0.0, 0.0,
                )
            )  # fmt: skip

    attempts = b.frame()
    end_exclusive = datetime.combine(a.end_date, datetime.min.time()) + timedelta(days=1)
    attempts = attempts.filter(pl.col("ts") < end_exclusive)  # the window ends; late legs are cut
    return AttackResult(
        attempts=attempts.sort("ts") if attempts.height else attempts,
        reports=pl.DataFrame(reports, schema=REPORT_SCHEMA)
        if reports
        else pl.DataFrame(schema=REPORT_SCHEMA),
        episodes=pl.DataFrame(episodes, schema=EPISODE_SCHEMA)
        if episodes
        else pl.DataFrame(schema=EPISODE_SCHEMA),
    )


def _episode(
    ep: int,
    kind: str,
    crime: str,
    ring: int | None,
    victim: int | None,
    start: datetime,
    n_attempts: int,
    attempted: float,
    lost: float,
    blocked: float,
) -> dict:
    return {
        "episode_id": ep,
        "fraud_type": kind,
        "crime_class": crime,
        "ring_id": ring,
        "victim_account": victim,
        "start_ts": start,
        "n_attempts": n_attempts,
        "attempted_amount": round(attempted, 2),
        "lost_amount": round(lost, 2),
        "blocked_amount": round(blocked, 2),
    }


__all__ = ["ATTEMPT_SCHEMA", "AttackResult", "simulate_attacks"]
