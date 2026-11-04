-- Days between a fraudulent event and the customer reporting it, by fraud type.
select
    r.fraud_type,
    count(*) as reports,
    percentile_approx(datediff(cast(r.reported_ts as date), a.event_day), 0.5) as median_days,
    percentile_approx(datediff(cast(r.reported_ts as date), a.event_day), 0.95) as p95_days
from lake.reports r
join lake.attempts a on a.event_id = r.event_id
group by r.fraud_type
