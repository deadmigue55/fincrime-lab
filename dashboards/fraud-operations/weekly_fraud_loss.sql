-- Fraud that got through (lost) and fraud that was stopped (blocked), in pesos per week.
select
    date_trunc('week', day) as week,
    sum(fraud_lost_amount) as lost,
    sum(fraud_blocked_amount) as blocked
from eval.daily_fraud
group by 1
order by 1
