-- Port of lake/sql/20_account_day.sql: one row per account and day.
select
    event_day as day,
    account_id,
    count(*) as n_attempts,
    count(*) filter (where status = 'approved') as n_approved,
    count(*) filter (where status = 'approved' and direction = 'out') as n_out,
    count(*) filter (where status = 'approved' and direction = 'in') as n_in,
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'out'), 0) as amount_out,
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'in'), 0) as amount_in,
    coalesce(max(amount) filter (where status = 'approved' and direction = 'out'), 0) as max_amount_out,
    count(*) filter (where status = 'approved' and hour(ts) between 0 and 5) as n_night,
    count(distinct device_id) as n_devices,
    count(distinct coalesce(cast(counterparty_account as string), counterparty_bank)) as n_counterparties,
    count(*) filter (where status <> 'approved') as n_blocked
from {{ source('lake', 'attempts') }}
group by event_day, account_id
