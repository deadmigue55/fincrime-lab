-- The 10 accounts that received the most money from other customers, with their true role.
select
    e.dst_account as account_id,
    coalesce(v.fraud_role, 'genuine') as role,
    count(distinct e.src_account) as distinct_senders,
    round(sum(e.amount)) as pesos_received
from mart.transfer_edge_day e
join eval.accounts v on v.account_id = e.dst_account
group by e.dst_account, coalesce(v.fraud_role, 'genuine')
order by pesos_received desc
limit 10
