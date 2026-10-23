-- One row per account and day: the building block of behavioural features.
delete from mart.account_day where day in (select day from touched);
insert into mart.account_day
select
    event_day,
    account_id,
    count(*),
    count(*) filter (where status = 'approved'),
    count(*) filter (where status = 'approved' and direction = 'out'),
    count(*) filter (where status = 'approved' and direction = 'in'),
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'out'), 0),
    coalesce(sum(amount) filter (where status = 'approved' and direction = 'in'), 0),
    coalesce(max(amount) filter (where status = 'approved' and direction = 'out'), 0),
    count(*) filter (where status = 'approved' and hour(ts) between 0 and 5),
    count(distinct device_id),
    count(distinct coalesce(counterparty_account::varchar, counterparty_bank)),
    count(*) filter (where status <> 'approved')
from lake.attempts
where event_day in (select day from touched)
group by 1, 2;
