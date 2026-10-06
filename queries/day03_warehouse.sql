-- =====================================================================
-- SECTION 1 -- DDL
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
    day_of_month smallint    not null,
    day_of_week  smallint    not null,
    day_name     varchar(10) not null,
    week_of_year smallint    not null,
    is_weekend   boolean     not null,
    month_start  date        not null,
    month_end    date        not null
);

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
    on warehouse.dim_customer (customer_unique_id)
    where is_current;

create index idx_dim_customer_lookup
    on warehouse.dim_customer (customer_unique_id, valid_from, valid_to);


create table warehouse.dim_product (
    product_key bigint generated always as identity primary key,
    product_id  char(32) not null unique,
    category_pt varchar(50),
    category_en varchar(50),
    weight_g    integer,
    length_cm   integer,
    height_cm   integer,
    width_cm    integer
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
    state           char(2),
    latitude        double precision,
    longitude       double precision
);


create table warehouse.fact_order_items (
    order_item_key   bigint generated always as identity primary key,
    date_key         integer not null references warehouse.dim_date (date_key),
    customer_key     bigint  not null references warehouse.dim_customer (customer_key),
    product_key      bigint  not null references warehouse.dim_product (product_key),
    seller_key       bigint  not null references warehouse.dim_seller (seller_key),
    geo_key          bigint  not null references warehouse.dim_geography (geo_key),
    order_id         char(32)    not null,
    order_item_id    integer     not null,
    order_status     varchar(20) not null,
    price            numeric(10,2) not null,
    freight_value    numeric(10,2) not null,
    delivery_days    integer,
    days_vs_estimate integer,
    is_late          boolean,

    constraint uq_fact_order_item unique (order_id, order_item_id)
);

create index idx_fact_date     on warehouse.fact_order_items (date_key);
create index idx_fact_customer on warehouse.fact_order_items (customer_key);
create index idx_fact_product  on warehouse.fact_order_items (product_key);
create index idx_fact_seller   on warehouse.fact_order_items (seller_key);
create index idx_fact_geo      on warehouse.fact_order_items (geo_key);

insert into warehouse.dim_date (
    date_key, full_date, year, quarter, month, month_name,
    day_of_month, day_of_week, day_name, week_of_year,
    is_weekend, month_start, month_end
)
select
    (to_char(d, 'YYYYMMDD'))::integer,
    d::date,
    extract(year    from d),
    extract(quarter from d),
    extract(month   from d),
    trim(to_char(d, 'Month')),
    extract(day     from d),
    extract(isodow  from d),
    trim(to_char(d, 'Day')),
    extract(week    from d),
    extract(isodow  from d) in (6, 7),
    date_trunc('month', d)::date,
    (date_trunc('month', d) + interval '1 month - 1 day')::date
from generate_series('2016-01-01'::date, '2019-12-31'::date, interval '1 day') d;


-- =====================================================================
-- SECTION 3 -- Dimensions
-- =====================================================================
with centroid as (
    select
        geolocation_zip_code_prefix as zip,
        avg(geolocation_lat)        as lat,
        avg(geolocation_lng)        as lng
    from source.geolocation
    group by 1
),
modal_place as (
    select distinct on (geolocation_zip_code_prefix)
        geolocation_zip_code_prefix as zip,
        geolocation_city            as city,
        geolocation_state           as state
    from source.geolocation
    group by geolocation_zip_code_prefix, geolocation_city, geolocation_state
    order by geolocation_zip_code_prefix, count(*) desc, geolocation_city
)
insert into warehouse.dim_geography (zip_code_prefix, city, state, latitude, longitude)
select m.zip, m.city, m.state, c.lat, c.lng
from modal_place m
join centroid c on c.zip = m.zip;

insert into warehouse.dim_geography (zip_code_prefix, city, state, latitude, longitude)
select distinct on (z.zip) z.zip, z.city, z.state, null, null
from (
    select customer_zip_code_prefix as zip, customer_city as city, customer_state as state
    from source.customers
    union all
    select seller_zip_code_prefix, seller_city, seller_state
    from source.sellers
) z
where not exists (
    select 1 from warehouse.dim_geography g where g.zip_code_prefix = z.zip
)
order by z.zip;

insert into warehouse.dim_geography
    (geo_key, zip_code_prefix, city, state, latitude, longitude)
overriding system value
values (-1, 'n/a', '(unknown)', 'XX', null, null);


insert into warehouse.dim_product
    (product_id, category_pt, category_en, weight_g, length_cm, height_cm, width_cm)
select
    p.product_id,
    p.product_category_name,
    coalesce(t.product_category_name_english,
             p.product_category_name,
             '(uncategorised)'),
    p.product_weight_g,
    p.product_length_cm,
    p.product_height_cm,
    p.product_width_cm
from source.products p
left join source.product_category_translation t
       on t.product_category_name = p.product_category_name;


insert into warehouse.dim_seller (seller_id, seller_city, seller_state, zip_code_prefix)
select seller_id, seller_city, seller_state, seller_zip_code_prefix
from source.sellers;

insert into warehouse.dim_customer
    (customer_unique_id, customer_city, customer_state, zip_code_prefix,
     valid_from, valid_to, is_current)
select distinct on (c.customer_unique_id)
    c.customer_unique_id,
    c.customer_city,
    c.customer_state,
    c.customer_zip_code_prefix,
    '2016-01-01'::date,
    '9999-12-31'::date,
    true
from source.customers c
left join source.orders o on o.customer_id = c.customer_id
order by c.customer_unique_id, o.order_purchase_timestamp desc nulls last;


-- =====================================================================
-- SECTION 4 -- Fact
-- =====================================================================
insert into warehouse.fact_order_items (
    date_key, customer_key, product_key, seller_key, geo_key,
    order_id, order_item_id, order_status,
    price, freight_value,
    delivery_days, days_vs_estimate, is_late
)
select
    (to_char(o.order_purchase_timestamp, 'YYYYMMDD'))::integer,
    dc.customer_key,
    dp.product_key,
    ds.seller_key,
    coalesce(dg.geo_key, -1),

    oi.order_id,
    oi.order_item_id,
    o.order_status,

    oi.price,
    oi.freight_value,

    o.order_delivered_customer_date::date - o.order_purchase_timestamp::date,
    o.order_delivered_customer_date::date - o.order_estimated_delivery_date::date,
    case
        when o.order_delivered_customer_date is null then null
        else o.order_delivered_customer_date > o.order_estimated_delivery_date
    end
from source.order_items oi
join source.orders    o  on o.order_id    = oi.order_id
join source.customers c  on c.customer_id = o.customer_id
join warehouse.dim_product  dp on dp.product_id = oi.product_id
join warehouse.dim_seller   ds on ds.seller_id  = oi.seller_id
-- SCD2 lookup: the version valid on the order date, not the current one.
join warehouse.dim_customer dc
      on dc.customer_unique_id = c.customer_unique_id
     and o.order_purchase_timestamp::date >= dc.valid_from
     and o.order_purchase_timestamp::date <  dc.valid_to
left join warehouse.dim_geography dg
       on dg.zip_code_prefix = c.customer_zip_code_prefix;


analyze warehouse.dim_date;
analyze warehouse.dim_customer;
analyze warehouse.dim_product;
analyze warehouse.dim_seller;
analyze warehouse.dim_geography;
analyze warehouse.fact_order_items;

-- =====================================================================
-- SECTION 5 -- Validation
-- =====================================================================
select 'dim_date'          as table_name, count(*) from warehouse.dim_date
union all select 'dim_customer',     count(*) from warehouse.dim_customer
union all select 'dim_product',      count(*) from warehouse.dim_product
union all select 'dim_seller',       count(*) from warehouse.dim_seller
union all select 'dim_geography',    count(*) from warehouse.dim_geography
union all select 'fact_order_items', count(*) from warehouse.fact_order_items
order by 1;

select
    (select count(*) from source.order_items)         as source_items,
    (select count(*) from warehouse.fact_order_items) as fact_rows,
    (select count(*) from source.order_items)
  - (select count(*) from warehouse.fact_order_items) as lost_rows;

select count(*) as facts_with_unknown_geo
from warehouse.fact_order_items
where geo_key = -1;


-- =====================================================================
-- SECTION 6 -- SCD Type 2 demonstration
-- =====================================================================
create temporary table scd_demo_target as
select c.customer_unique_id
from source.orders o
join source.customers c on c.customer_id = o.customer_id
group by 1
having count(*) >= 3
order by 1
limit 1;

select 'BEFORE' as stage, customer_key, customer_unique_id,
       customer_city, customer_state, valid_from, valid_to, is_current
from warehouse.dim_customer
where customer_unique_id in (select customer_unique_id from scd_demo_target);

update warehouse.dim_customer d
set valid_to   = date '2018-06-01',
    is_current = false
from scd_demo_target t
where d.customer_unique_id = t.customer_unique_id
  and d.is_current;

insert into warehouse.dim_customer
    (customer_unique_id, customer_city, customer_state, zip_code_prefix,
     valid_from, valid_to, is_current)
select
    t.customer_unique_id,
    'curitiba',
    'PR',
    '80010',
    date '2018-06-01',
    date '9999-12-31',
    true
from scd_demo_target t;

select 'AFTER' as stage, customer_key, customer_unique_id,
       customer_city, customer_state, valid_from, valid_to, is_current
from warehouse.dim_customer
where customer_unique_id in (select customer_unique_id from scd_demo_target)
order by valid_from;

select
    f.order_id,
    d.customer_city as city_on_fact,
    d.valid_from,
    d.valid_to,
    d.is_current
from warehouse.fact_order_items f
join warehouse.dim_customer d on d.customer_key = f.customer_key
where d.customer_unique_id in (select customer_unique_id from scd_demo_target);


-- =====================================================================
-- SECTION 7 -- Day 2 queries rewritten against the star
-- =====================================================================

-- Q1. Monthly revenue and order count.
select
    d.month_start              as month,
    sum(f.price)               as revenue,
    count(distinct f.order_id) as orders
from warehouse.fact_order_items f
join warehouse.dim_date d on d.date_key = f.date_key
where d.year in (2017, 2018)
  and f.order_status <> 'canceled'
group by 1
order by 1;


-- Q3. Top 3 categories per state.
with category_state_revenue as (
    select
        g.state,
        p.category_en              as category,
        sum(f.price)               as revenue,
        count(distinct f.order_id) as orders
    from warehouse.fact_order_items f
    join warehouse.dim_geography g on g.geo_key     = f.geo_key
    join warehouse.dim_product   p on p.product_key = f.product_key
    join warehouse.dim_date      d on d.date_key    = f.date_key
    where d.year in (2017, 2018)
      and f.order_status <> 'canceled'
    group by 1, 2
),
ranked as (
    select *,
           rank() over (partition by state order by revenue desc) as rnk
    from category_state_revenue
)
select state, rnk, category, revenue, orders
from ranked
where rnk <= 3
order by state, rnk;


-- Q5. Delivery performance by state.
select
    g.state,
    count(*)                          as delivered_items,
    round(avg(f.delivery_days), 1)    as avg_delivery_days,
    round(avg(f.days_vs_estimate), 1) as avg_days_vs_promise,
    round(100.0 * count(*) filter (where f.is_late) / count(*), 2) as late_pct
from warehouse.fact_order_items f
join warehouse.dim_geography g on g.geo_key = f.geo_key
where f.order_status = 'delivered'
  and f.delivery_days is not null
group by 1
order by avg_delivery_days desc;
