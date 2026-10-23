-- Which devices were used on which accounts: the raw material of device-sharing graphs.
delete from mart.device_account_day where day in (select day from touched);
insert into mart.device_account_day
select event_day, device_id, account_id, count(*)
from lake.attempts
where event_day in (select day from touched) and device_id is not null
group by 1, 2, 3;
