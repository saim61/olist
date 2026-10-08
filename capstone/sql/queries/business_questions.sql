-- =====================================================================
-- The five business questions, answered against the warehouse.
-- pipeline/serve.py answers the same questions from the Parquet marts.
-- =====================================================================

-- Q1. Which product categories drive the most revenue?
select p.category_en as category,
       round(sum(f.price), 2) as revenue,
       count(distinct f.order_id) as orders,
       round(100.0 * sum(f.price) / sum(sum(f.price)) over (), 2) as pct_of_total
from warehouse.fact_order_items f
join warehouse.dim_product p on p.product_key = f.product_key
where f.order_status <> 'canceled'
group by 1
order by revenue desc
limit 10;

-- Q2. Which states drive the most revenue?
select g.state,
       round(sum(f.price), 2) as revenue,
       count(distinct f.order_id) as orders,
       round(100.0 * sum(f.price) / sum(sum(f.price)) over (), 2) as pct_of_total
from warehouse.fact_order_items f
join warehouse.dim_geography g on g.geo_key = f.geo_key
where f.order_status <> 'canceled'
group by 1
order by revenue desc
limit 10;

-- Q3. How is revenue trending month over month?
with monthly as (
    select d.month_start as month,
           sum(f.price) as revenue,
           count(distinct f.order_id) as orders
    from warehouse.fact_order_items f
    join warehouse.dim_date d on d.date_key = f.date_key
    where f.order_status <> 'canceled'
    group by 1
),
with_prev as (
    select month, revenue, orders,
           lag(revenue) over (order by month) as prev_revenue
    from monthly
)
select month, round(revenue, 2) as revenue, orders,
       round(100.0 * (revenue - prev_revenue) / nullif(prev_revenue, 0), 2) as growth_pct
from with_prev
order by month;

-- Q4. Where are deliveries late?
select g.state,
       count(*) as delivered_items,
       round(avg(f.delivery_days), 1) as avg_delivery_days,
       round(avg(f.days_vs_estimate), 1) as avg_days_vs_promise,
       round(100.0 * count(*) filter (where f.is_late) / count(*), 2) as late_pct
from warehouse.fact_order_items f
join warehouse.dim_geography g on g.geo_key = f.geo_key
where f.order_status = 'delivered' and f.delivery_days is not null
group by 1
order by late_pct desc
limit 10;

-- Q5. Which sellers are worst for late delivery?
-- The HAVING guard keeps tiny-denominator noise out of the ranking.
select s.seller_id, s.seller_state,
       count(*) as delivered_items,
       round(100.0 * count(*) filter (where f.is_late) / count(*), 2) as late_pct,
       round(avg(f.delivery_days), 1) as avg_delivery_days
from warehouse.fact_order_items f
join warehouse.dim_seller s on s.seller_key = f.seller_key
where f.order_status = 'delivered' and f.delivery_days is not null
group by 1, 2
having count(*) >= 100
order by late_pct desc
limit 10;
