"""
Day 1 -- load the raw Olist CSVs into the `source` schema.

Uses COPY ... FROM STDIN rather than pandas.to_sql on purpose:

  * COPY does no type inference. Every value is cast straight to the
    column type declared in the DDL, so the DDL is the single contract.
    pandas would infer customer_zip_code_prefix as int64 and silently
    turn '09790' into 9790 for 24% of rows.
  * Postgres' CSV parser handles quoted embedded newlines, which the
    reviews file has 5,495 of.
  * It streams, so nothing large is held in memory.

Re-runnable: truncates before loading, so running twice is a no-op.

    python pipeline/load_source.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "olist"

load_dotenv(ROOT / ".env")

DSN = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    user=os.getenv("POSTGRES_USER", "olist"),
    password=os.getenv("POSTGRES_PASSWORD", "olist"),
    dbname=os.getenv("POSTGRES_DB", "olist"),
)

# (table, csv file, columns)
# Order matters: parents before children, or the FKs reject the rows.
# Columns are listed explicitly so the CSV's column order is never
# assumed, and so geolocation's IDENTITY key is left for Postgres.
LOADS: list[tuple[str, str, list[str]]] = [
    (
        "source.product_category_translation",
        "product_category_name_translation.csv",
        ["product_category_name", "product_category_name_english"],
    ),
    (
        "source.geolocation",
        "olist_geolocation_dataset.csv",
        ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
         "geolocation_city", "geolocation_state"],
    ),
    (
        "source.customers",
        "olist_customers_dataset.csv",
        ["customer_id", "customer_unique_id", "customer_zip_code_prefix",
         "customer_city", "customer_state"],
    ),
    (
        "source.sellers",
        "olist_sellers_dataset.csv",
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
    ),
    (
        "source.products",
        "olist_products_dataset.csv",
        ["product_id", "product_category_name", "product_name_lenght",
         "product_description_lenght", "product_photos_qty", "product_weight_g",
         "product_length_cm", "product_height_cm", "product_width_cm"],
    ),
    (
        "source.orders",
        "olist_orders_dataset.csv",
        ["order_id", "customer_id", "order_status", "order_purchase_timestamp",
         "order_approved_at", "order_delivered_carrier_date",
         "order_delivered_customer_date", "order_estimated_delivery_date"],
    ),
    (
        "source.order_items",
        "olist_order_items_dataset.csv",
        ["order_id", "order_item_id", "product_id", "seller_id",
         "shipping_limit_date", "price", "freight_value"],
    ),
    (
        "source.order_payments",
        "olist_order_payments_dataset.csv",
        ["order_id", "payment_sequential", "payment_type",
         "payment_installments", "payment_value"],
    ),
    (
        "source.order_reviews",
        "olist_order_reviews_dataset.csv",
        ["review_id", "order_id", "review_score", "review_comment_title",
         "review_comment_message", "review_creation_date",
         "review_answer_timestamp"],
    ),
]

# Expected counts from profiling -- the load fails loudly if reality differs.
EXPECTED = {
    "source.product_category_translation": 71,
    "source.geolocation": 1_000_163,
    "source.customers": 99_441,
    "source.sellers": 3_095,
    "source.products": 32_951,
    "source.orders": 99_441,
    "source.order_items": 112_650,
    "source.order_payments": 103_886,
    "source.order_reviews": 99_224,
}


def load_table(cur, table: str, csv_file: str, columns: list[str]) -> tuple[int, float]:
    path = DATA / csv_file
    if not path.exists():
        raise FileNotFoundError(f"{path} -- see README for how to fetch the dataset")

    collist = ", ".join(columns)
    sql = f"COPY {table} ({collist}) FROM STDIN WITH (FORMAT csv, HEADER true)"

    t0 = time.perf_counter()
    # utf-8-sig strips the BOM on the translation file. Harmless elsewhere.
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        cur.copy_expert(sql, fh)
    elapsed = time.perf_counter() - t0

    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0], elapsed


def main() -> int:
    print(f"connecting to {DSN['host']}:{DSN['port']}/{DSN['dbname']} as {DSN['user']}")
    conn = psycopg2.connect(**DSN)
    conn.autocommit = False
    cur = conn.cursor()

    # One statement, so FK order does not matter here. RESTART IDENTITY
    # resets geolocation's sequence so re-runs produce identical keys.
    tables = [t for t, _, _ in LOADS]
    print(f"truncating {len(tables)} tables\n")
    cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")

    print(f"{'table':<40} {'rows':>10} {'secs':>7}   status")
    print("-" * 72)

    failures = []
    total_rows = 0
    t_start = time.perf_counter()

    for table, csv_file, columns in LOADS:
        rows, secs = load_table(cur, table, csv_file, columns)
        total_rows += rows
        want = EXPECTED[table]
        ok = rows == want
        status = "OK" if ok else f"MISMATCH (expected {want:,})"
        if not ok:
            failures.append(f"{table}: got {rows:,}, expected {want:,}")
        print(f"{table:<40} {rows:>10,} {secs:>7.2f}   {status}")

    print("-" * 72)
    print(f"{'TOTAL':<40} {total_rows:>10,} {time.perf_counter() - t_start:>7.2f}")

    if failures:
        conn.rollback()
        print("\nROLLED BACK -- row counts did not match profiling:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    conn.commit()
    print("\ncommitted.")

    # Cheap sanity checks that the types survived the round trip.
    print("\nverification:")
    checks = [
        ("zip leading zeros preserved",
         "SELECT COUNT(*) FROM source.customers WHERE customer_zip_code_prefix LIKE '0%'",
         lambda v: v == 23_995),
        ("reviews with embedded newline in comment",
         r"SELECT COUNT(*) FROM source.order_reviews WHERE review_comment_message LIKE E'%\n%'",
         lambda v: v > 0),
        ("orders with NULL delivery date",
         "SELECT COUNT(*) FROM source.orders WHERE order_delivered_customer_date IS NULL",
         lambda v: v == 2_965),
        ("distinct product categories",
         "SELECT COUNT(DISTINCT product_category_name) FROM source.products",
         lambda v: v == 73),
    ]
    for label, sql, ok_fn in checks:
        cur.execute(sql)
        val = cur.fetchone()[0]
        print(f"  {'OK ' if ok_fn(val) else '** '}{label:<45} {val:,}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
