-- Drops loaded by status. Rows are attempts per drop; a drop that carries only late fraud
-- reports (after the last event day) counts its report rows instead.
select
    status,
    count(*) as drops,
    sum(source_rows) as rows_delivered,
    min(drop_id) as first_drop,
    max(drop_id) as last_drop
from ops.load_ledger
group by status
