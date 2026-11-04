-- Drill 07 | Window functions | Seven-day moving average
-- Question: Attempts per day and their average over the day and the six days before it. What happens on the first six days?
select
    day,
    attempts,
    avg(attempts) over (order by day rows between 6 preceding and current row) as attempts_7d
from mart.daily_kpis
order by day
