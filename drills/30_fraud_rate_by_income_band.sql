-- Drill 30 | Joins | Fraud rate by income band
-- Question: Join the events to the accounts and compare the fraud rate across income bands. Is the gap real or an artefact of volume?
select
    d.income_band,
    count(*) as attempts,
    sum(case when t.is_fraud then 1 else 0 end) as fraud_events,
    sum(case when t.is_fraud then 1 else 0 end) * 10000.0 / count(*) as fraud_per_10k
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
join dim.accounts d on d.account_id = a.account_id
group by d.income_band
order by d.income_band
