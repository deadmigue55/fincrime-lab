-- Drill 22 | Data quality | How late do fraud reports arrive?
-- Question: Fraud reports arrive after the event. Per fraud type, the median and the 90th percentile of days between the event and the arrival of its report. (Dates again differ between engines.)
-- Databricks SQL version.
select
    r.fraud_type,
    count(*) as reports,
    percentile_cont(0.5) within group (order by datediff(r.arrival, t.event_day)) as median_days,
    percentile_cont(0.9) within group (order by datediff(r.arrival, t.event_day)) as p90_days
from lake.reports r
join lake.truth t on t.event_id = r.event_id
group by r.fraud_type
order by r.fraud_type
