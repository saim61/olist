-- =====================================================================
-- Star schema. Grain: one row per item within an order.
-- =====================================================================
drop table if exists warehouse.fact_order_items cascade;
drop table if exists warehouse.dim_date         cascade;
drop table if exists warehouse.dim_customer     cascade;
drop table if exists warehouse.dim_product      cascade;
drop table if exists warehouse.dim_seller       cascade;
drop table if exists warehouse.dim_geography    cascade;

create table warehouse.dim_date (
    date_key     integer     primary key,
    full_date    date        not null unique,
    year         smallint    not null,
    quarter      smallint    not null,
    month        smallint    not null,
    month_name   varchar(10) not null,
    day_of_week  smallint    not null,
    is_weekend   boolean     not null,
    month_start  date        not null
);

-- SCD Type 2
create table warehouse.dim_customer (
    customer_key       bigint generated always as identity primary key,
    customer_unique_id char(32)    not null,
    customer_city      varchar(50),
    customer_state     char(2),
    zip_code_prefix    varchar(5),
    valid_from         date        not null,
    valid_to           date        not null default '9999-12-31',
    is_current         boolean     not null default true
);
create unique index uq_dim_customer_current
    on warehouse.dim_customer (customer_unique_id) where is_current;
create index idx_dim_customer_lookup
    on warehouse.dim_customer (customer_unique_id, valid_from, valid_to);

-- SCD Type 1
create table warehouse.dim_product (
    product_key bigint generated always as identity primary key,
    product_id  char(32) not null unique,
    category_pt varchar(50),
    category_en varchar(50),
    weight_g    integer
);

create table warehouse.dim_seller (
    seller_key      bigint generated always as identity primary key,
    seller_id       char(32) not null unique,
    seller_city     varchar(50),
    seller_state    char(2),
    zip_code_prefix varchar(5)
);

create table warehouse.dim_geography (
    geo_key         bigint generated always as identity primary key,
    zip_code_prefix varchar(5) not null unique,
    city            varchar(50),
    state           char(2)
);

create table warehouse.fact_order_items (
    order_item_key   bigint generated always as identity primary key,
    date_key         integer not null references warehouse.dim_date,
    customer_key     bigint  not null references warehouse.dim_customer,
    product_key      bigint  not null references warehouse.dim_product,
    seller_key       bigint  not null references warehouse.dim_seller,
    geo_key          bigint  not null references warehouse.dim_geography,
    -- degenerate dimensions
    order_id         char(32)    not null,
    order_item_id    integer     not null,
    order_status     varchar(20) not null,
    -- additive
    price            numeric(10,2) not null,
    freight_value    numeric(10,2) not null,
    -- non-additive: average, never sum
    delivery_days    integer,
    days_vs_estimate integer,
    is_late          boolean,
    constraint uq_fact_grain unique (order_id, order_item_id)
);

create index idx_fact_date     on warehouse.fact_order_items (date_key);
create index idx_fact_customer on warehouse.fact_order_items (customer_key);
create index idx_fact_product  on warehouse.fact_order_items (product_key);
create index idx_fact_seller   on warehouse.fact_order_items (seller_key);
create index idx_fact_geo      on warehouse.fact_order_items (geo_key);
