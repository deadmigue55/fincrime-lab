-- Late-data rule: an event can arrive days after it happened, never before. So no attempt may be
-- dated after the last drop that loaded. Returns the offending days; the test passes on zero rows.
select event_day, count(*) as events
from {{ source('lake', 'attempts') }}
where event_day > (
    select max(cast(drop_id as date)) from {{ source('ops', 'load_ledger') }} where status = 'loaded'
)
group by event_day
