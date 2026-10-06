"""raw -> staging: clean, standardise, deduplicate, derive.

Upserts on the natural key, so re-running with the same raw data changes
nothing. Deduplication keeps the most recently extracted version of each
key, which is why raw carries _extracted_at.
"""
from __future__ import annotations


def transform_customers(cur) -> int:
    cur.execute(
        r"""
        insert into staging.customers
            (customer_id, customer_unique_id, zip_code_prefix, city, state, _loaded_at)
        select distinct on (customer_id)
            customer_id, customer_unique_id, zip_code_prefix, city, state, now()
        from (
            select
                customer_id,
                customer_unique_id,
                trim(customer_zip_code_prefix) as zip_code_prefix,
                lower(btrim(regexp_replace(customer_city, '\s+', ' ', 'g'))) as city,
                upper(trim(customer_state))    as state,
                _extracted_at
            from raw.customers
            where customer_id is not null
        ) cleaned
        order by customer_id, _extracted_at desc
        on conflict (customer_id) do update set
            customer_unique_id = excluded.customer_unique_id,
            zip_code_prefix    = excluded.zip_code_prefix,
            city               = excluded.city,
            state              = excluded.state,
            _loaded_at         = now()
        """
    )
    return cur.rowcount


def transform_orders(cur) -> int:
    cur.execute(
        """
        insert into staging.orders (
            order_id, customer_id, order_status, order_purchase_timestamp,
            order_approved_at, order_delivered_carrier_date,
            order_delivered_customer_date, order_estimated_delivery_date,
            delivery_days, days_vs_estimate, is_late, _loaded_at
        )
        select distinct on (order_id)
            order_id,
            customer_id,
            lower(trim(order_status)),
            order_purchase_timestamp,
            order_approved_at,
            order_delivered_carrier_date,
            order_delivered_customer_date,
            order_estimated_delivery_date,
            order_delivered_customer_date::date - order_purchase_timestamp::date,
            order_delivered_customer_date::date - order_estimated_delivery_date::date,
            case when order_delivered_customer_date is null then null
                 else order_delivered_customer_date > order_estimated_delivery_date
            end,
            now()
        from raw.orders
        where order_id is not null
          and order_purchase_timestamp is not null
          and order_estimated_delivery_date is not null
        order by order_id, _extracted_at desc
        on conflict (order_id) do update set
            order_status                  = excluded.order_status,
            order_approved_at             = excluded.order_approved_at,
            order_delivered_carrier_date  = excluded.order_delivered_carrier_date,
            order_delivered_customer_date = excluded.order_delivered_customer_date,
            delivery_days                 = excluded.delivery_days,
            days_vs_estimate              = excluded.days_vs_estimate,
            is_late                       = excluded.is_late,
            _loaded_at                    = now()
        """
    )
    return cur.rowcount


def transform_order_items(cur) -> int:
    cur.execute(
        """
        insert into staging.order_items (
            order_id, order_item_id, product_id, seller_id,
            shipping_limit_date, price, freight_value, _loaded_at
        )
        select distinct on (order_id, order_item_id)
            order_id, order_item_id, product_id, seller_id,
            shipping_limit_date, price, freight_value, now()
        from raw.order_items
        where order_id is not null
          and order_item_id is not null
          and price >= 0
          and freight_value >= 0
        order by order_id, order_item_id, _extracted_at desc
        on conflict (order_id, order_item_id) do update set
            product_id          = excluded.product_id,
            seller_id           = excluded.seller_id,
            shipping_limit_date = excluded.shipping_limit_date,
            price               = excluded.price,
            freight_value       = excluded.freight_value,
            _loaded_at          = now()
        """
    )
    return cur.rowcount


def run(cur, log, run_id: int) -> None:
    with log.step("transform.customers") as c:
        cur.execute("select count(*) from raw.customers")
        c["rows_in"] = cur.fetchone()[0]
        c["rows_out"] = transform_customers(cur)

    with log.step("transform.orders") as c:
        cur.execute("select count(*) from raw.orders")
        c["rows_in"] = cur.fetchone()[0]
        c["rows_out"] = transform_orders(cur)

    with log.step("transform.order_items") as c:
        cur.execute("select count(*) from raw.order_items")
        c["rows_in"] = cur.fetchone()[0]
        c["rows_out"] = transform_order_items(cur)
