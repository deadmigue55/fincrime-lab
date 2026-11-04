-- Percentage of fraud attempts stopped, against genuine blocks per 10,000 attempts, by month.
-- The first weeks are high on friction: at launch every device and beneficiary is new, so the
-- new_device_amount and young_beneficiary rules fire on genuine customers.
select
    date_trunc('month', f.day) as month,
    sum(f.fraud_blocked) / sum(f.fraud_attempts) * 100 as fraud_stopped_pct,
    sum(f.genuine_blocked) / sum(k.attempts) * 10000 as genuine_blocks_per_10k
from eval.daily_fraud f
join mart.daily_kpis k on k.day = f.day
group by 1
order by 1
