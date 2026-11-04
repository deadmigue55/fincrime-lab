-- Drill 05 | Dates | Attempts and money per month
-- Question: Total attempts and approved pesos per calendar month.
select
    date_trunc('month', day) as month,
    sum(attempts) as attempts,
    sum(approved_amount) as approved_amount
from mart.daily_kpis
group by 1
order by 1
