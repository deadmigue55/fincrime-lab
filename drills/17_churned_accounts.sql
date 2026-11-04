-- Drill 17 | Funnels | Accounts that went quiet
-- Question: How many accounts had no activity in the last 30 days of the data, although they were active before?
with last_seen as (
    select account_id, max(day) as last_day from mart.account_day group by account_id
),
bounds as (
    select max(day) as end_day from mart.daily_kpis
)
select
    count(*) as accounts_ever_active,
    sum(case when l.last_day < b.end_day - interval 30 day then 1 else 0 end) as quiet_accounts
from last_seen l
cross join bounds b
