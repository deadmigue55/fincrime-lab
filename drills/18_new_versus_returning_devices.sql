-- Drill 18 | Cohorts | New and returning devices per day
-- Question: For each day, how many distinct devices were used and how many were seen for the first time that day?
with first_seen as (
    select device_id, min(day) as first_day
    from mart.device_account_day
    group by device_id
)
select
    d.day,
    count(distinct d.device_id) as devices,
    count(distinct case when d.day = f.first_day then d.device_id end) as new_devices
from mart.device_account_day d
join first_seen f on f.device_id = d.device_id
group by d.day
order by d.day
