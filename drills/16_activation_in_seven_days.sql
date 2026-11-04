-- Drill 16 | Funnels | Activation within a week
-- Question: Of all accounts, how many sent money out within seven days of opening?
with first_out as (
    select account_id, min(day) as first_day
    from mart.account_day
    where n_out > 0
    group by account_id
)
select
    count(*) as accounts,
    sum(case when f.first_day <= a.opened_on + interval 7 day then 1 else 0 end) as activated,
    sum(case when f.first_day <= a.opened_on + interval 7 day then 1 else 0 end) * 1.0 / count(*) as activation_rate
from dim.accounts a
left join first_out f on f.account_id = a.account_id
