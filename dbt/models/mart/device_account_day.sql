-- Port of lake/sql/30_device_account_day.sql: which devices were used on which accounts.
select event_day as day, device_id, account_id, count(*) as n_events
from {{ source('lake', 'attempts') }}
where device_id is not null
group by event_day, device_id, account_id
