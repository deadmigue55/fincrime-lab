-- Data contracts: how often each one ran, and how many violations it found.
select contract, severity, count(*) as runs, sum(violations) as violations, sum(case when passed then 0 else 1 end) as failed_runs
from ops.dq_results
group by contract, severity
order by severity, violations desc
