-- Two marts built from the same attempts must agree: per day, the sum over accounts equals the
-- daily total. A difference means one model dropped or duplicated rows.
select k.day, k.attempts, a.n_attempts
from {{ ref('daily_kpis') }} k
full join (
    select day, sum(n_attempts) as n_attempts from {{ ref('account_day') }} group by day
) a on a.day = k.day
where k.attempts is distinct from a.n_attempts
