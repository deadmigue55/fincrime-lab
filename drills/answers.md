# Answers on the full dataset

Every drill returns the same answer in DuckDB and in Databricks (floats compared to a relative 1e-9).
The first rows of each result are shown here.

## 01 Approval rate by event type

| event_type | attempts | approval_rate |
|---|---|---|
| card_pos | 12798079 | 1 |
| spei_out | 5692575 | 0.999099 |
| card_online | 4984374 | 0.999419 |
| spei_in | 4977341 | 1 |
| atm | 1706163 | 1 |

9 row(s). Same result in Databricks.

## 02 The five busiest days

| day | attempts |
|---|---|
| 2025-05-31 | 298493 |
| 2025-06-30 | 262960 |
| 2025-04-30 | 230651 |
| 2025-06-15 | 227849 |
| 2025-02-28 | 224996 |

5 row(s). Same result in Databricks.

## 03 Longest runs of days with blocked fraud

| start_day | end_day | days |
|---|---|---|
| 2025-03-22 | 2025-06-15 | 86 |
| 2025-01-08 | 2025-02-07 | 31 |
| 2024-10-17 | 2024-11-10 | 25 |
| 2024-11-12 | 2024-12-04 | 23 |
| 2025-02-09 | 2025-03-02 | 22 |

5 row(s). Same result in Databricks.

## 04 Median and mean card amount by merchant group

| mcc_group | median_amount | mean_amount | purchases |
|---|---|---|---|
| marketplace | 235.94 | 567.161 | 1742686 |
| subscriptions | 235.93 | 565.77 | 747862 |
| travel | 235.85 | 565.401 | 398903 |
| utilities | 235.25 | 563.322 | 249694 |
| digital_goods | 235.1 | 565.012 | 498620 |

14 row(s). Same result in Databricks.

## 05 Attempts and money per month

| month | attempts | approved_amount |
|---|---|---|
| 2024-01-01 | 90459 | 1.74142e+08 |
| 2024-02-01 | 250143 | 4.51639e+08 |
| 2024-03-01 | 456270 | 7.85789e+08 |
| 2024-04-01 | 613321 | 1.05979e+09 |
| 2024-05-01 | 835865 | 1.44245e+09 |

18 row(s). Same result in Databricks.

## 06 Running total of approved pesos

| day | approved_amount | running_amount |
|---|---|---|
| 2024-01-01 | 263262 | 263262 |
| 2024-01-02 | 361231 | 624493 |
| 2024-01-03 | 468632 | 1.09312e+06 |
| 2024-01-04 | 580061 | 1.67319e+06 |
| 2024-01-05 | 884119 | 2.5573e+06 |

547 row(s). Same result in Databricks.

## 07 Seven-day moving average

| day | attempts | attempts_7d |
|---|---|---|
| 2024-01-01 | 229 | 229 |
| 2024-01-02 | 378 | 303.5 |
| 2024-01-03 | 417 | 341.333 |
| 2024-01-04 | 582 | 401.5 |
| 2024-01-05 | 887 | 498.6 |

547 row(s). Same result in Databricks.

## 08 Month-over-month growth

| month | attempts | change | growth |
|---|---|---|---|
| 2024-01-01 | 90459 | None | None |
| 2024-02-01 | 250143 | 159684 | 1.76526 |
| 2024-03-01 | 456270 | 206127 | 0.824037 |
| 2024-04-01 | 613321 | 157051 | 0.344206 |
| 2024-05-01 | 835865 | 222544 | 0.362851 |

18 row(s). Same result in Databricks.

## 09 Top three accounts per state

| state | account_id | amount_out | rn |
|---|---|---|---|
| AS | 200232 | 400392 | 1 |
| AS | 23301 | 376167 | 2 |
| AS | 153246 | 299716 | 3 |
| BC | 83218 | 1.05712e+06 | 1 |
| BC | 36778 | 570064 | 2 |

96 row(s). Same result in Databricks.

## 10 Share of fraud pesos by type

| fraud_type | pesos | share |
|---|---|---|
| ato | 1.05228e+07 | 0.684397 |
| card_testing | 4.85249e+06 | 0.315603 |

2 row(s). Same result in Databricks.

## 11 Days since an account's previous active day

| day | days_since_previous |
|---|---|
| 2024-01-10 | None |
| 2024-01-13 | 3 |
| 2024-01-21 | 8 |
| 2024-01-28 | 7 |
| 2024-01-31 | 3 |

68 row(s). Same result in Databricks.

## 12 First outgoing event of each account

| first_outgoing_type | accounts |
|---|---|
| card_pos | 93180 |
| spei_out | 42364 |
| card_online | 34444 |
| atm | 12540 |
| bill_pay | 11207 |

6 row(s). Same result in Databricks.

## 13 Accounts opened per month

| cohort_month | accounts |
|---|---|
| 2024-01-01 | 7960 |
| 2024-02-01 | 7899 |
| 2024-03-01 | 8677 |
| 2024-04-01 | 8711 |
| 2024-05-01 | 9142 |

18 row(s). Same result in Databricks.

## 14 Monthly cohort retention

| cohort_month | active_month | active_accounts | retention |
|---|---|---|---|
| 2024-01-01 | 2024-01-01 | 7473 | 0.938819 |
| 2024-01-01 | 2024-02-01 | 7871 | 0.988819 |
| 2024-01-01 | 2024-03-01 | 7889 | 0.99108 |
| 2024-01-01 | 2024-04-01 | 7873 | 0.98907 |
| 2024-01-01 | 2024-05-01 | 7891 | 0.991332 |

171 row(s). Same result in Databricks.

## 15 From attempt to outcome

| event_type | attempts | challenged | challenge_failed | declined | approved |
|---|---|---|---|---|---|
| card_pos | 12798079 | 0 | 0 | 0 | 12798079 |
| spei_out | 5692575 | 151143 | 5130 | 0 | 5687445 |
| card_online | 4984374 | 5566 | 389 | 2505 | 4981480 |
| spei_in | 4977341 | 0 | 0 | 0 | 4977341 |
| atm | 1706163 | 0 | 0 | 0 | 1706163 |

9 row(s). Same result in Databricks.

## 16 Activation within a week

| accounts | activated | activation_rate |
|---|---|---|
| 200390 | 165572 | 0.826249 |

1 row(s). Same result in Databricks.

## 17 Accounts that went quiet

| accounts_ever_active | quiet_accounts |
|---|---|
| 199331 | 1029 |

1 row(s). Same result in Databricks.

## 18 New and returning devices per day

| day | devices | new_devices |
|---|---|---|
| 2024-01-01 | 62 | 62 |
| 2024-01-02 | 108 | 81 |
| 2024-01-03 | 139 | 103 |
| 2024-01-04 | 177 | 123 |
| 2024-01-05 | 261 | 152 |

547 row(s). Same result in Databricks.

## 19 Duplicate event ids

| duplicate_rows |
|---|
| 0 |

1 row(s). Same result in Databricks.

## 20 Activity without an account

| orphan_rows |
|---|
| 0 |

1 row(s). Same result in Databricks.

## 21 Latest result of each data check

| contract | severity | violations | passed | run_at |
|---|---|---|---|---|
| account_exists | error | 0 | True | 2026-10-23T12:51:15.037570 |
| allowed_values | error | 0 | True | 2026-10-23T12:51:15.037570 |
| amount_positive | error | 0 | True | 2026-10-23T12:51:15.037570 |
| decision_matches_status | error | 0 | True | 2026-10-23T12:51:15.037570 |
| event_id_unique_in_drop | error | 0 | True | 2026-10-23T12:51:15.037570 |

14 row(s). Same result in Databricks.

## 22 How late do fraud reports arrive?

| fraud_type | reports | median_days | p90_days |
|---|---|---|---|
| ato | 300 | 2 | 5 |
| card_testing | 3822 | 29 | 87 |

2 row(s). Same result in Databricks.

## 23 Accounts that only send

| send_only_accounts |
|---|
| 2258 |

1 row(s). Same result in Databricks.

## 24 Accounts receiving from many senders in a day

| dst_account | day | senders | pesos |
|---|---|---|---|
| 200092 | 2025-01-05 | 5 | 1.58734e+06 |
| 200345 | 2024-11-26 | 5 | 821374 |
| 200111 | 2024-10-30 | 5 | 781532 |
| 200134 | 2024-03-26 | 5 | 398667 |
| 200018 | 2024-11-02 | 5 | 305628 |

10 row(s). Same result in Databricks.

## 25 Devices shared by several accounts

| day | device_id | accounts |
|---|---|---|
| 2024-11-01 | 7000000290 | 6 |
| 2024-03-22 | 7000000470 | 5 |
| 2024-03-26 | 7000000161 | 5 |
| 2024-06-26 | 7000000281 | 5 |
| 2024-11-27 | 7000000450 | 5 |

10 row(s). Same result in Databricks.

## 26 Pairs of accounts sharing a device

| account_a | account_b | shared_days |
|---|---|---|
| 200083 | 200085 | 4 |
| 200171 | 200174 | 4 |
| 200248 | 200252 | 4 |
| 200016 | 200017 | 3 |
| 200027 | 200028 | 3 |

10 row(s). Same result in Databricks.

## 27 Accounts with at least one blocked event

| accounts_with_a_block |
|---|
| 5572 |

1 row(s). Same result in Databricks.

## 28 Share of events at night

| event_type | events | night_share |
|---|---|---|
| payroll | 1447514 | 0.886201 |
| bill_pay | 1424006 | 0.0674358 |
| spei_in | 4977341 | 0.0527912 |
| spei_out | 5692575 | 0.0407801 |
| atm | 1706163 | 0.0281608 |

9 row(s). Same result in Databricks.

## 29 Fraud per 10,000 attempts by hour

| hour | attempts | fraud_per_10k |
|---|---|---|
| 0 | 150921 | 33.9913 |
| 1 | 254064 | 19.6014 |
| 2 | 367015 | 14.3863 |
| 3 | 457570 | 10.2935 |
| 4 | 507169 | 11.1994 |

24 row(s). Same result in Databricks.

## 30 Fraud rate by income band

| income_band | attempts | fraud_events | fraud_per_10k |
|---|---|---|---|
| high | 1410955 | 319 | 2.26088 |
| low | 10273352 | 2899 | 2.82186 |
| lower_mid | 11151786 | 2741 | 2.4579 |
| mid | 8030256 | 2006 | 2.49805 |
| upper_mid | 3873331 | 919 | 2.37263 |

5 row(s). Same result in Databricks.
