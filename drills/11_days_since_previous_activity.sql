-- Drill 11 | Window functions | Days since an account's previous active day
-- Question: For account 1000, list each active day and how many days passed since the one before. (Dates are where the two engines differ.)
select
    day,
    date_diff('day', lag(day) over (order by day), day) as days_since_previous
from mart.account_day
where account_id = 1000
order by day
