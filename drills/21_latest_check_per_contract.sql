-- Drill 21 | Data quality | Latest result of each data check
-- Question: For each data contract, keep only the most recent run (the table has one row per daily drop).
select contract, severity, violations, passed, run_at
from (
    select *, row_number() over (partition by contract order by run_at desc, drop_id desc) as rn
    from ops.dq_results
) x
where rn = 1
order by contract
