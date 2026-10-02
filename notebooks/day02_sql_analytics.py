# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Day 2 -- Analytical SQL
#
# Each lab question in its own cell, run through SQLAlchemy into a pandas
# DataFrame.
#
# The SQL here is the same text as `queries/day02_analytics.sql`. That file
# is the artifact you would hand to another engineer; this notebook is for
# running and eyeballing results.
#
# Keyword explanations: `queries/README.md`

# %%
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
load_dotenv(ROOT / ".env")

# SQLAlchemy URL: dialect+driver://user:password@host:port/database
URL = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
    f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5433')}"
    f"/{os.getenv('POSTGRES_DB')}"
)

engine = create_engine(URL)

# Fail now with a clear message rather than inside the first query.
with engine.connect() as conn:
    print(conn.execute(text("select version()")).scalar())


# %%
def q(sql: str) -> pd.DataFrame:
    """Run SQL, return a DataFrame."""
    return pd.read_sql(text(sql), engine)


pd.set_option("display.max_rows", 120)
pd.set_option("display.width", 200)

# %% [markdown]
# ## Q1. Monthly revenue and order count, 2017-2018
#
# `count(distinct o.order_id)` is load-bearing: the join to `order_items`
# repeats each order once per item, so a plain `count(*)` overstates orders
# by about 14%.

# %%
q1 = q("""
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
order by 1
""")
q1

# %%
print(f"months  : {len(q1)}")
print(f"revenue : {q1.revenue.sum():,.2f}")
print(f"orders  : {q1.orders.sum():,}")

# %% [markdown]
# ## Q2. Month-over-month revenue growth %
#
# `lag(revenue)` is computed in its own CTE so the final select can name it
# instead of repeating the window expression three times.
#
# Watch September 2018: the dataset stops, so growth reads -99.98%. That is
# a collection artifact, not a business event.

# %%
q2 = q("""
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
order by month
""")
q2

# %% [markdown]
# ## Q3. Top 3 categories by revenue in each state
#
# `rank() over (partition by state order by revenue desc)` restarts the
# numbering for every state.
#
# The ranking is computed in one CTE and filtered in the next, because
# `where` runs before window functions exist.

# %%
q3 = q("""
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
order by state, rnk
""")
q3

# %%
print(f"rows   : {len(q3)}")
print(f"states : {q3.state.nunique()}")
print("\nmost common category at rank 1:")
print(q3[q3.rnk == 1].category.value_counts().head())

# %% [markdown]
# ## Q4. Running total of revenue per seller
#
# `sum()` becomes a window function by adding `over`. The frame clause
# `rows between unbounded preceding and current row` makes it cumulative.
#
# Limited to the top 5 sellers so the output stays readable.

# %%
q4 = q("""
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
order by sm.seller_id, sm.month
""")
q4.head(15)

# %% [markdown]
# ## Q5. Actual vs estimated delivery time, by state
#
# `date - date` gives integer days. `filter (where ...)` applies a condition
# to a single aggregate.

# %%
q5 = q("""
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
order by avg_actual_days desc
""")
q5

# %% [markdown]
# Every state beats its promise on average, yet late rates reach 24%.
# The mean hides the tail: most orders arrive very early, a minority very
# late. Always pair an average with a rate or a percentile.

# %% [markdown]
# ## Q6. Repeat customer rate
#
# Must group by `customer_unique_id`. `customer_id` is generated per order,
# so grouping by it reports a 0% repeat rate with no error.

# %%
q6 = q("""
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
from customer_orders
""")
q6

# %%
# The same question answered wrongly, for contrast.
q6_wrong = q("""
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
from wrong
""")
q6_wrong

# %% [markdown]
# ## Q7. Cohort retention
#
# Assign each customer to the month of their first order, then count how
# many ordered again 1, 2 and 3 months later.
#
# `filter (where month_offset = n)` is the SQL pivot idiom: one aggregate
# per output column.

# %%
q7 = q("""
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
    join source.customers c     on c.customer_id        = o.customer_id
    join customer_first_order f on f.customer_unique_id = c.customer_unique_id
    where o.order_status <> 'canceled'
)
select
    cohort_month,
    count(distinct customer_unique_id) filter (where month_offset = 0) as cohort_size,
    count(distinct customer_unique_id) filter (where month_offset = 1) as month_1,
    count(distinct customer_unique_id) filter (where month_offset = 2) as month_2,
    count(distinct customer_unique_id) filter (where month_offset = 3) as month_3,
    round(100.0 * count(distinct customer_unique_id) filter (where month_offset = 1)
          / nullif(count(distinct customer_unique_id) filter (where month_offset = 0), 0), 2) as m1_pct
from orders_with_offset
group by 1
order by 1
""")
q7

# %% [markdown]
# Month-1 retention is under 1%. Normal for a marketplace selling furniture
# and watches -- people do not rebuy a bed frame monthly. Note 2016-11 is
# missing entirely: a gap in the dataset, not a month without sales.

# %% [markdown]
# ## Q8. Orders with no payment row (anti-join)
#
# `not exists` is the safe form. `not in` returns zero rows if the subquery
# contains a single NULL.

# %%
q8 = q("""
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
order by o.order_purchase_timestamp
""")
q8

# %% [markdown]
# ## Q9. Deduplication
#
# Runs on a scratch copy in `staging`, never on `source`. `ctid` is the
# physical row id -- the only thing distinguishing two identical rows.

# %%
from sqlalchemy import text as _text  # noqa: E402

with engine.begin() as conn:
    conn.execute(_text("drop table if exists staging.sellers_dedup_demo"))
    conn.execute(_text(
        "create table staging.sellers_dedup_demo as select * from source.sellers"))
    conn.execute(_text(
        "insert into staging.sellers_dedup_demo "
        "select * from source.sellers order by seller_id limit 100"))

before = q("""
select count(*) as total_rows,
       count(distinct seller_id) as distinct_sellers,
       count(*) - count(distinct seller_id) as duplicate_rows
from staging.sellers_dedup_demo
""")
print("before dedup:")
before

# %%
with engine.begin() as conn:
    conn.execute(_text("""
        delete from staging.sellers_dedup_demo a
        using (
            select ctid,
                   row_number() over (partition by seller_id order by ctid) as rn
            from staging.sellers_dedup_demo
        ) dup
        where a.ctid = dup.ctid
          and dup.rn > 1
    """))

after = q("""
select count(*) as total_rows,
       count(distinct seller_id) as distinct_sellers,
       count(*) - count(distinct seller_id) as duplicate_rows
from staging.sellers_dedup_demo
""")
print("after dedup:")
after

# %% [markdown]
# ## Q10. EXPLAIN ANALYZE and an index
#
# `customer_city` has no index, so the filter forces a sequential scan.
# Read the plan inside out -- the most indented node runs first.

# %%
PLAN_SQL = """
select count(*), sum(oi.price)
from source.orders o
join source.customers   c  on c.customer_id = o.customer_id
join source.order_items oi on oi.order_id   = o.order_id
where c.customer_city = 'sao paulo'
  and o.order_status <> 'canceled'
"""

# Drop first, so re-running the notebook measures a real before/after
# rather than two post-index runs.
with engine.begin() as conn:
    conn.execute(_text("drop index if exists source.idx_customers_city"))
    conn.execute(_text("analyze source.customers"))

with engine.connect() as conn:
    rows = conn.execute(_text(f"explain (analyze, buffers) {PLAN_SQL}")).fetchall()
print("BEFORE INDEX")
print("\n".join(r[0] for r in rows))

# %%
with engine.begin() as conn:
    conn.execute(_text(
        "create index if not exists idx_customers_city "
        "on source.customers (customer_city)"))
    conn.execute(_text("analyze source.customers"))

with engine.connect() as conn:
    rows = conn.execute(_text(f"explain (analyze, buffers) {PLAN_SQL}")).fetchall()
print("AFTER INDEX")
print("\n".join(r[0] for r in rows))

# %% [markdown]
# `Parallel Seq Scan` on customers (41,950 rows discarded) becomes a
# `Bitmap Index Scan`, and runtime drops from ~46 ms to ~36 ms.
#
# Only 21% faster, because `orders` and `order_items` are still scanned
# sequentially and dominate the cost. Read the plan before adding an index,
# or you optimise something that was not the bottleneck.

# %%
engine.dispose()
