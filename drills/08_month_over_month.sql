-- Drill 08 | Window functions | Month-over-month growth
-- Question: Attempts per month, the change from the previous month and the growth rate. The first month has no previous one: leave it empty.
with m as (
    select date_trunc('month', day) as month, sum(attempts) as attempts
    from mart.daily_kpis
    group by 1
)
select
    month,
    attempts,
    attempts - lag(attempts) over (order by month) as change,
    attempts * 1.0 / lag(attempts) over (order by month) - 1 as growth
from m
order by month
