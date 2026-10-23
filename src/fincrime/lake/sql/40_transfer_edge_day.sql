-- Money moving between two accounts of the bank, per day: the edges of the transfer graph.
delete from mart.transfer_edge_day where day in (select day from touched);
insert into mart.transfer_edge_day
select event_day, account_id, counterparty_account, count(*), sum(amount)
from lake.attempts
where event_day in (select day from touched)
  and event_type = 'spei_out' and status = 'approved' and counterparty_account is not null
group by 1, 2, 3;
