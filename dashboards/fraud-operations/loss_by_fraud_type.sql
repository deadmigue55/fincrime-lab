-- Fraud events by type and outcome. Ground truth, so this view is for evaluation, not for a model.
-- Metabase: the [[...]] line is a dashboard filter (a field filter). In the Databricks SQL editor, delete that line.
select
    t.fraud_type,
    a.status = 'approved' as got_through,
    count(*) as events,
    sum(a.amount) as pesos
from (select * from lake.attempts
[[where {{event_day}}]]
) a
join (select * from lake.truth where is_fraud
[[and {{fraud_type}}]]
) t on t.event_id = a.event_id and t.event_day = a.event_day
where a.event_type <> 'spei_in'
group by 1, 2
order by pesos desc
