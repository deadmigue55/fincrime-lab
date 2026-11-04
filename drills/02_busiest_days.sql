-- Drill 02 | Aggregation | The five busiest days
-- Question: Which five days had the most attempts? Break ties by the earlier day.
select day, attempts
from mart.daily_kpis
order by attempts desc, day
limit 5
