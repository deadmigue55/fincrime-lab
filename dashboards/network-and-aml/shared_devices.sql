-- Devices seen on several accounts on the same day, by number of accounts.
select accounts_on_device, count(*) as device_days
from (
    select day, device_id, count(distinct account_id) as accounts_on_device
    from mart.device_account_day
    group by day, device_id
)
where accounts_on_device >= 2
group by 1
order by 1
