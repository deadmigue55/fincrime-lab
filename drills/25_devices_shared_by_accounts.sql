-- Drill 25 | Graph questions | Devices shared by several accounts
-- Question: Which devices were used by three or more different accounts on the same day?
select day, device_id, count(distinct account_id) as accounts
from mart.device_account_day
group by day, device_id
having count(distinct account_id) >= 3
order by accounts desc, day, device_id
limit 10
