-- Fraud events by type and outcome. Ground truth, so this view is for evaluation, not for a model.
select
    t.fraud_type,
    a.status = 'approved' as got_through,
    count(*) as events,
    sum(a.amount) as pesos
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
where t.is_fraud and a.event_type <> 'spei_in'
group by 1, 2
order by pesos desc
