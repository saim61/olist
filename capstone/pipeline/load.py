"""staging -> warehouse. Dimensions first, then the fact.

All writes are upserts on a natural key, so the step is idempotent.
dim_customer is SCD Type 2: a changed attribute closes the current row
and opens a new one instead of overwriting history.
"""
from __future__ import annotations


def load_dim_date(cur) -> int:
    cur.execute(
        """
        insert into warehouse.dim_date (date_key, full_date, year, quarter, month,
                                        month_name, day_of_week, is_weekend, month_start)
        select (to_char(d, 'YYYYMMDD'))::integer,
               d::date,
               extract(year from d), extract(quarter from d), extract(month from d),
               trim(to_char(d, 'Month')), extract(isodow from d),
               extract(isodow from d) in (6, 7),
               date_trunc('month', d)::date
        from generate_series('2016-01-01'::date, '2019-12-31'::date, interval '1 day') d
        on conflict (date_key) do nothing
        """
    )
    return cur.rowcount


def load_dim_product(cur) -> int:
    cur.execute(
        """
        insert into warehouse.dim_product (product_id, category_pt, category_en, weight_g)
        select p.product_id,
               p.product_category_name,
               coalesce(t.product_category_name_english,
                        p.product_category_name,
                        '(uncategorised)'),
               p.product_weight_g
        from source.products p
        left join source.product_category_translation t
               on t.product_category_name = p.product_category_name
        on conflict (product_id) do update set
            category_pt = excluded.category_pt,
            category_en = excluded.category_en,
            weight_g    = excluded.weight_g
        """
    )
    return cur.rowcount


def load_dim_seller(cur) -> int:
    cur.execute(
        """
        insert into warehouse.dim_seller (seller_id, seller_city, seller_state,
                                          zip_code_prefix)
        select seller_id, seller_city, seller_state, seller_zip_code_prefix
        from source.sellers
        on conflict (seller_id) do update set
            seller_city     = excluded.seller_city,
            seller_state    = excluded.seller_state,
            zip_code_prefix = excluded.zip_code_prefix
        """
    )
    return cur.rowcount


def load_dim_geography(cur) -> int:
    """Union of customer and seller zips, so no fact needs an unknown member."""
    cur.execute(
        """
        insert into warehouse.dim_geography (zip_code_prefix, city, state)
        select distinct on (zip) zip, city, state
        from (
            select zip_code_prefix as zip, city, state from staging.customers
            union all
            select seller_zip_code_prefix, seller_city, seller_state from source.sellers
        ) z
        order by zip, city
        on conflict (zip_code_prefix) do nothing
        """
    )
    return cur.rowcount


def load_dim_customer_scd2(cur) -> tuple[int, int]:
    """Expire changed rows, insert new versions, then first-time customers.

    The collapse to one row per person must be DETERMINISTIC: staging is
    keyed on customer_id, and one customer_unique_id can own several of
    them with different cities. Without the customer_id tiebreak, each run
    picks a different address, reports a false change, and versions the row
    again forever -- an unbounded idempotency bug.
    """
    cur.execute(
        """
        create temporary table current_customer on commit drop as
        select distinct on (customer_unique_id)
               customer_unique_id, city, state, zip_code_prefix
        from staging.customers
        order by customer_unique_id, customer_id
        """
    )
    cur.execute("create index on current_customer (customer_unique_id)")

    cur.execute(
        """
        create temporary table changed on commit drop as
        select c.customer_unique_id, c.city, c.state, c.zip_code_prefix,
               d.valid_from as existing_valid_from
        from current_customer c
        join warehouse.dim_customer d
          on d.customer_unique_id = c.customer_unique_id and d.is_current
        where d.customer_city   is distinct from c.city
           or d.customer_state  is distinct from c.state
           or d.zip_code_prefix is distinct from c.zip_code_prefix
        """
    )

    # A row opened earlier today would otherwise get valid_from = valid_to.
    cur.execute(
        """
        update warehouse.dim_customer d
        set customer_city = c.city, customer_state = c.state,
            zip_code_prefix = c.zip_code_prefix
        from changed c
        where d.customer_unique_id = c.customer_unique_id
          and d.is_current and d.valid_from >= current_date
        """
    )
    corrected = cur.rowcount

    cur.execute(
        """
        update warehouse.dim_customer d
        set valid_to = current_date, is_current = false
        from changed c
        where d.customer_unique_id = c.customer_unique_id
          and d.is_current and d.valid_from < current_date
        """
    )
    expired = cur.rowcount

    cur.execute(
        """
        insert into warehouse.dim_customer (customer_unique_id, customer_city,
            customer_state, zip_code_prefix, valid_from, valid_to, is_current)
        select customer_unique_id, city, state, zip_code_prefix,
               current_date, date '9999-12-31', true
        from changed where existing_valid_from < current_date
        """
    )
    versioned = cur.rowcount

    cur.execute(
        """
        insert into warehouse.dim_customer (customer_unique_id, customer_city,
            customer_state, zip_code_prefix, valid_from, valid_to, is_current)
        select c.customer_unique_id, c.city, c.state, c.zip_code_prefix,
               date '2016-01-01', date '9999-12-31', true
        from current_customer c
        where not exists (select 1 from warehouse.dim_customer d
                          where d.customer_unique_id = c.customer_unique_id)
        """
    )
    return expired + corrected, versioned + cur.rowcount


def load_fact(cur) -> int:
    cur.execute(
        """
        insert into warehouse.fact_order_items (
            date_key, customer_key, product_key, seller_key, geo_key,
            order_id, order_item_id, order_status, price, freight_value,
            delivery_days, days_vs_estimate, is_late)
        select (to_char(o.order_purchase_timestamp, 'YYYYMMDD'))::integer,
               dc.customer_key, dp.product_key, ds.seller_key, dg.geo_key,
               oi.order_id, oi.order_item_id, o.order_status,
               oi.price, oi.freight_value,
               o.delivery_days, o.days_vs_estimate, o.is_late
        from staging.order_items oi
        join staging.orders    o on o.order_id    = oi.order_id
        join staging.customers c on c.customer_id = o.customer_id
        join warehouse.dim_product   dp on dp.product_id      = oi.product_id
        join warehouse.dim_seller    ds on ds.seller_id       = oi.seller_id
        join warehouse.dim_geography dg on dg.zip_code_prefix = c.zip_code_prefix
        -- SCD2 lookup: the version valid on the order date, not the current one.
        join warehouse.dim_customer dc
              on dc.customer_unique_id = c.customer_unique_id
             and o.order_purchase_timestamp::date >= dc.valid_from
             and o.order_purchase_timestamp::date <  dc.valid_to
        on conflict (order_id, order_item_id) do update set
            date_key         = excluded.date_key,
            customer_key     = excluded.customer_key,
            product_key      = excluded.product_key,
            seller_key       = excluded.seller_key,
            geo_key          = excluded.geo_key,
            order_status     = excluded.order_status,
            price            = excluded.price,
            freight_value    = excluded.freight_value,
            delivery_days    = excluded.delivery_days,
            days_vs_estimate = excluded.days_vs_estimate,
            is_late          = excluded.is_late
        """
    )
    return cur.rowcount


def run(cur, log) -> None:
    with log.step("load.dim_date") as c:
        c["rows_out"] = load_dim_date(cur)
    with log.step("load.dim_product") as c:
        c["rows_out"] = load_dim_product(cur)
    with log.step("load.dim_seller") as c:
        c["rows_out"] = load_dim_seller(cur)
    with log.step("load.dim_geography") as c:
        c["rows_out"] = load_dim_geography(cur)
    with log.step("load.dim_customer") as c:
        expired, inserted = load_dim_customer_scd2(cur)
        c["rows_out"], c["rows_rejected"] = inserted, expired
    with log.step("load.fact") as c:
        cur.execute("select count(*) from staging.order_items")
        c["rows_in"] = cur.fetchone()[0]
        c["rows_out"] = load_fact(cur)
