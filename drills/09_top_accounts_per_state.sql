-- Drill 09 | Window functions | Top three accounts per state
-- Question: For June 2025, the three accounts with the most pesos sent out in each state.
with m as (
    select a.state, d.account_id, sum(d.amount_out) as amount_out
    from mart.account_day d
    join dim.accounts a on a.account_id = d.account_id
    where d.day >= date '2025-06-01' and d.day < date '2025-07-01'
    group by a.state, d.account_id
),
r as (
    select *, row_number() over (partition by state order by amount_out desc, account_id) as rn
    from m
)
select state, account_id, amount_out, rn
from r
where rn <= 3
order by state, rn
