from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_py = sys.executable
os.environ.setdefault("PYSPARK_PYTHON", _py)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", _py)
os.environ.setdefault("HADOOP_HOME", r"C:\hadoop")

from pyspark.sql import DataFrame, SparkSession, Window  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    DecimalType, IntegerType, StringType, StructField, StructType, TimestampType,
)

DATA = ROOT / "data" / "olist"
OUT = ROOT / "data" / "marts"

# ----------------------------------------------------------------------
# Explicit schemas.
# ----------------------------------------------------------------------
ORDERS = StructType([
    StructField("order_id", StringType(), False),
    StructField("customer_id", StringType(), False),
    StructField("order_status", StringType(), False),
    StructField("order_purchase_timestamp", TimestampType(), False),
    StructField("order_approved_at", TimestampType(), True),
    StructField("order_delivered_carrier_date", TimestampType(), True),
    StructField("order_delivered_customer_date", TimestampType(), True),
    StructField("order_estimated_delivery_date", TimestampType(), False),
])

ORDER_ITEMS = StructType([
    StructField("order_id", StringType(), False),
    StructField("order_item_id", IntegerType(), False),
    StructField("product_id", StringType(), False),
    StructField("seller_id", StringType(), False),
    StructField("shipping_limit_date", TimestampType(), True),
    StructField("price", DecimalType(10, 2), False),
    StructField("freight_value", DecimalType(10, 2), False),
])

CUSTOMERS = StructType([
    StructField("customer_id", StringType(), False),
    StructField("customer_unique_id", StringType(), False),
    StructField("customer_zip_code_prefix", StringType(), False),
    StructField("customer_city", StringType(), False),
    StructField("customer_state", StringType(), False),
])

PRODUCTS = StructType([
    StructField("product_id", StringType(), False),
    StructField("product_category_name", StringType(), True),
    StructField("product_name_lenght", IntegerType(), True),
    StructField("product_description_lenght", IntegerType(), True),
    StructField("product_photos_qty", IntegerType(), True),
    StructField("product_weight_g", IntegerType(), True),
    StructField("product_length_cm", IntegerType(), True),
    StructField("product_height_cm", IntegerType(), True),
    StructField("product_width_cm", IntegerType(), True),
])

SELLERS = StructType([
    StructField("seller_id", StringType(), False),
    StructField("seller_zip_code_prefix", StringType(), False),
    StructField("seller_city", StringType(), False),
    StructField("seller_state", StringType(), False),
])

TRANSLATION = StructType([
    StructField("product_category_name", StringType(), False),
    StructField("product_category_name_english", StringType(), False),
])

def build_spark() -> SparkSession:
    return (
        SparkSession.builder
        .appName("olist-marts")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )

def read_csv(spark: SparkSession, name: str, schema: StructType) -> DataFrame:
    return (
        spark.read
        .option("header", True)
        .option("multiLine", True)
        .option("escape", '"')
        .option("timestampFormat", "yyyy-MM-dd HH:mm:ss")
        .schema(schema)
        .csv(str(DATA / name))
    )

def read_all(spark: SparkSession) -> dict[str, DataFrame]:
    return {
        "orders": read_csv(spark, "olist_orders_dataset.csv", ORDERS),
        "items": read_csv(spark, "olist_order_items_dataset.csv", ORDER_ITEMS),
        "customers": read_csv(spark, "olist_customers_dataset.csv", CUSTOMERS),
        "products": read_csv(spark, "olist_products_dataset.csv", PRODUCTS),
        "sellers": read_csv(spark, "olist_sellers_dataset.csv", SELLERS),
        "translation": read_csv(
            spark, "product_category_name_translation.csv", TRANSLATION),
    }

def build_fact(df: dict[str, DataFrame], broadcast: bool = True) -> DataFrame:
    """Rebuild fact_order_items: joins plus derived delivery measures."""
    products = df["products"].join(
        F.broadcast(df["translation"]) if broadcast else df["translation"],
        on="product_category_name", how="left",
    ).select(
        "product_id",
        F.coalesce(
            F.col("product_category_name_english"),
            F.col("product_category_name"),
            F.lit("(uncategorised)"),
        ).alias("category"),
    )

    small_products = F.broadcast(products) if broadcast else products
    small_sellers = F.broadcast(df["sellers"]) if broadcast else df["sellers"]
    small_customers = F.broadcast(df["customers"]) if broadcast else df["customers"]

    return (
        df["items"]
        .join(df["orders"], on="order_id", how="inner")
        .join(small_customers, on="customer_id", how="inner")
        .join(small_products, on="product_id", how="inner")
        .join(small_sellers, on="seller_id", how="inner")
        .withColumn(
            "delivery_days",
            F.datediff(F.col("order_delivered_customer_date"),
                       F.col("order_purchase_timestamp")),
        )
        .withColumn(
            "days_vs_estimate",
            F.datediff(F.col("order_delivered_customer_date"),
                       F.col("order_estimated_delivery_date")),
        )
        .withColumn(
            "is_late",
            F.when(F.col("order_delivered_customer_date").isNull(), None)
             .otherwise(F.col("order_delivered_customer_date")
                        > F.col("order_estimated_delivery_date")),
        )
        .withColumn("order_date", F.to_date("order_purchase_timestamp"))
        .withColumn("year", F.year("order_purchase_timestamp"))
        .withColumn("month", F.month("order_purchase_timestamp"))
    )

def plan_of(df: DataFrame) -> str:
    return df._jdf.queryExecution().executedPlan().toString()

def summarise_plan(label: str, plan: str) -> dict:
    print("\n" + "=" * 70)
    print(f"JOIN STRATEGY: {label}")
    print("=" * 70)
    for line in plan.splitlines():
        if any(k in line for k in ("Join", "Exchange hashpartitioning", "Sort ")):
            print("  " + line.strip()[:108])
    stats = {
        "sort_merge": plan.count("SortMergeJoin"),
        "broadcast_join": plan.count("BroadcastHashJoin"),
        "shuffles": plan.count("Exchange hashpartitioning"),
    }
    print(f"\n  SortMergeJoin={stats['sort_merge']}  "
          f"BroadcastHashJoin={stats['broadcast_join']}  "
          f"shuffle Exchanges={stats['shuffles']}")
    return stats

def compare_join_strategies(spark: SparkSession, df: dict[str, DataFrame]) -> None:
    original = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")

    spark.conf.set("spark.sql.autoBroadcastJoinThreshold", -1)
    without = summarise_plan(
        "auto-broadcast DISABLED (forces sort-merge)",
        plan_of(build_fact(df, broadcast=False)),
    )

    spark.conf.set("spark.sql.autoBroadcastJoinThreshold", original)
    with_bc = summarise_plan(
        "broadcast hints ON",
        plan_of(build_fact(df, broadcast=True)),
    )

    print("\n" + "-" * 70)
    print(f"  {'':34}{'sort-merge':>14}{'broadcast':>14}")
    for k in ("sort_merge", "broadcast_join", "shuffles"):
        print(f"  {k:34}{without[k]:>14}{with_bc[k]:>14}")
    print(f"\n  shuffles avoided: {without['shuffles'] - with_bc['shuffles']}")

def build_revenue_mart(fact: DataFrame) -> DataFrame:
    """Daily revenue per category and state, plus a 7-day rolling average."""
    daily = (
        fact.filter(F.col("order_status") != "canceled")
        .groupBy("order_date", "year", "month", "category", "customer_state")
        .agg(
            F.sum("price").alias("revenue"),
            F.countDistinct("order_id").alias("orders"),
            F.count("*").alias("items"),
        )
    )

    w = (
        Window.partitionBy("category", "customer_state")
        .orderBy("order_date")
        .rowsBetween(-6, 0)
    )

    return (
        daily
        .withColumn("revenue_7d_avg", F.round(F.avg("revenue").over(w), 2))
        .withColumn("days_in_window", F.count("revenue").over(w))
    )

def build_delivery_mart(fact: DataFrame) -> DataFrame:
    return (
        fact.filter(F.col("order_status") == "delivered")
        .filter(F.col("delivery_days").isNotNull())
        .groupBy("year", "month", "customer_state")
        .agg(
            F.count("*").alias("delivered_items"),
            F.round(F.avg("delivery_days"), 1).alias("avg_delivery_days"),
            F.round(F.avg("days_vs_estimate"), 1).alias("avg_days_vs_promise"),
            F.round(100.0 * F.sum(F.col("is_late").cast("int")) / F.count("*"), 2)
             .alias("late_pct"),
        )
    )

def write_partitioned(df: DataFrame, name: str) -> Path:
    path = OUT / name
    (df.repartition("year", "month")
       .write.mode("overwrite")
       .partitionBy("year", "month")
       .parquet(str(path)))
    return path

def show_layout(path: Path, limit: int = 8) -> None:
    print(f"\n  {path.relative_to(ROOT)}")
    parts = sorted(p for p in path.rglob("*.parquet"))
    for p in parts[:limit]:
        print(f"    {str(p.relative_to(path)):55} {p.stat().st_size / 1024:8.1f} KB")
    if len(parts) > limit:
        print(f"    ... {len(parts) - limit} more files")
    total = sum(p.stat().st_size for p in parts)
    print(f"    {len(parts)} files, {total / 1024 / 1024:.2f} MB total")

def report_shuffles(spark: SparkSession, top: int = 6) -> None:
    """Find the stages that shuffled the most, via the Spark REST API.

    Same numbers the UI shows at localhost:4040/stages, but captured rather
    than eyeballed.
    """
    import json
    import urllib.request

    base = (spark.sparkContext.uiWebUrl or "http://localhost:4040").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/v1/applications", timeout=10) as r:
            app_id = json.load(r)[0]["id"]
        with urllib.request.urlopen(
                f"{base}/api/v1/applications/{app_id}/stages", timeout=10) as r:
            stages = json.load(r)
    except Exception as exc:
        print(f"  could not reach the Spark REST API: {exc}")
        return

    MB = 1024 * 1024
    rows = [
        {
            "id": s.get("stageId"),
            "name": (s.get("name") or "")[:46],
            "tasks": s.get("numTasks", 0),
            "read": s.get("shuffleReadBytes", 0) / MB,
            "write": s.get("shuffleWriteBytes", 0) / MB,
            "spill": s.get("diskBytesSpilled", 0) / MB,
            "secs": s.get("executorRunTime", 0) / 1000.0,
        }
        for s in stages
        if s.get("status") == "COMPLETE"
    ]
    rows.sort(key=lambda r: r["read"] + r["write"], reverse=True)

    print("\n" + "=" * 70)
    print("SHUFFLE BY STAGE (the Spark UI's own metrics)")
    print("=" * 70)
    print(f"  {'stage':>5} {'tasks':>6} {'read MB':>9} {'write MB':>9} "
          f"{'spill MB':>9} {'cpu s':>7}  name")
    for r in rows[:top]:
        print(f"  {r['id']:>5} {r['tasks']:>6} {r['read']:>9.2f} {r['write']:>9.2f} "
              f"{r['spill']:>9.2f} {r['secs']:>7.1f}  {r['name']}")

    if rows:
        w = rows[0]
        print(f"\n  biggest shuffle: stage {w['id']} -- "
              f"{w['read']:.2f} MB read + {w['write']:.2f} MB written "
              f"across {w['tasks']} tasks")
        print(f"  total shuffled across all stages: "
              f"{sum(r['read'] + r['write'] for r in rows):.2f} MB")
        spilled = sum(r["spill"] for r in rows)
        print(f"  total spilled to disk: {spilled:.2f} MB"
              + ("  (none -- everything fit in memory)" if spilled == 0 else ""))


def main() -> int:
    hold = "--hold" in sys.argv
    spark = build_spark()
    spark.sparkContext.setLogLevel("ERROR")
    print(f"Spark {spark.version}   UI: http://localhost:4040")

    df = read_all(spark)
    print(f"\n  orders={df['orders'].count():,}  items={df['items'].count():,}")

    compare_join_strategies(spark, df)

    fact = build_fact(df, broadcast=True).cache()
    print(f"\n  fact rows: {fact.count():,}")

    revenue = build_revenue_mart(fact)
    delivery = build_delivery_mart(fact)

    print("\n" + "=" * 70)
    print("WRITING PARQUET, PARTITIONED BY year/month")
    print("=" * 70)
    show_layout(write_partitioned(revenue, "mart_revenue_daily"))
    show_layout(write_partitioned(delivery, "mart_delivery_performance"))

    print("\n  sample -- rolling average once the window is full:")
    (revenue.filter((F.col("category") == "health_beauty")
                    & (F.col("customer_state") == "SP")
                    & (F.col("days_in_window") == 7))
            .orderBy("order_date")
            .select("order_date", "revenue", "revenue_7d_avg", "orders")
            .show(8, truncate=False))

    print("  read back from Parquet (partition pruning on year=2018):")
    back = spark.read.parquet(str(OUT / "mart_revenue_daily"))
    print(f"    all partitions : {back.count():,} rows")
    print(f"    year=2018 only : {back.filter(F.col('year') == 2018).count():,} rows")

    report_shuffles(spark)

    fact.unpersist()
    if hold:
        input("\n  Spark UI live at http://localhost:4040 -- press Enter to stop... ")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
