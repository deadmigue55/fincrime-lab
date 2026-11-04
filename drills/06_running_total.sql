-- Drill 06 | Window functions | Running total of approved pesos
-- Question: For each day, the approved pesos that day and the total so far.
select
    day,
    approved_amount,
    sum(approved_amount) over (order by day) as running_amount
from mart.daily_kpis
order by day
