-- Databricks SQL port of lake/sql/30_device_account_day.sql: which devices were used on which accounts.
create or replace table mart.device_account_day as
select event_day as day, device_id, account_id, count(*) as n_events
from lake.attempts
where device_id is not null
group by event_day, device_id, account_id;
