-- Drill 24 | Graph questions | Accounts receiving from many senders in a day
-- Question: Which account-days received money from at least four different senders? Show the biggest ten by number of senders, then pesos.
select
    dst_account,
    day,
    count(distinct src_account) as senders,
    sum(amount) as pesos
from mart.transfer_edge_day
group by dst_account, day
having count(distinct src_account) >= 4
order by senders desc, pesos desc, dst_account, day
limit 10
