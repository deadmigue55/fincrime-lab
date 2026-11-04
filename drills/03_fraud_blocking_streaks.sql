-- Drill 03 | Gaps and islands | Longest runs of days with blocked fraud
-- Question: Find the five longest runs of consecutive days on which at least one fraud attempt was blocked (days with none break a run).
with d as (
    select day, fraud_blocked > 0 as hit, row_number() over (order by day) as rn_all
    from eval.daily_fraud
),
h as (
    select day, rn_all, row_number() over (order by day) as rn_hit
    from d
    where hit
)
select min(day) as start_day, max(day) as end_day, count(*) as days
from h
group by rn_all - rn_hit
order by days desc, start_day
limit 5
