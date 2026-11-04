-- Laundering events planted in the simulation, by month.
select date_trunc('month', day) as month, sum(laundering_events) as laundering_events
from eval.daily_fraud
group by 1
order by 1
