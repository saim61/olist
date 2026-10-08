# Day 2 — Analytical SQL, explained

Companion to [`day02_analytics.sql`](day02_analytics.sql) and
`notebooks/day02_sql_analytics.ipynb`.

---

## The one thing to understand first: grain

The three transaction tables count different things:

| Table | One row means | Rows |
|---|---|---|
| `orders` | one order | 99,441 |
| `order_items` | one item within an order | 112,650 |
| `order_payments` | one payment method used | 103,886 |

Join them naively and an order with 3 items paid by 2 methods becomes
**6 rows**. Every `SUM` and `COUNT` inflates, nothing errors, and the number
looks plausible. This is called **fan-out** and it is the most common
analytics bug there is.

Rules that follow from it:

- `count(*)` after joining to a child table counts *child rows*, not parents.
  Use `count(distinct parent_id)`.
- Never join `order_items` and `order_payments` in the same query unless you
  aggregate one of them to the order grain first.
- Decide what "revenue" means once, and stay with it. These queries use
  `sum(order_items.price)` — merchandise sold, excluding freight. Alternatives
  are `sum(price + freight_value)` (total charged) and
  `sum(payments.payment_value)` (cash collected). All three are defensible
  and all three give different numbers.

---

## Logical query order

SQL does not execute top to bottom. It runs:

```
FROM → JOIN → WHERE → GROUP BY → HAVING → window functions → SELECT → ORDER BY → LIMIT
```

Two consequences that explain most "why doesn't this work" moments:

1. **`WHERE` cannot see window functions**, because they are computed later.
   Filtering on a `rank()` needs a CTE or subquery.
2. **`WHERE` cannot see `SELECT` aliases** for the same reason. (`GROUP BY`,
   `HAVING` and `ORDER BY` *can* — an inconsistency in the standard.)

`WHERE` filters rows *before* grouping; `HAVING` filters groups *after*.

---

## Q1. Monthly revenue and order count

```sql
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
```

**Returns** 21 rows, R$13,449,674.68 total, 97,906 orders.

### Keywords

| Keyword | Purpose |
|---|---|
| `date_trunc('month', ts)` | Snaps a timestamp down to the first instant of its month, so all of March collapses to `2017-03-01`. The unit can be `year`, `quarter`, `week`, `day`, `hour`… |
| `::date` | Postgres cast shorthand. Drops the time portion for readability. Equivalent to `CAST(x AS date)`. |
| `sum(...)` | Aggregate. Collapses many rows into one value per group. |
| `count(distinct x)` | Counts **unique** values of `x`. Without `distinct` you count rows, which after a join is the wrong number. |
| `join` | Inner join. Keeps only rows matching on both sides — so it is also a **filter**. Orders with no items silently disappear. |
| `group by 1` | Group by the 1st expression in the `select` list. Saves retyping `date_trunc(...)`. |
| `order by 1` | Same positional shorthand, for sorting. |

### Why `>= ... AND < ...` instead of `BETWEEN`

`BETWEEN '2017-01-01' AND '2018-12-31'` is inclusive of both ends, but
`2018-12-31` as a timestamp means `2018-12-31 00:00:00`. Everything that
happened *during* 31 December is silently dropped.

A **half-open interval** — `>= start AND < next_start` — has no such edge.
Use it for every timestamp range.

### Why `<> 'canceled'`

The status column has eight values forming a lifecycle:

```
created → approved → invoiced → processing → shipped → delivered
                                               ↘ canceled
```

Only `canceled` represents revenue that never happened. Excluding any other
status would be arbitrary — a shipped order is *further along* than an
invoiced one, so a filter that keeps `invoiced` but drops `shipped` is
inconsistent.

> **Watch the spelling.** The data uses `canceled` (one L). Writing
> `<> 'cancelled'` matches zero rows, so the filter does nothing and
> R$92,183 of cancelled revenue stays in. No error is raised. Always check
> categorical values with `select col, count(*) ... group by 1` before
> filtering on them — or read the `ck_orders_status` CHECK constraint in the
> DDL, which lists the valid domain.

---

## Q2. Month-over-month growth %

```sql
with monthly as ( ... Q1 ... ),
with_prev as (
    select month, revenue, orders,
           lag(revenue) over (order by month) as prev_revenue
    from monthly
)
select month, revenue, prev_revenue,
       round(100.0 * (revenue - prev_revenue) / nullif(prev_revenue, 0), 2) as growth_pct
from with_prev
order by month;
```

### Keywords

| Keyword | Purpose |
|---|---|
| `with x as (...)` | **CTE** (Common Table Expression). A named subquery scoped to one statement. Chain several with commas; each can reference the ones before it. |
| `lag(col)` | Window function. Returns `col` from the **previous row**. `lag(col, 2)` goes back two rows. `lead()` is the mirror image. |
| `over (order by ...)` | Defines the window. `order by` inside `over` decides what "previous" means — independent of the query's final `order by`. |
| `nullif(a, b)` | Returns `NULL` if `a = b`, else `a`. Here it converts a zero denominator into `NULL`, so you get an empty result instead of a division-by-zero error. |
| `round(x, 2)` | Rounds to 2 decimal places. |

### Why two CTEs

You cannot reference a `select` alias from within the same `select` list —
all items are conceptually evaluated at once. So this fails:

```sql
select revenue,
       lag(revenue) over (order by month) as prev,
       (revenue - prev) / prev            -- ERROR: "prev" does not exist
```

Two options: repeat the full `lag(...)` expression every time you need it
(works, but unreadable at three repetitions), or **name it in a prior CTE**
and treat it as an ordinary column afterwards. The second is why
`with_prev` exists.

> General rule: when an expression appears more than once, give it a name in
> a prior CTE. Postgres 12+ inlines CTEs, so this costs nothing at runtime.

### Why the first row is NULL

There is no row before January 2017, so `lag` returns `NULL` and the
arithmetic propagates it. That is correct — do not `coalesce` it to zero,
which would assert flat growth where you have no data at all.

### `lag` compares rows, not months

If a month were missing from the data, the next month would compare itself
to two months prior and report the gap as one month's growth — silently.
These months happen to be contiguous, so the query is safe by luck.

The robust fix is a **date spine**: generate every month with
`generate_series`, `LEFT JOIN` the data onto it, and get explicit `NULL`s
for empty periods. That is what `dim_date` is for in Day 3.

### Reading the output

- **Nov 2017 +52%, Dec −26%** — Black Friday. December's "decline" is
  measured against an inflated November and is still the second-best month
  of the year. Month-over-month always flatters the month after a trough and
  punishes the month after a peak; pair it with year-over-year.
- **Sep 2018 −99.98%** — the dataset stops. Arithmetically perfect,
  completely meaningless. Incomplete periods should be excluded or flagged.

---

## Q3. Top 3 categories per state

```sql
with category_state_revenue as (
    select c.customer_state as state,
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
    where ...
    group by 1, 2
),
ranked as (
    select state, category, revenue, orders,
           rank() over (partition by state order by revenue desc) as rnk
    from category_state_revenue
)
select state, rnk, category, revenue, orders
from ranked
where rnk <= 3
order by state, rnk;
```

**Returns** 81 rows across 27 states.

### Keywords

| Keyword | Purpose |
|---|---|
| `partition by x` | Splits rows into groups and **restarts the window** for each. Like `group by` without collapsing rows. |
| `rank()` | 1, 2, 2, 4 — ties share a rank, then the next rank *skips*. |
| `dense_rank()` | 1, 2, 2, 3 — ties share, no skipping. |
| `row_number()` | 1, 2, 3, 4 — always distinct; ties broken arbitrarily, so results can vary between runs. |
| `left join` | Keeps every row from the left table even with no match on the right; unmatched right columns come back `NULL`. |
| `coalesce(a, b, c)` | Returns the first non-`NULL` argument. |
| `desc` | Descending sort. Highest revenue gets rank 1. |

### Building a join chain

Each join reaches a new table through a key **already present** in the query:

| Step | Table | Joined on | Key came from |
|---|---|---|---|
| start | `orders` | — | has `order_id`, `customer_id` |
| +1 | `order_items` | `order_id` | `orders` |
| +2 | `customers` | `customer_id` | `orders` |
| +3 | `products` | `product_id` | **`order_items`** |

`products` must come after `order_items`, because `product_id` does not
exist in the query until `order_items` is joined. That ordering constraint
is how you work out any join chain.

### Why `left join` for the translation table

Two categories (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`)
have no translation row, and 610 products have no category at all. An inner
join would drop all of those products and quietly understate revenue.

The `coalesce` then handles both cases: untranslated categories fall back to
their Portuguese name, and missing ones become `'(uncategorised)'` — visible
rather than silently absent.

### Why ranking needs its own CTE

```sql
select ..., rank() over (...) as rnk
from ...
where rnk <= 3          -- ERROR: column "rnk" does not exist
```

`WHERE` runs before window functions are computed. Rank in one CTE, filter
in the next.

### Caveat in the result

`AP` reaches rank 3 with R$1,437 from a **single order**. Rankings over tiny
denominators are noise. A production report would add
`having count(distinct o.order_id) >= 10`, or show the order count beside
the revenue — which is why `orders` is kept in the output.

---

## Q4. Running total of revenue per seller

```sql
sum(sm.revenue) over (
    partition by sm.seller_id
    order by sm.month
    rows between unbounded preceding and current row
) as running_total
```

### Keywords

| Keyword | Purpose |
|---|---|
| `sum(x) over (...)` | The *aggregate* `sum` used as a window function. Keeps every row and adds a cumulative column, instead of collapsing rows. |
| `rows between A and B` | The **frame clause**. Defines which rows within the partition feed the aggregate for the current row. |
| `unbounded preceding` | Start of the partition. |
| `current row` | Stop at the row being computed. |

An aggregate becomes a window function purely by adding `OVER`. `sum(x)`
gives one number; `sum(x) over (order by d)` gives a running total.

### Why the frame clause matters

Omit it and the default is
`RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, which differs from
`ROWS` in one important way: **`RANGE` includes all rows that are tied on
the `ORDER BY` value.** If two rows share a month, `RANGE` gives both the
same running total — the end-of-month figure — whereas `ROWS` adds them one
at a time.

Here the CTE guarantees one row per seller-month, so the two behave
identically. Writing `ROWS` explicitly says what you meant and survives the
day someone changes the grain.

### Sample output

```
seller_id     month       revenue   running_total
4869f7a5...   2017-03-01   1385.00        1385.00
4869f7a5...   2017-05-01    374.00        1759.00   <- April is missing
4869f7a5...   2017-06-01   5064.85        6823.85
```

Note the gap: this seller had no April sales, so no April row exists. The
running total stays correct — but, as in Q2, the window walks *rows*, not
*months*. Any calculation needing "the last 3 months" rather than "the last
3 rows" requires a date spine.

---

## Q5. Actual vs estimated delivery time, by state

```sql
round(avg(o.order_delivered_customer_date::date
          - o.order_purchase_timestamp::date), 1) as avg_actual_days,
round(100.0 * count(*) filter (
          where o.order_delivered_customer_date
              > o.order_estimated_delivery_date
      ) / count(*), 2) as late_pct
```

### Keywords

| Keyword | Purpose |
|---|---|
| `date - date` | Subtracting two `date` values yields an **integer** number of days. Subtracting two `timestamp` values yields an `interval`, which cannot go straight into `avg` — hence the `::date` casts. |
| `filter (where ...)` | Applies a condition to **one aggregate only**. Standard SQL, and clearer than `count(case when ... then 1 end)`. |
| `is not null` | Required here: undelivered orders have a `NULL` delivery date. |

### Why `::date` and not `EXTRACT`

`timestamp - timestamp` produces an `interval` like `8 days 04:21:33`.
Averaging intervals works but formats awkwardly. Casting both sides to
`date` first gives plain integers, so `avg` returns a clean number of days.

The trade-off: casting truncates the time, so an order placed at 23:00 and
delivered at 01:00 two days later counts as 2 days, not 1.08. For
whole-day delivery SLAs that is the behaviour you want.

### Reading the output

```
state  delivered  actual_days  promised_days  vs_promise  late_pct
RR            41         29.3           46.6      -17.3      12.20
AP            67         27.2           46.9      -19.7       4.48
AL           397         24.5           33.2       -8.7      23.93
```

Every state beats its promise on average — `vs_promise` is negative
everywhere, by 9 to 20 days. The estimates are heavily padded.

Yet `late_pct` ranges from 4% to 24%. Those two facts coexist because the
average hides the tail: most deliveries arrive far early, and a minority
arrive very late. **This is why you report a percentile or a late rate
alongside a mean** — an average alone would suggest delivery is a solved
problem in AL, where one order in four misses its date.

---

## Q6. Repeat customer rate

```sql
with customer_orders as (
    select c.customer_unique_id, count(distinct o.order_id) as orders
    from source.orders o
    join source.customers c on c.customer_id = o.customer_id
    where o.order_status <> 'canceled'
    group by 1
)
select count(*) filter (where orders > 1) ...
```

### The trap this question exists to teach

| Grouped by | Customers | Repeats | Rate |
|---|---|---|---|
| `customer_id` | 98,816 | **0** | **0.00%** |
| `customer_unique_id` | 95,560 | 2,924 | **3.06%** |

`customers.customer_id` is generated **per order**. Buy twice, get two
`customer_id` values. Grouping by it therefore reports that no customer has
ever ordered twice — a result that is arithmetically correct, obviously
false, and produces no error.

`customer_unique_id` is the actual person. The real repeat rate is 3.06%,
and one customer placed 17 orders.

The general lesson: before grouping by an id, confirm what one row of that
id *means*. A column named `customer_id` in a table named `customers` can
still not be the customer.

---

## Q7. Cohort retention

```sql
with customer_first_order as (
    select c.customer_unique_id,
           date_trunc('month', min(o.order_purchase_timestamp))::date as cohort_month
    ...
),
orders_with_offset as (
    select ...,
           (extract(year  from order_month) - extract(year  from cohort_month)) * 12
         + (extract(month from order_month) - extract(month from cohort_month)) as month_offset
    ...
)
select cohort_month,
       count(distinct customer_unique_id) filter (where month_offset = 0) as cohort_size,
       count(distinct customer_unique_id) filter (where month_offset = 1) as month_1,
       ...
```

### Keywords

| Keyword | Purpose |
|---|---|
| `min(ts)` + `group by` | Finds each customer's first order — the standard way to assign a cohort. |
| `extract(year from d)` | Pulls one field out of a date. |
| `filter (where offset = n)` | Pivots rows into columns. Each `filter` counts a different month offset within the same `group by`. |

### Why months are subtracted this way

```sql
(year_b - year_a) * 12 + (month_b - month_a)
```

Converting both dates to an absolute month number and subtracting is exact.
The obvious alternative — `age()` or dividing a day count by 30 — drifts,
because months have different lengths.

### The `filter` pivot

SQL has no native pivot. The idiom is one aggregate per output column, each
with its own `FILTER`:

```sql
count(*) filter (where month_offset = 1) as month_1,
count(*) filter (where month_offset = 2) as month_2,
```

All of them run over the same grouped rows, each counting a different
subset. For a variable number of columns you would generate the SQL, or
pivot in pandas after the fact.

### Reading the output

```
cohort_month  cohort_size  m1  m2  m3  m1_pct
2017-08-01           4162  28  14  11    0.67
2017-09-01           4112  28  22  12    0.68
```

Retention is **under 1%** in month 1. For a subscription business that would
be catastrophic; for a marketplace selling furniture and watches it is
normal — people simply do not buy a bed frame monthly. Cohort retention is
only meaningful against a baseline for the same category.

Also note **2016-11 is absent entirely** and 2016-12 has a single customer.
That is a gap in the dataset, not a month with no sales — another argument
for a date spine.

---

## Q8. Anti-join: orders with no payment

```sql
select o.order_id, ...
from source.orders o
where not exists (
    select 1 from source.order_payments p where p.order_id = o.order_id
);
```

**Returns exactly 1 row** — a delivered 2016 order with 3 items and no
payment record.

### Three ways to write an anti-join

| Form | Safe? | Notes |
|---|---|---|
| `NOT EXISTS (select 1 ...)` | ✅ | Preferred. `NULL`-safe, and the planner optimises it well. |
| `LEFT JOIN ... WHERE right.key IS NULL` | ✅ | Equivalent. Reads less obviously. |
| `NOT IN (select key ...)` | ⚠️ | **Returns zero rows if the subquery contains a single `NULL`.** |

The `NOT IN` trap: `x NOT IN (1, 2, NULL)` evaluates to `NULL`, never
`TRUE`, because SQL cannot prove `x` differs from an unknown value. One
`NULL` anywhere in the subquery silently empties your entire result.

Use `NOT EXISTS`. It has no such failure mode.

### Why `select 1`

The subquery's select list is never evaluated — `EXISTS` only asks whether a
row exists. `select 1` is convention, signalling "the columns are
irrelevant here."

---

## Q9. Deduplication

```sql
delete from staging.sellers_dedup_demo a
using (
    select ctid,
           row_number() over (partition by seller_id order by ctid) as rn
    from staging.sellers_dedup_demo
) dup
where a.ctid = dup.ctid
  and dup.rn > 1;
```

Runs on a scratch copy in `staging`, never on `source`. 3,095 rows + 100
injected duplicates → 3,195, then back to 3,095.

### Keywords

| Keyword | Purpose |
|---|---|
| `ctid` | Postgres' **physical row identifier** (block, offset). Every row has one, including rows identical in every column. |
| `row_number() over (partition by key ...)` | Numbers rows 1, 2, 3 within each key. Everything above 1 is a duplicate. |
| `delete ... using (...)` | Postgres syntax for deleting based on a join against another relation. |
| `create table as select` | Materialises a query into a new table. Copies **no** constraints or indexes. |

### Why `ctid` and not a column

The duplicates are byte-identical, so no column distinguishes them. `ctid`
is the only thing that does.

Two caveats: `ctid` is **not stable** — `VACUUM FULL`, `UPDATE` and
clustering all move rows — so use it within a single statement and never
store it. And `row_number()` needs *some* `ORDER BY`; ordering by `ctid`
means "keep whichever copy is physically first," which is arbitrary but
deterministic within the statement.

With a real timestamp you would order meaningfully instead:

```sql
row_number() over (partition by id order by updated_at desc)
```

— the standard "keep the latest version of each key" pattern, which is
exactly what Day 4's staging layer does.

### The better fix

Deduplicating after the fact is cleanup, not a solution. The real fix is a
`PRIMARY KEY` or `UNIQUE` constraint, so duplicates cannot be inserted at
all — which is why `source.sellers` has one and this demo table does not.

---

## Q10. EXPLAIN ANALYZE and an index

```sql
explain (analyze, buffers)
select count(*), sum(oi.price)
from source.orders o
join source.customers   c  on c.customer_id = o.customer_id
join source.order_items oi on oi.order_id   = o.order_id
where c.customer_city = 'sao paulo'
  and o.order_status <> 'canceled';

create index if not exists idx_customers_city on source.customers (customer_city);
analyze source.customers;
```

### Measured result

| | Before | After |
|---|---|---|
| Access method on `customers` | `Parallel Seq Scan` | `Parallel Bitmap Heap Scan` + `Bitmap Index Scan` |
| Rows discarded by filter | 41,950 | 0 |
| Execution time | ~41–46 ms | ~32–38 ms |

The **plan change is the reliable result**; the timing is not. Across three
runs the improvement ranged from 7% to 30% on identical data, because at
these sizes everything is already in cache and the measurement is mostly
noise. Quote the plan change, and treat a single timing as an anecdote —
benchmark repeatedly before claiming a speedup.

> The query drops the index before measuring, so re-running the file always
> compares a real before against a real after. Without that,
> `create index if not exists` leaves the index in place and the second run
> silently reports two post-index timings.

### Keywords

| Keyword | Purpose |
|---|---|
| `explain` | Shows the planner's *estimated* plan without running the query. |
| `explain analyze` | **Actually runs it** and shows real timings and row counts. |
| `buffers` | Adds how many 8 KB pages were read — `hit` = already cached, `read` = fetched from disk. |
| `analyze source.customers` | Refreshes table statistics. Unrelated to `EXPLAIN ANALYZE` despite the shared word. |

> `EXPLAIN ANALYZE` executes the statement. On an `UPDATE` or `DELETE` the
> changes really happen — wrap it in `BEGIN; ... ROLLBACK;` when testing one.

### How to read the plan

Read **inside out**: the most indented node runs first.

- `Seq Scan` — reads every row. `Rows Removed by Filter: 41950` means 84% of
  the work was discarded. That line is the signal an index would help.
- `Bitmap Index Scan` — reads the index to find matching rows, then
  `Bitmap Heap Scan` fetches them. Postgres chooses this over a plain index
  scan when many rows match (15,540 here), because sorting hits into physical
  order beats random I/O.
- `cost=` is the planner's unitless estimate; `actual time=` is real
  milliseconds. A large gap between `rows=` estimated and actual means stale
  statistics — run `ANALYZE`.

### Why only 21% faster

The index removed the scan on `customers`, but the query still sequentially
scans `orders` *and* `order_items`, which dominate. Indexing the cheapest
part of a plan yields little — **always read the plan before adding an
index**, or you optimise something that wasn't the bottleneck.

Indexes also cost: disk space, and slower writes since every `INSERT` must
update them. On this workload a 9 ms gain for a permanent write penalty is
a marginal trade, which is itself the lesson.

---

## Reference: the window function family

```sql
function() OVER (PARTITION BY ... ORDER BY ... ROWS BETWEEN ... )
                 └─────┬──────┘ └─────┬─────┘ └──────┬──────┘
                   which rows     their order    which subset
```

| Need | Function |
|---|---|
| Previous / next row's value | `lag()`, `lead()` |
| Position within a group | `row_number()`, `rank()`, `dense_rank()` |
| Running total | `sum(x) over (order by d)` |
| Moving average | `avg(x) over (order by d rows between 6 preceding and current row)` |
| First / last in group | `first_value()`, `last_value()` |
| Percentile bucket | `ntile(4)` |

Aggregates become window functions simply by adding `OVER`. `sum(x)`
collapses rows; `sum(x) over (...)` keeps them and adds a column.

---

## Running them

```bash
# whole file
docker exec -i olist_postgres psql -U olist -d olist -f - < queries/day02_analytics.sql

# interactively
docker exec -it olist_postgres psql -U olist -d olist
```

Or open `notebooks/day02_sql_analytics.ipynb`, which runs each query through
SQLAlchemy into a DataFrame.

DBeaver: host `localhost`, port **5433**, database `olist`.
