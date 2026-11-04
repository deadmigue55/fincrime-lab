-- Drill 27 | Sets | Accounts with at least one blocked event
-- Question: How many accounts have at least one event that was not approved? Use EXISTS, not a join, and say why.
select count(*) as accounts_with_a_block
from dim.accounts a
where exists (
    select 1 from lake.attempts x
    where x.account_id = a.account_id and x.status <> 'approved'
)
