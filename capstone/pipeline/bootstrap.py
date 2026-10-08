"""Create the schemas and load the CSVs into the simulated source system.

    python -m pipeline.bootstrap

Orders are split into two arrival batches by BATCH_CUTOFF so the ELT has
something to pick up incrementally on its second run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .db import ROOT, connect

CSV_DIR = Path(os.getenv("CSV_DIR", "/data/olist"))
DDL_DIR = ROOT / "sql" / "ddl"
CUTOFF = os.getenv("BATCH_CUTOFF", "2018-01-01")

# Parents first: the source FKs reject children whose parent is absent.
LOADS: list[tuple[str, str, list[str]]] = [
    ("source.product_category_translation", "product_category_name_translation.csv",
     ["product_category_name", "product_category_name_english"]),
    ("source.geolocation", "olist_geolocation_dataset.csv",
     ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
      "geolocation_city", "geolocation_state"]),
    ("source.customers", "olist_customers_dataset.csv",
     ["customer_id", "customer_unique_id", "customer_zip_code_prefix",
      "customer_city", "customer_state"]),
    ("source.sellers", "olist_sellers_dataset.csv",
     ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"]),
    ("source.products", "olist_products_dataset.csv",
     ["product_id", "product_category_name", "product_name_lenght",
      "product_description_lenght", "product_photos_qty", "product_weight_g",
      "product_length_cm", "product_height_cm", "product_width_cm"]),
    ("source.orders", "olist_orders_dataset.csv",
     ["order_id", "customer_id", "order_status", "order_purchase_timestamp",
      "order_approved_at", "order_delivered_carrier_date",
      "order_delivered_customer_date", "order_estimated_delivery_date"]),
    ("source.order_items", "olist_order_items_dataset.csv",
     ["order_id", "order_item_id", "product_id", "seller_id",
      "shipping_limit_date", "price", "freight_value"]),
    ("source.order_payments", "olist_order_payments_dataset.csv",
     ["order_id", "payment_sequential", "payment_type",
      "payment_installments", "payment_value"]),
    ("source.order_reviews", "olist_order_reviews_dataset.csv",
     ["review_id", "order_id", "review_score", "review_comment_title",
      "review_comment_message", "review_creation_date", "review_answer_timestamp"]),
]


def apply_ddl(cur) -> None:
    for path in sorted(DDL_DIR.glob("*.sql")):
        cur.execute(path.read_text(encoding="utf-8"))
        print(f"  applied {path.name}")


def ingest_csvs(cur) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, csv_name, columns in LOADS:
        path = CSV_DIR / csv_name
        if not path.exists():
            raise FileNotFoundError(f"{path} missing -- see README for the dataset")
        sql = (f"copy {table} ({', '.join(columns)}) "
               "from stdin with (format csv, header true)")
        # COPY casts straight to the declared column types -- no inference,
        # so leading zeros on zip prefixes survive.
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            cur.copy_expert(sql, fh)
        cur.execute(f"select count(*) from {table}")
        counts[table] = cur.fetchone()[0]
        print(f"  {table:40} {counts[table]:>9,}")
    return counts


def batch_summary(cur) -> None:
    cur.execute(
        """
        select case when order_purchase_timestamp < %s
                    then 'batch 1' else 'batch 2' end as batch,
               count(*),
               min(order_purchase_timestamp)::date,
               max(order_purchase_timestamp)::date
        from source.orders group by 1 order by 1
        """,
        (CUTOFF,),
    )
    print(f"\n  arrival batches (cutoff {CUTOFF}):")
    for batch, n, lo, hi in cur.fetchall():
        print(f"    {batch}  {n:>7,} orders   {lo} .. {hi}")


def main() -> int:
    print("bootstrap: schemas + source data")
    with connect() as conn:
        cur = conn.cursor()
        apply_ddl(cur)
        print()
        ingest_csvs(cur)
        batch_summary(cur)
    print("\nbootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
