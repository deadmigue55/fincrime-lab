# SQL drills on the bank data

Thirty questions of the kind a data-analyst interview asks, each with a worked solution, on the simulated bank's
tables (`lake`, `dim`, `mart`, `eval`, `ops`). Try the question first, then open the file.

- Every solution runs on **DuckDB** and on **Databricks SQL**, and returns the same answer on both. Two drills
  (days between dates, late reports) need a different date function per engine, so they have a
  `.databricks.sql` twin: the differences are the point of those drills.
- [`answers.md`](answers.md) has the first rows of every result on the full dataset (35 million events).
- `python drills/compare.py` reruns everything on both engines and rewrites `answers.md`
  (it needs the data and a Databricks profile, see [the Databricks write-up](../docs/databricks-metabase.md)).
- A test runs every drill on a small simulated bank, so a renamed column breaks CI.
- `eval` holds ground truth and appears here only for evaluation questions.

| # | Topic | Drill | Question |
|---|---|---|---|
| 01 | Aggregation | [Approval rate by event type](01_approval_rate_by_event_type.sql) | For each event type, how many attempts were there and what share were approved? Keep event types with at least 1,000 attempts, biggest first. |
| 02 | Aggregation | [The five busiest days](02_busiest_days.sql) | Which five days had the most attempts? Break ties by the earlier day. |
| 03 | Gaps and islands | [Longest runs of days with blocked fraud](03_fraud_blocking_streaks.sql) | Find the five longest runs of consecutive days on which at least one fraud attempt was blocked (days with none break a run). |
| 04 | Aggregation | [Median and mean card amount by merchant group](04_median_card_amount_by_merchant_group.sql) | For card purchases, what are the median and the mean amount per merchant group? Why do they differ? |
| 05 | Dates | [Attempts and money per month](05_monthly_totals.sql) | Total attempts and approved pesos per calendar month. |
| 06 | Window functions | [Running total of approved pesos](06_running_total.sql) | For each day, the approved pesos that day and the total so far. |
| 07 | Window functions | [Seven-day moving average](07_moving_average.sql) | Attempts per day and their average over the day and the six days before it. What happens on the first six days? |
| 08 | Window functions | [Month-over-month growth](08_month_over_month.sql) | Attempts per month, the change from the previous month and the growth rate. The first month has no previous one: leave it empty. |
| 09 | Window functions | [Top three accounts per state](09_top_accounts_per_state.sql) | For June 2025, the three accounts with the most pesos sent out in each state. |
| 10 | Window functions | [Share of fraud pesos by type](10_fraud_share_by_type.sql) | Pesos in fraud events by fraud type, and each type's share of the total, in one query. |
| 11 | Window functions | [Days since an account's previous active day](11_days_since_previous_activity.sql) (two dialects) | For account 1000, list each active day and how many days passed since the one before. (Dates are where the two engines differ.) |
| 12 | Window functions | [First outgoing event of each account](12_first_outgoing_event.sql) | For every account, what was the type of its first outgoing event? Count accounts per type. |
| 13 | Cohorts | [Accounts opened per month](13_accounts_opened_per_month.sql) | How many accounts were opened each month? |
| 14 | Cohorts | [Monthly cohort retention](14_cohort_retention.sql) | For accounts grouped by the month they were opened, the share still active (any attempt) in each later month. |
| 15 | Funnels | [From attempt to outcome](15_decision_funnel.sql) | Per event type: attempts, how many were challenged with a step-up, how many failed the challenge, how many were declined and how many approved. |
| 16 | Funnels | [Activation within a week](16_activation_in_seven_days.sql) | Of all accounts, how many sent money out within seven days of opening? |
| 17 | Funnels | [Accounts that went quiet](17_churned_accounts.sql) | How many accounts had no activity in the last 30 days of the data, although they were active before? |
| 18 | Cohorts | [New and returning devices per day](18_new_versus_returning_devices.sql) | For each day, how many distinct devices were used and how many were seen for the first time that day? |
| 19 | Data quality | [Duplicate event ids](19_duplicate_event_ids.sql) | Is any event id in the lake used by more than one row? Return one number. |
| 20 | Data quality | [Activity without an account](20_orphan_accounts.sql) | How many rows of daily account activity point to an account that is not in the accounts table? |
| 21 | Data quality | [Latest result of each data check](21_latest_check_per_contract.sql) | For each data contract, keep only the most recent run (the table has one row per daily drop). |
| 22 | Data quality | [How late do fraud reports arrive?](22_late_report_delay.sql) (two dialects) | Fraud reports arrive after the event. Per fraud type, the median and the 90th percentile of days between the event and the arrival of its report. (Dates again differ between engines.) |
| 23 | Sets | [Accounts that only send](23_senders_never_receivers.sql) | How many accounts sent money out at some point but never received any? |
| 24 | Graph questions | [Accounts receiving from many senders in a day](24_receivers_with_many_senders.sql) | Which account-days received money from at least four different senders? Show the biggest ten by number of senders, then pesos. |
| 25 | Graph questions | [Devices shared by several accounts](25_devices_shared_by_accounts.sql) | Which devices were used by three or more different accounts on the same day? |
| 26 | Graph questions | [Pairs of accounts sharing a device](26_account_pairs_sharing_a_device.sql) | Which pairs of accounts used the same device on the same day, most often first? Count each pair once. |
| 27 | Sets | [Accounts with at least one blocked event](27_accounts_with_a_blocked_event.sql) | How many accounts have at least one event that was not approved? Use EXISTS, not a join, and say why. |
| 28 | Conditional aggregation | [Share of events at night](28_night_share_by_event_type.sql) | For each event type, the share of events between midnight and 6 am. |
| 29 | Conditional aggregation | [Fraud per 10,000 attempts by hour](29_fraud_rate_by_hour.sql) | For each hour of the day, the number of attempts and the fraud events per 10,000 attempts. |
| 30 | Joins | [Fraud rate by income band](30_fraud_rate_by_income_band.sql) | Join the events to the accounts and compare the fraud rate across income bands. Is the gap real or an artefact of volume? |
