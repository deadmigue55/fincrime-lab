-- Drill 26 | Graph questions | Pairs of accounts sharing a device
-- Question: Which pairs of accounts used the same device on the same day, most often first? Count each pair once.
select
    a.account_id as account_a,
    b.account_id as account_b,
    count(*) as shared_days
from mart.device_account_day a
join mart.device_account_day b
    on a.day = b.day and a.device_id = b.device_id and a.account_id < b.account_id
group by a.account_id, b.account_id
order by shared_days desc, account_a, account_b
limit 10
