-- Databricks SQL port of lake/sql/10_daily_kpis.sql: full rebuild instead of per-day refresh.
create or replace table mart.daily_kpis as
select
    event_day as day,
    count(*) as attempts,
    count(*) filter (where status = 'approved') as approved,
    count(*) filter (where status = 'declined') as declined,
    count(*) filter (where status = 'stepup_failed') as stepup_failed,
    coalesce(sum(amount) filter (where status = 'approved'), 0) as approved_amount,
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'out'), 0) as outgoing_amount,
    count(distinct account_id) as active_accounts
from lake.attempts
group by event_day;
