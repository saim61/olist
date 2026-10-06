"""staging -> warehouse, using upserts so the step is idempotent.

Dimensions load before the fact, because the fact resolves their surrogate
keys. dim_customer is SCD Type 2: a changed attribute closes the current
row and opens a new one rather than overwriting.
"""
from __future__ import annotations


def load_dim_product(cur) -> int:
    cur.execute(
        """
        insert into warehouse.dim_product
            (product_id, category_pt, category_en, weight_g,
             length_cm, height_cm, width_cm)
        select
            p.product_id,
            p.product_category_name,
            coalesce(t.product_category_name_english,
                     p.product_category_name,
                     '(uncategorised)'),
            p.product_weight_g, p.product_length_cm,
            p.product_height_cm, p.product_width_cm
        from source.products p
        left join source.product_category_translation t
               on t.product_category_name = p.product_category_name
        on conflict (product_id) do update set
            category_pt = excluded.category_pt,
            category_en = excluded.category_en,
            weight_g    = excluded.weight_g,
            length_cm   = excluded.length_cm,
            height_cm   = excluded.height_cm,
            width_cm    = excluded.width_cm
        """
    )
    return cur.rowcount


def load_dim_seller(cur) -> int:
    cur.execute(
        """
        insert into warehouse.dim_seller
            (seller_id, seller_city, seller_state, zip_code_prefix)
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
    cur.execute(
        """
        insert into warehouse.dim_geography (zip_code_prefix, city, state)
        select distinct on (zip_code_prefix) zip_code_prefix, city, state
        from staging.customers
        order by zip_code_prefix
        on conflict (zip_code_prefix) do nothing
        """
    )
    return cur.rowcount


def load_dim_customer_scd2(cur) -> tuple[int, int]:
    """Close changed rows, then insert new versions and first-time customers.

    staging.customers is keyed on customer_id, but a person
    (customer_unique_id) may own several customer_id values carrying
    different cities. The collapse to one row per person must therefore be
    DETERMINISTIC -- without the customer_id tiebreak below, each run picks
    a different address, reports a spurious change, and versions the row
    again forever.
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
        create temporary table changed_customers on commit drop as
        select c.customer_unique_id, c.city, c.state, c.zip_code_prefix,
               d.valid_from as existing_valid_from
        from current_customer c
        join warehouse.dim_customer d
          on d.customer_unique_id = c.customer_unique_id
         and d.is_current
        where d.customer_city   is distinct from c.city
           or d.customer_state  is distinct from c.state
           or d.zip_code_prefix is distinct from c.zip_code_prefix
        """
    )

    # A row created earlier today would otherwise get valid_from = valid_to,
    # a zero-length version. Correct it in place instead.
    cur.execute(
        """
        update warehouse.dim_customer d
        set customer_city   = c.city,
            customer_state  = c.state,
            zip_code_prefix = c.zip_code_prefix
        from changed_customers c
        where d.customer_unique_id = c.customer_unique_id
          and d.is_current
          and d.valid_from >= current_date
        """
    )
    corrected = cur.rowcount

    cur.execute(
        """
        update warehouse.dim_customer d
        set valid_to = current_date, is_current = false
        from changed_customers c
        where d.customer_unique_id = c.customer_unique_id
          and d.is_current
          and d.valid_from < current_date
        """
    )
    expired = cur.rowcount

    cur.execute(
        """
        insert into warehouse.dim_customer
            (customer_unique_id, customer_city, customer_state,
             zip_code_prefix, valid_from, valid_to, is_current)
        select customer_unique_id, city, state, zip_code_prefix,
               current_date, date '9999-12-31', true
        from changed_customers
        where existing_valid_from < current_date
        """
    )
    versioned = cur.rowcount

    cur.execute(
        """
        insert into warehouse.dim_customer
            (customer_unique_id, customer_city, customer_state,
             zip_code_prefix, valid_from, valid_to, is_current)
        select c.customer_unique_id, c.city, c.state, c.zip_code_prefix,
               date '2016-01-01', date '9999-12-31', true
        from current_customer c
        where not exists (
            select 1 from warehouse.dim_customer d
            where d.customer_unique_id = c.customer_unique_id
        )
        """
    )
    return expired + corrected, versioned + cur.rowcount


def load_fact(cur) -> int:
    cur.execute(
        """
        insert into warehouse.fact_order_items (
            date_key, customer_key, product_key, seller_key, geo_key,
            order_id, order_item_id, order_status,
            price, freight_value,
            delivery_days, days_vs_estimate, is_late
        )
        select
            (to_char(o.order_purchase_timestamp, 'YYYYMMDD'))::integer,
            dc.customer_key, dp.product_key, ds.seller_key,
            coalesce(dg.geo_key, -1),
            oi.order_id, oi.order_item_id, o.order_status,
            oi.price, oi.freight_value,
            o.delivery_days, o.days_vs_estimate, o.is_late
        from staging.order_items oi
        join staging.orders    o on o.order_id    = oi.order_id
        join staging.customers c on c.customer_id = o.customer_id
        join warehouse.dim_product dp on dp.product_id = oi.product_id
        join warehouse.dim_seller  ds on ds.seller_id  = oi.seller_id
        join warehouse.dim_customer dc
              on dc.customer_unique_id = c.customer_unique_id
             and o.order_purchase_timestamp::date >= dc.valid_from
             and o.order_purchase_timestamp::date <  dc.valid_to
        left join warehouse.dim_geography dg
               on dg.zip_code_prefix = c.zip_code_prefix
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


def run(cur, log, run_id: int) -> None:
    with log.step("load.dim_product") as c:
        c["rows_out"] = load_dim_product(cur)

    with log.step("load.dim_seller") as c:
        c["rows_out"] = load_dim_seller(cur)

    with log.step("load.dim_geography") as c:
        c["rows_out"] = load_dim_geography(cur)

    with log.step("load.dim_customer") as c:
        expired, inserted = load_dim_customer_scd2(cur)
        c["rows_out"] = inserted
        c["rows_rejected"] = expired

    with log.step("load.fact") as c:
        cur.execute("select count(*) from staging.order_items")
        c["rows_in"] = cur.fetchone()[0]
        c["rows_out"] = load_fact(cur)
