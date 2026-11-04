-- Drill 23 | Sets | Accounts that only send
-- Question: How many accounts sent money out at some point but never received any?
select count(*) as send_only_accounts
from (
    select account_id from mart.account_day where n_out > 0
    except
    select account_id from mart.account_day where n_in > 0
) x
