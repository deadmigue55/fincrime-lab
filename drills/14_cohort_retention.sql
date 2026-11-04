-- Drill 14 | Cohorts | Monthly cohort retention
-- Question: For accounts grouped by the month they were opened, the share still active (any attempt) in each later month.
with cohort as (
    select account_id, date_trunc('month', opened_on) as cohort_month
    from dim.accounts
),
size as (
    select cohort_month, count(*) as accounts from cohort group by 1
),
activity as (
    select distinct account_id, date_trunc('month', day) as active_month
    from mart.account_day
)
select
    c.cohort_month,
    a.active_month,
    count(*) as active_accounts,
    count(*) * 1.0 / s.accounts as retention
from cohort c
join activity a on a.account_id = c.account_id and a.active_month >= c.cohort_month
join size s on s.cohort_month = c.cohort_month
group by c.cohort_month, a.active_month, s.accounts
order by c.cohort_month, a.active_month
