-- Drill 15 | Funnels | From attempt to outcome
-- Question: Per event type: attempts, how many were challenged with a step-up, how many failed the challenge, how many were declined and how many approved.
select
    event_type,
    count(*) as attempts,
    sum(case when decision = 'step_up' then 1 else 0 end) as challenged,
    sum(case when status = 'stepup_failed' then 1 else 0 end) as challenge_failed,
    sum(case when status = 'declined' then 1 else 0 end) as declined,
    sum(case when status = 'approved' then 1 else 0 end) as approved
from lake.attempts
group by event_type
order by attempts desc
