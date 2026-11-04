-- Drill 12 | Window functions | First outgoing event of each account
-- Question: For every account, what was the type of its first outgoing event? Count accounts per type.
with f as (
    select account_id, event_type,
           row_number() over (partition by account_id order by ts, event_id) as rn
    from lake.attempts
    where direction = 'out'
)
select event_type as first_outgoing_type, count(*) as accounts
from f
where rn = 1
group by event_type
order by accounts desc, first_outgoing_type
