-- =====================================================================
-- Day 2 -- Analytical SQL on the Olist source schema
--
-- Keyword and design explanations: queries/README.md
-- Run all:  psql -U olist -d olist -f queries/day02_analytics.sql
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. Monthly revenue and order count, 2017-2018
-- ---------------------------------------------------------------------
select
    date_trunc('month', o.order_purchase_timestamp)::date as month,
    sum(oi.price)                                         as revenue,
    count(distinct o.order_id)                            as orders
from source.orders o
join source.order_items oi on oi.order_id = o.order_id
where o.order_purchase_timestamp >= '2017-01-01'
  and o.order_purchase_timestamp <  '2019-01-01'
  and o.order_status <> 'canceled'
group by 1
order by 1;


-- ---------------------------------------------------------------------
-- Q2. Month-over-month revenue growth %
-- ---------------------------------------------------------------------
with monthly as (
    select
        date_trunc('month', o.order_purchase_timestamp)::date as month,
        sum(oi.price)                                         as revenue,
        count(distinct o.order_id)                            as orders
    from source.orders o
    join source.order_items oi on oi.order_id = o.order_id
    where o.order_purchase_timestamp >= '2017-01-01'
      and o.order_purchase_timestamp <  '2019-01-01'
      and o.order_status <> 'canceled'
    group by 1
),
with_prev as (
    select
        month,
        revenue,
        orders,
        lag(revenue) over (order by month) as prev_revenue
    from monthly
)
select
    month,
    revenue,
    prev_revenue,
    round(100.0 * (revenue - prev_revenue) / nullif(prev_revenue, 0), 2) as growth_pct
from with_prev
order by month;


-- ---------------------------------------------------------------------
-- Q3. Top 3 product categories by revenue in each state
-- State = customer_state (where demand is), not seller_state.
-- ---------------------------------------------------------------------
with category_state_revenue as (
    select
        c.customer_state as state,
        coalesce(t.product_category_name_english,
                 p.product_category_name,
                 '(uncategorised)') as category,
        sum(oi.price)              as revenue,
        count(distinct o.order_id) as orders
    from source.orders o
    join source.order_items oi on oi.order_id   = o.order_id
    join source.customers   c  on c.customer_id = o.customer_id
    join source.products    p  on p.product_id  = oi.product_id
    left join source.product_category_translation t
           on t.product_category_name = p.product_category_name
    where o.order_purchase_timestamp >= '2017-01-01'
      and o.order_purchase_timestamp <  '2019-01-01'
      and o.order_status <> 'canceled'
    group by 1, 2
),
ranked as (
    select
        state,
        category,
        revenue,
        orders,
        rank() over (partition by state order by revenue desc) as rnk
    from category_state_revenue
)
select state, rnk, category, revenue, orders
from ranked
where rnk <= 3
order by state, rnk;


-- ---------------------------------------------------------------------
-- Q4. Running total of revenue per seller, by month
-- Limited to the 5 largest sellers so the output stays readable.
-- ---------------------------------------------------------------------
with seller_monthly as (
    select
        oi.seller_id,
        date_trunc('month', o.order_purchase_timestamp)::date as month,
        sum(oi.price) as revenue
    from source.orders o
    join source.order_items oi on oi.order_id = o.order_id
    where o.order_status <> 'canceled'
    group by 1, 2
),
top_sellers as (
    select seller_id
    from seller_monthly
    group by 1
    order by sum(revenue) desc
    limit 5
)
select
    sm.seller_id,
    sm.month,
    sm.revenue,
    sum(sm.revenue) over (
        partition by sm.seller_id
        order by sm.month
        rows between unbounded preceding and current row
    ) as running_total
from seller_monthly sm
join top_sellers ts on ts.seller_id = sm.seller_id
order by sm.seller_id, sm.month;


-- ---------------------------------------------------------------------
-- Q5. Actual vs estimated delivery time, by state
-- Only delivered orders can be measured.
-- ---------------------------------------------------------------------
select
    c.customer_state as state,
    count(*)         as delivered_orders,
    round(avg(o.order_delivered_customer_date::date
              - o.order_purchase_timestamp::date), 1) as avg_actual_days,
    round(avg(o.order_estimated_delivery_date::date
              - o.order_purchase_timestamp::date), 1) as avg_promised_days,
    round(avg(o.order_delivered_customer_date::date
              - o.order_estimated_delivery_date::date), 1) as avg_days_vs_promise,
    round(100.0 * count(*) filter (
              where o.order_delivered_customer_date
                  > o.order_estimated_delivery_date
          ) / count(*), 2) as late_pct
from source.orders o
join source.customers c on c.customer_id = o.customer_id
where o.order_status = 'delivered'
  and o.order_delivered_customer_date is not null
group by 1
order by avg_actual_days desc;


-- ---------------------------------------------------------------------
-- Q6. Repeat customer rate
-- Grouped by customer_unique_id: customer_id is per-order, so grouping
-- by it would report a 0% repeat rate.
-- ---------------------------------------------------------------------
with customer_orders as (
    select
        c.customer_unique_id,
        count(distinct o.order_id) as orders
    from source.orders o
    join source.customers c on c.customer_id = o.customer_id
    where o.order_status <> 'canceled'
    group by 1
)
select
    count(*)                                  as total_customers,
    count(*) filter (where orders > 1)        as repeat_customers,
    round(100.0 * count(*) filter (where orders > 1) / count(*), 2) as repeat_rate_pct,
    max(orders)                               as most_orders_by_one_customer,
    round(avg(orders), 3)                     as avg_orders_per_customer
from customer_orders;

-- Q6b. The same thing done wrong, for contrast: grouping by customer_id.
with wrong as (
    select o.customer_id, count(distinct o.order_id) as orders
    from source.orders o
    where o.order_status <> 'canceled'
    group by 1
)
select
    count(*)                           as total_customers,
    count(*) filter (where orders > 1) as repeat_customers,
    round(100.0 * count(*) filter (where orders > 1) / count(*), 2) as repeat_rate_pct
from wrong;


-- ---------------------------------------------------------------------
-- Q7. Cohort retention: customers by first-order month, with how many
-- ordered again in each of the next 3 months.
-- ---------------------------------------------------------------------
with customer_first_order as (
    select
        c.customer_unique_id,
        date_trunc('month', min(o.order_purchase_timestamp))::date as cohort_month
    from source.orders o
    join source.customers c on c.customer_id = o.customer_id
    where o.order_status <> 'canceled'
    group by 1
),
orders_with_offset as (
    select
        f.customer_unique_id,
        f.cohort_month,
        (extract(year  from date_trunc('month', o.order_purchase_timestamp))
         - extract(year  from f.cohort_month)) * 12
      + (extract(month from date_trunc('month', o.order_purchase_timestamp))
         - extract(month from f.cohort_month)) as month_offset
    from source.orders o
    join source.customers c        on c.customer_id        = o.customer_id
    join customer_first_order f    on f.customer_unique_id = c.customer_unique_id
    where o.order_status <> 'canceled'
)
select
    cohort_month,
    count(distinct customer_unique_id) filter (where month_offset = 0) as cohort_size,
    count(distinct customer_unique_id) filter (where month_offset = 1) as month_1,
    count(distinct customer_unique_id) filter (where month_offset = 2) as month_2,
    count(distinct customer_unique_id) filter (where month_offset = 3) as month_3,
    round(100.0 * count(distinct customer_unique_id) filter (where month_offset = 1)
          / nullif(count(distinct customer_unique_id) filter (where month_offset = 0), 0), 2) as m1_pct,
    round(100.0 * count(distinct customer_unique_id) filter (where month_offset = 2)
          / nullif(count(distinct customer_unique_id) filter (where month_offset = 0), 0), 2) as m2_pct,
    round(100.0 * count(distinct customer_unique_id) filter (where month_offset = 3)
          / nullif(count(distinct customer_unique_id) filter (where month_offset = 0), 0), 2) as m3_pct
from orders_with_offset
group by 1
order by 1;


-- ---------------------------------------------------------------------
-- Q8. Anti-join: orders with no payment row
-- ---------------------------------------------------------------------
select
    o.order_id,
    o.order_status,
    o.order_purchase_timestamp,
    (select count(*) from source.order_items i where i.order_id = o.order_id) as items
from source.orders o
where not exists (
    select 1
    from source.order_payments p
    where p.order_id = o.order_id
)
order by o.order_purchase_timestamp;

-- Q8b. The same anti-join written as LEFT JOIN ... IS NULL.
select count(*) as orders_without_payment
from source.orders o
left join source.order_payments p on p.order_id = o.order_id
where p.order_id is null;


-- ---------------------------------------------------------------------
-- Q9. Deduplication, demonstrated on a scratch copy.
-- ---------------------------------------------------------------------
drop table if exists staging.sellers_dedup_demo;

create table staging.sellers_dedup_demo as
select * from source.sellers;

-- Inject duplicates: re-insert 100 rows that already exist.
insert into staging.sellers_dedup_demo
select * from source.sellers order by seller_id limit 100;

select
    count(*)                    as total_rows,
    count(distinct seller_id)   as distinct_sellers,
    count(*) - count(distinct seller_id) as duplicate_rows
from staging.sellers_dedup_demo;

-- Delete all but the first physical row per key.
-- ctid is Postgres' physical row identifier; it is the only thing that
-- distinguishes two otherwise identical rows.
delete from staging.sellers_dedup_demo a
using (
    select
        ctid,
        row_number() over (partition by seller_id order by ctid) as rn
    from staging.sellers_dedup_demo
) dup
where a.ctid = dup.ctid
  and dup.rn > 1;

select
    count(*)                  as total_rows,
    count(distinct seller_id) as distinct_sellers,
    count(*) - count(distinct seller_id) as duplicate_rows
from staging.sellers_dedup_demo;


-- ---------------------------------------------------------------------
-- Q10. EXPLAIN ANALYZE, then add an index and re-measure.
-- customer_city has no index, so this filter forces a sequential scan.
--
-- The index is dropped first so that re-running this file always measures
-- a genuine before/after rather than two post-index runs.
-- ---------------------------------------------------------------------
drop index if exists source.idx_customers_city;
analyze source.customers;

explain (analyze, buffers)
select count(*), sum(oi.price)
from source.orders o
join source.customers   c  on c.customer_id = o.customer_id
join source.order_items oi on oi.order_id   = o.order_id
where c.customer_city = 'sao paulo'
  and o.order_status <> 'canceled';

create index if not exists idx_customers_city on source.customers (customer_city);
analyze source.customers;

explain (analyze, buffers)
select count(*), sum(oi.price)
from source.orders o
join source.customers   c  on c.customer_id = o.customer_id
join source.order_items oi on oi.order_id   = o.order_id
where c.customer_city = 'sao paulo'
  and o.order_status <> 'canceled';
