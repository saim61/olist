-- =====================================================================
-- raw, staging, and pipeline metadata
-- =====================================================================
drop table if exists meta.pipeline_runs cascade;
drop table if exists meta.watermarks    cascade;

create table meta.pipeline_runs (
    run_id        bigint      not null,
    step          text        not null,
    started_at    timestamptz not null default now(),
    ended_at      timestamptz,
    rows_in       bigint,
    rows_out      bigint,
    rows_rejected bigint      default 0,
    status        text        not null default 'running',
    message       text,
    primary key (run_id, step),
    constraint ck_run_status check (status in ('running','success','failed'))
);
create index idx_runs_started on meta.pipeline_runs (started_at desc);

create table meta.watermarks (
    table_name      text primary key,
    watermark_value timestamp   not null,
    updated_at      timestamptz not null default now()
);

-- raw: untouched copy of what was extracted. No constraints by design,
-- so malformed rows land and stay visible instead of being rejected.
drop table if exists raw.orders      cascade;
drop table if exists raw.order_items cascade;
drop table if exists raw.customers   cascade;

create table raw.orders (
    order_id char(32), customer_id char(32), order_status varchar(20),
    order_purchase_timestamp timestamp, order_approved_at timestamp,
    order_delivered_carrier_date timestamp, order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp,
    _extracted_at timestamptz not null default now(), _run_id bigint
);
create table raw.order_items (
    order_id char(32), order_item_id integer, product_id char(32),
    seller_id char(32), shipping_limit_date timestamp,
    price numeric(10,2), freight_value numeric(10,2),
    _extracted_at timestamptz not null default now(), _run_id bigint
);
create table raw.customers (
    customer_id char(32), customer_unique_id char(32),
    customer_zip_code_prefix varchar(5), customer_city varchar(50),
    customer_state char(2),
    _extracted_at timestamptz not null default now(), _run_id bigint
);
create index idx_raw_orders_run on raw.orders (_run_id);

-- staging: typed, trimmed, deduplicated. Keys enforced here.
drop table if exists staging.orders      cascade;
drop table if exists staging.order_items cascade;
drop table if exists staging.customers   cascade;

create table staging.orders (
    order_id                      char(32) primary key,
    customer_id                   char(32)    not null,
    order_status                  varchar(20) not null,
    order_purchase_timestamp      timestamp   not null,
    order_delivered_customer_date timestamp,
    order_estimated_delivery_date timestamp   not null,
    delivery_days                 integer,
    days_vs_estimate              integer,
    is_late                       boolean,
    _loaded_at                    timestamptz not null default now()
);

create table staging.order_items (
    order_id      char(32) not null,
    order_item_id integer  not null,
    product_id    char(32) not null,
    seller_id     char(32) not null,
    price         numeric(10,2) not null,
    freight_value numeric(10,2) not null,
    _loaded_at    timestamptz   not null default now(),
    primary key (order_id, order_item_id)
);

create table staging.customers (
    customer_id        char(32) primary key,
    customer_unique_id char(32)    not null,
    zip_code_prefix    varchar(5)  not null,
    city               varchar(50) not null,
    state              char(2)     not null,
    _loaded_at         timestamptz not null default now()
);
create index idx_stg_cust_unique on staging.customers (customer_unique_id);
