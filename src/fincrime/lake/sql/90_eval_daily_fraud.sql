-- GROUND TRUTH. Lives in schema `eval`: evaluation code may read it, models never.
delete from eval.daily_fraud where day in (select day from touched);
insert into eval.daily_fraud
select
    a.event_day,
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in'),
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status = 'approved'),
    count(*) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status <> 'approved'),
    coalesce(sum(a.amount) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status = 'approved'), 0),
    coalesce(sum(a.amount) filter (where t.is_fraud and a.event_type <> 'spei_in' and a.status <> 'approved'), 0),
    count(*) filter (where t.crime_class = 'laundering'),
    count(*) filter (where t.crime_class is null and a.status <> 'approved')
from lake.attempts a
join lake.truth t on t.event_id = a.event_id and t.event_day = a.event_day
where a.event_day in (select day from touched)
group by 1;
