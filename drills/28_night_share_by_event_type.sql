-- Drill 28 | Conditional aggregation | Share of events at night
-- Question: For each event type, the share of events between midnight and 6 am.
select
    event_type,
    count(*) as events,
    sum(case when extract(hour from ts) < 6 then 1 else 0 end) * 1.0 / count(*) as night_share
from lake.attempts
group by event_type
order by night_share desc, event_type
