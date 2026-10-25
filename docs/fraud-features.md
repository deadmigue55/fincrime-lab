# Fraud features (step 5)

The feature store the fraud models read: one row per outgoing attempt (27,175,021 rows on the
200,000-customer bank, 1.8 GB of Parquet), built in SQL from the lake and checked three ways for
leakage. Code: `src/fincrime/fraud/features.py`. Build it with `uv run fincrime fraud features`.

## The rule that makes it point-in-time

A feature of event `f` uses only events `e` of the same account that were **known** when `f`
happened:

```
e.ts < f.ts    and    e.eff < f.ts
```

`ts` is event time. `eff` is when the scoring system could first see `e`: its own `ts` for the 97%
of events that arrive in real time, and the **end of its arrival day** for the 3% that arrive 1 to 3
days late (the platform's late drops, step 4). Using event time alone would be correct about the
*world* and wrong about the *bank*: a transaction that reached the bank two days late cannot have
informed a decision made the next morning. This is the bitemporal part of the problem, and the
reason the late-arrival machinery of step 4 exists.

How it is computed: every window runs over all events by event time (a RANGE frame that stops one
microsecond before `f`, so events with the same timestamp never see each other), then the late
events that had happened but not yet arrived are subtracted. Subtraction is exact for counts and
sums, so the catalogue contains only counts and sums; means, ratios and z-scores are derived from
them at the end. Device sharing uses an `ASOF` join on the moment each (device, account) pair was
first seen.

**Assumptions** (named, not measured): on-time events are visible immediately; late events become
visible at the end of their arrival day (conservative); the store has no geolocation features
because the simulator has no geography below the account's state.

## What is in it

37 model columns, grouped below, each described in `features.catalogue()`:

| Family | Columns | Why a fraud analyst cares |
|---|---|---|
| event | amount, hour, day of week and month, night flag, event type, channel, MCC group, counterparty kind, has device | what the attempt looks like on its own |
| velocity | attempts in the last 10 min, 1 h, 24 h, 7 d, 30 d | card-testing bursts |
| friction | declined or failed step-ups in 1 h and 24 h | an attacker probing the rules |
| money | pesos sent in 1 h, 24 h, 7 d; received in 24 h, 7 d | draining and pass-through |
| baseline | 30-day count, sum and sum of squares of payments, derived mean, amount-to-mean ratio, z-score, night share | "is this unusual for this customer" |
| novelty and sharing | earlier events on this device and with this counterparty, new-device and new-payee flags, other accounts seen on the device, account age | takeover and mule signals |

Four audit columns (state, income band, age band, sex) travel with the rows for fairness
reports and are never model inputs. The decision, rule and status of the event being scored are
not in the store: they are the incumbent policy's outcome, and using them would teach a model to
copy the rules it is meant to be compared with.

## Three independent leakage checks (`tests/test_fraud_features.py`)

1. **Oracle.** A slow, obvious Python implementation of the definition is compared with the SQL on
   every outgoing event of a small bank (26,115 events, more than 300 of them late arrivals). Every
   count, sum, novelty flag and device-sharing count matches.
2. **Truncation**, the rule from `methodology.md`: delete every row from a moment T on, rebuild, and
   no feature of an earlier event may move. None does.
3. **Leak guard.** The query text contains no reference to truth, reports or fraud labels, and the
   output has no `decision`, `rule` or `status` column.

## What it found at full scale

The store has exactly one row for each of the 27,175,021 outgoing attempts, no duplicates, and the
planted behaviours show up in the features the way the generator's mechanisms say they should:

| | Account takeover (814 events) | Card testing (7,738) | Everything else (27.2M) |
|---|---|---|---|
| New device | 48.5% | 10.3% | 1.8% |
| Attempts in the previous 10 minutes | 0.29 | 3.87 | 0.013 |
| Median amount vs the account's 30-day average (accounts with 30+ prior events) | 14.5x | 0.08x | 0.43x |
| Other accounts seen on the device | 5.5 | 0 | 0 |

Takeovers are new device, new payee, unusually large and often on a device that other accounts
used (a mule's phone); card testing is a burst of tiny charges. The 30% of takeovers made from a
cloned device (an assumption of the simulator) are the reason new-device is not 100% for takeovers,
and the reason a model that relies on it alone will miss them.

Build time: 737 seconds on a laptop with DuckDB capped at 16 GB (it spills to disk), over the
whole 34.7M-event history, because every window needs the full history behind it.

## Limits

- Counts and sums only: no `max`, distinct counts or medians over windows, because they cannot be
  corrected for late arrivals by subtraction.
- Simulator signal is strong by construction; what a model recovers here says the pipeline is
  right, not how it would do at a bank (see `methodology.md` section 1). Step 6 repeats the
  headline on the public BAF benchmark for that reason.
