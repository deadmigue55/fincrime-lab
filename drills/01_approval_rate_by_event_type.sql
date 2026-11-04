-- Drill 01 | Aggregation | Approval rate by event type
-- Question: For each event type, how many attempts were there and what share were approved? Keep event types with at least 1,000 attempts, biggest first.
select
    event_type,
    count(*) as attempts,
    sum(case when status = 'approved' then 1 else 0 end) * 1.0 / count(*) as approval_rate
from lake.attempts
group by event_type
having count(*) >= 1000
order by attempts desc
