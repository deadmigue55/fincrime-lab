-- Attempts declined, and attempts that failed the step-up check, per 10,000 attempts, by week.
-- Metabase: the [[...]] line is a dashboard filter (a field filter). In the Databricks SQL editor, delete that line.
select
    date_trunc('week', day) as week,
    sum(declined) / sum(attempts) * 10000 as declined_per_10k,
    sum(stepup_failed) / sum(attempts) * 10000 as stepup_failed_per_10k
from mart.daily_kpis
[[where {{day}}]]
group by 1
order by 1
