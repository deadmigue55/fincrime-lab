-- Port of lake/sql/40_transfer_edge_day.sql: money moving between two accounts, per day.
select
    event_day as day,
    account_id as src_account,
    counterparty_account as dst_account,
    count(*) as n_transfers,
    sum(amount) as amount
from {{ source('lake', 'attempts') }}
where event_type = 'spei_out' and status = 'approved' and counterparty_account is not null
group by event_day, account_id, counterparty_account
