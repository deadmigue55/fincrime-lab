-- Who receives transfers from other customers: number of different senders and pesos received,
-- by the account's true role (ground truth). Mules receive far more money, not more senders.
with received as (
    select dst_account as account_id, count(distinct src_account) as senders, sum(amount) as pesos
    from mart.transfer_edge_day
    group by dst_account
)
select
    coalesce(v.fraud_role, 'genuine') as role,
    count(*) as accounts,
    round(avg(r.senders), 1) as avg_senders,
    round(avg(r.pesos)) as avg_pesos_received
from received r
join eval.accounts v on v.account_id = r.account_id
group by 1
order by 1
