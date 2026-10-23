-- Bank-wide counts per event day. Rebuilt for the days a drop touched.
delete from mart.daily_kpis where day in (select day from touched);
insert into mart.daily_kpis
select
    event_day,
    count(*),
    count(*) filter (where status = 'approved'),
    count(*) filter (where status = 'declined'),
    count(*) filter (where status = 'stepup_failed'),
    coalesce(sum(amount) filter (where status = 'approved'), 0),
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'out'), 0),
    count(distinct account_id)
from lake.attempts
where event_day in (select day from touched)
group by 1;
