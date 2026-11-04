-- Databricks SQL port of lake/sql/90_eval_daily_fraud.sql. Ground truth: evaluation code only.
create or replace table eval.daily_fraud as
select
    a.event_day as day,
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in') as fraud_attempts,
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status = 'approved') as fraud_approved,
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status <> 'approved') as fraud_blocked,
    coalesce(sum(a.amount) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status = 'approved'), 0) as fraud_lost_amount,
    coalesce(sum(a.amount) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status <> 'approved'), 0) as fraud_blocked_amount,
    count(*) filter (where t.crime_class = 'laundering') as laundering_events,
    count(*) filter (where t.crime_class is null and a.status <> 'approved') as genuine_blocked
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
group by a.event_day;
