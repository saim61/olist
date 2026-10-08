"""source -> raw, incrementally, on a watermark over purchase timestamp.

Children are pulled by reference to the orders landed in this run, so an
order and its items always arrive together.
"""
from __future__ import annotations

from datetime import datetime

from .db import get_watermark, set_watermark

WM = "raw.orders"


def extract_orders(cur, run_id: int,
                   max_ts: datetime | str | None = None) -> tuple[int, datetime | None]:
    wm = get_watermark(cur, WM)
    sql = """
        insert into raw.orders (
            order_id, customer_id, order_status, order_purchase_timestamp,
            order_approved_at, order_delivered_carrier_date,
            order_delivered_customer_date, order_estimated_delivery_date, _run_id)
        select order_id, customer_id, order_status, order_purchase_timestamp,
               order_approved_at, order_delivered_carrier_date,
               order_delivered_customer_date, order_estimated_delivery_date, %s
        from source.orders
        where order_purchase_timestamp > %s
    """
    params: list = [run_id, wm]
    if max_ts is not None:
        sql += " and order_purchase_timestamp < %s"
        params.append(max_ts)

    cur.execute(sql, params)
    rows = cur.rowcount
    cur.execute("select max(order_purchase_timestamp) from raw.orders where _run_id = %s",
                (run_id,))
    return rows, cur.fetchone()[0]


def extract_children(cur, run_id: int) -> int:
    cur.execute(
        """
        insert into raw.order_items (order_id, order_item_id, product_id, seller_id,
                                     shipping_limit_date, price, freight_value, _run_id)
        select oi.order_id, oi.order_item_id, oi.product_id, oi.seller_id,
               oi.shipping_limit_date, oi.price, oi.freight_value, %s
        from source.order_items oi
        where oi.order_id in (select order_id from raw.orders where _run_id = %s)
        """,
        (run_id, run_id),
    )
    items = cur.rowcount

    cur.execute(
        """
        insert into raw.customers (customer_id, customer_unique_id,
                                   customer_zip_code_prefix, customer_city,
                                   customer_state, _run_id)
        select c.customer_id, c.customer_unique_id, c.customer_zip_code_prefix,
               c.customer_city, c.customer_state, %s
        from source.customers c
        where c.customer_id in (select customer_id from raw.orders where _run_id = %s)
        """,
        (run_id, run_id),
    )
    return items + cur.rowcount


def run(cur, log, run_id: int, max_ts: datetime | str | None = None) -> int:
    with log.step("extract.orders") as c:
        rows, new_wm = extract_orders(cur, run_id, max_ts)
        c["rows_in"] = c["rows_out"] = rows

    with log.step("extract.children") as c:
        c["rows_out"] = extract_children(cur, run_id)

    # Advance only after both steps succeed, so a mid-run failure replays.
    if new_wm is not None:
        set_watermark(cur, WM, new_wm)
    return rows
