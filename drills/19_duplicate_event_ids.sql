-- Drill 19 | Data quality | Duplicate event ids
-- Question: Is any event id in the lake used by more than one row? Return one number.
select count(*) - count(distinct event_id) as duplicate_rows
from lake.attempts
