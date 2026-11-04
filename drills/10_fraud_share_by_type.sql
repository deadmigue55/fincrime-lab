-- Drill 10 | Window functions | Share of fraud pesos by type
-- Question: Pesos in fraud events by fraud type, and each type's share of the total, in one query.
select
    t.fraud_type,
    sum(a.amount) as pesos,
    sum(a.amount) * 1.0 / sum(sum(a.amount)) over () as share
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
where t.is_fraud and a.event_type <> 'spei_in'
group by t.fraud_type
order by pesos desc
