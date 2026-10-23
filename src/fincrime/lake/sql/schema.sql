-- Schemas of the warehouse. `dim`, `mart` and `ops` are observable-only; `eval` holds ground
-- truth and is the only place models must never read from.
create schema if not exists dim;
create schema if not exists mart;
create schema if not exists eval;
create schema if not exists ops;

create table if not exists ops.load_ledger (
    drop_id        varchar primary key,      -- the arrival date, e.g. 2024-03-05
    status         varchar not null,         -- started, loaded, quarantined
    source_rows    bigint,
    source_sha256  varchar,
    event_days     varchar,                  -- comma-separated event days the drop touched
    message        varchar,
    started_at     timestamp,
    finished_at    timestamp
);

-- Every event id the lake holds, keyed. Contracts look ids up here instead of scanning the lake,
-- so the cost of a drop stays flat as the lake grows.
create table if not exists ops.event_registry (
    event_id   bigint primary key,
    event_day  date,
    drop_id    varchar
);

create table if not exists ops.dq_results (
    drop_id     varchar,
    contract    varchar,
    severity    varchar,                     -- error or warn
    violations  bigint,
    passed      boolean,
    run_at      timestamp
);

create table if not exists mart.daily_kpis (
    day               date,
    attempts          bigint,
    approved          bigint,
    declined          bigint,
    stepup_failed     bigint,
    approved_amount   double,
    outgoing_amount   double,
    active_accounts   bigint
);

create table if not exists mart.account_day (
    day                   date,
    account_id            bigint,
    n_attempts            bigint,
    n_approved            bigint,
    n_out                 bigint,
    n_in                  bigint,
    amount_out            double,
    amount_in             double,
    max_amount_out        double,
    n_night               bigint,
    n_devices             bigint,
    n_counterparties      bigint,
    n_blocked             bigint
);

create table if not exists mart.device_account_day (
    day         date,
    device_id   bigint,
    account_id  bigint,
    n_events    bigint
);

create table if not exists mart.transfer_edge_day (
    day          date,
    src_account  bigint,
    dst_account  bigint,
    n_transfers  bigint,
    amount       double
);

create table if not exists eval.daily_fraud (
    day                 date,
    fraud_attempts      bigint,
    fraud_approved      bigint,
    fraud_blocked       bigint,
    fraud_lost_amount   double,
    fraud_blocked_amount double,
    laundering_events   bigint,
    genuine_blocked     bigint
);
