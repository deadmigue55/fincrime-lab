-- Drill 29 | Conditional aggregation | Fraud per 10,000 attempts by hour
-- Question: For each hour of the day, the number of attempts and the fraud events per 10,000 attempts.
select
    extract(hour from a.ts) as hour,
    count(*) as attempts,
    sum(case when t.is_fraud then 1 else 0 end) * 10000.0 / count(*) as fraud_per_10k
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
group by extract(hour from a.ts)
order by hour
