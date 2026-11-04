-- Days between a fraudulent event and the customer's report, in 14-day buckets.
-- The last bucket (126) holds 126 days or more.
select
    least(cast(floor(datediff(cast(r.reported_ts as date), a.event_day) / 14) * 14 as int), 126) as days_after_event,
    count(case when r.fraud_type = 'card_testing' then 1 end) as card_testing,
    count(case when r.fraud_type = 'ato' then 1 end) as account_takeover
from lake.reports r
join lake.attempts a on a.event_id = r.event_id
group by 1
order by 1
