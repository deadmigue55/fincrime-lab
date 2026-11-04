-- Drill 20 | Data quality | Activity without an account
-- Question: How many rows of daily account activity point to an account that is not in the accounts table?
select count(*) as orphan_rows
from mart.account_day d
left join dim.accounts a on a.account_id = d.account_id
where a.account_id is null
