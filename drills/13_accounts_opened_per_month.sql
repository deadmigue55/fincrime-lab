-- Drill 13 | Cohorts | Accounts opened per month
-- Question: How many accounts were opened each month?
select date_trunc('month', opened_on) as cohort_month, count(*) as accounts
from dim.accounts
group by 1
order by 1
