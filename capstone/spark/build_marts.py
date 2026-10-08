"""PySpark marts, built from the warehouse star schema.

    python spark/build_marts.py

Three Parquet outputs, each partitioned by year and month:
  mart_revenue_daily        revenue by category and state, 7-day rolling avg
  mart_delivery_performance late %, average delay, by state and seller
  mart_customer_cohorts     retention by first-order month

Reads over JDBC rather than re-reading the CSVs: the warehouse is the
single source of truth, and the marts are derived from it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.getenv("MARTS_DIR", "/app/marts"))
JDBC_JAR = os.getenv("POSTGRES_JDBC", "/opt/jars/postgresql.jar")

from pyspark.sql import DataFrame, SparkSession, Window  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402


def jdbc_url() -> str:
    return (f"jdbc:postgresql://{os.getenv('POSTGRES_HOST', 'postgres')}:"
            f"{os.getenv('POSTGRES_PORT', '5432')}/"
            f"{os.getenv('POSTGRES_DB', 'olist_capstone')}")


def spark_session() -> SparkSession:
    return (
        SparkSession.builder
        .appName("olist-capstone-marts")
        .master("local[*]")
        .config("spark.jars", JDBC_JAR)
        .config("spark.driver.extraClassPath", JDBC_JAR)
        # 200 default partitions on ~100k rows is all overhead, no benefit.
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def read_table(spark: SparkSession, table: str) -> DataFrame:
    return (spark.read.format("jdbc")
            .option("url", jdbc_url())
            .option("dbtable", table)
            .option("user", os.getenv("POSTGRES_USER", "olist"))
            .option("password", os.getenv("POSTGRES_PASSWORD", "olist"))
            .option("driver", "org.postgresql.Driver")
            .load())


def enriched_fact(spark: SparkSession) -> DataFrame:
    """Fact joined to its dimensions. Dimensions are broadcast: all small."""
    fact = read_table(spark, "warehouse.fact_order_items")
    dim_date = read_table(spark, "warehouse.dim_date").select(
        "date_key", "full_date", "year", "month", "month_start")
    dim_product = read_table(spark, "warehouse.dim_product").select(
        "product_key", "category_en")
    dim_geo = read_table(spark, "warehouse.dim_geography").select(
        "geo_key", F.col("state").alias("customer_state"))
    dim_seller = read_table(spark, "warehouse.dim_seller").select(
        "seller_key", "seller_id", "seller_state")
    dim_customer = read_table(spark, "warehouse.dim_customer").select(
        "customer_key", "customer_unique_id")

    return (fact
            .join(F.broadcast(dim_date), "date_key")
            .join(F.broadcast(dim_product), "product_key")
            .join(F.broadcast(dim_geo), "geo_key")
            .join(F.broadcast(dim_seller), "seller_key")
            .join(F.broadcast(dim_customer), "customer_key"))


def mart_revenue_daily(fact: DataFrame) -> DataFrame:
    # Cast the money sum to double. NUMERIC arrives from JDBC as DecimalType,
    # which Parquet stores as decimal128 and pandas then surfaces as objects
    # of Decimal -- awkward for every downstream consumer.
    daily = (fact.filter(F.col("order_status") != "canceled")
             .groupBy("full_date", "year", "month", "category_en", "customer_state")
             .agg(F.sum("price").cast("double").alias("revenue"),
                  F.countDistinct("order_id").alias("orders"),
                  F.count("*").alias("items")))

    # 7 ROWS, which equals 7 days only because the grouping above yields at
    # most one row per category/state/day. Days with no sales are absent.
    w = (Window.partitionBy("category_en", "customer_state")
         .orderBy("full_date").rowsBetween(-6, 0))

    return (daily
            .withColumn("revenue_7d_avg", F.round(F.avg("revenue").over(w), 2))
            .withColumn("days_in_window", F.count("revenue").over(w))
            .withColumnRenamed("full_date", "order_date")
            .withColumnRenamed("category_en", "category"))


def mart_delivery_performance(fact: DataFrame) -> DataFrame:
    return (fact
            .filter(F.col("order_status") == "delivered")
            .filter(F.col("delivery_days").isNotNull())
            .groupBy("year", "month", "customer_state", "seller_id", "seller_state")
            .agg(F.count("*").alias("delivered_items"),
                 F.round(F.avg("delivery_days"), 1).alias("avg_delivery_days"),
                 F.round(F.avg("days_vs_estimate"), 1).alias("avg_days_vs_promise"),
                 F.sum(F.col("is_late").cast("int")).alias("late_items"),
                 F.round(100.0 * F.sum(F.col("is_late").cast("int"))
                         / F.count("*"), 2).alias("late_pct")))


def mart_customer_cohorts(fact: DataFrame) -> DataFrame:
    """Retention by first-order month, measured in months since acquisition."""
    orders = (fact.filter(F.col("order_status") != "canceled")
              .select("customer_unique_id", "order_id", "full_date").distinct())

    first = (orders.groupBy("customer_unique_id")
             .agg(F.trunc(F.min("full_date"), "month").alias("cohort_month")))

    joined = (orders.join(first, "customer_unique_id")
              .withColumn("order_month", F.trunc(F.col("full_date"), "month"))
              .withColumn("month_offset",
                          F.months_between(F.col("order_month"),
                                           F.col("cohort_month")).cast("int")))

    cohort = (joined.groupBy("cohort_month", "month_offset")
              .agg(F.countDistinct("customer_unique_id").alias("customers"),
                   F.countDistinct("order_id").alias("orders")))

    size = (cohort.filter(F.col("month_offset") == 0)
            .select("cohort_month", F.col("customers").alias("cohort_size")))

    return (cohort.join(F.broadcast(size), "cohort_month")
            .withColumn("retention_pct",
                        F.round(100.0 * F.col("customers") / F.col("cohort_size"), 2))
            .withColumn("year", F.year("cohort_month"))
            .withColumn("month", F.month("cohort_month"))
            .filter(F.col("month_offset").between(0, 3)))


def write_mart(df: DataFrame, name: str) -> tuple[int, int, float]:
    path = OUT / name
    # repartition before partitionBy, or each in-memory partition writes its
    # own file per month and you get hundreds of tiny files.
    (df.repartition("year", "month")
       .write.mode("overwrite").partitionBy("year", "month").parquet(str(path)))
    files = sorted(path.rglob("*.parquet"))
    size = sum(f.stat().st_size for f in files) / 1024 / 1024
    return df.count(), len(files), size


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    spark = spark_session()
    spark.sparkContext.setLogLevel("ERROR")
    print(f"Spark {spark.version}  ->  {OUT}")

    fact = enriched_fact(spark).cache()
    print(f"  fact rows: {fact.count():,}")

    marts = {
        "mart_revenue_daily": mart_revenue_daily(fact),
        "mart_delivery_performance": mart_delivery_performance(fact),
        "mart_customer_cohorts": mart_customer_cohorts(fact),
    }

    print(f"\n  {'mart':30} {'rows':>9} {'files':>7} {'MB':>8}")
    print("  " + "-" * 56)
    for name, df in marts.items():
        rows, files, size = write_mart(df, name)
        print(f"  {name:30} {rows:>9,} {files:>7} {size:>8.2f}")

    print("\n  partition layout (mart_revenue_daily):")
    for p in sorted((OUT / "mart_revenue_daily").glob("year=*/month=*"))[:4]:
        print(f"    {p.relative_to(OUT)}")

    fact.unpersist()
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
