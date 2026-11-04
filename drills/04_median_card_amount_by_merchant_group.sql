-- Drill 04 | Aggregation | Median and mean card amount by merchant group
-- Question: For card purchases, what are the median and the mean amount per merchant group? Why do they differ?
select
    mcc_group,
    percentile_cont(0.5) within group (order by amount) as median_amount,
    avg(amount) as mean_amount,
    count(*) as purchases
from lake.attempts
where event_type in ('card_pos', 'card_online')
group by mcc_group
order by median_amount desc, mcc_group
