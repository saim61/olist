"""The five business questions, answered from the Parquet marts.

    python -m pipeline.serve

The head of operations asks: which categories and states drive revenue,
how is it trending, and where are deliveries late?
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

MARTS = Path(os.getenv("MARTS_DIR", "/app/marts"))
pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def banner(n: int, question: str) -> None:
    print(f"\n{'=' * 72}\nQ{n}. {question}\n{'=' * 72}")


def load(name: str) -> pd.DataFrame:
    """Read a mart. Money columns are written as double by the Spark job,
    so nothing needs coercing here."""
    return pd.read_parquet(MARTS / name)


def q1(rev: pd.DataFrame) -> None:
    banner(1, "Which product categories drive the most revenue?")
    total = rev["revenue"].sum()
    out = (rev.groupby("category", as_index=False)
              .agg(revenue=("revenue", "sum"), orders=("orders", "sum"))
              .sort_values("revenue", ascending=False)
              .head(10))
    out["pct_of_total"] = (100 * out["revenue"] / total).round(2)
    out["revenue"] = out["revenue"].round(2)
    print(out.to_string(index=False))


def q2(rev: pd.DataFrame) -> None:
    banner(2, "Which states drive the most revenue?")
    total = rev["revenue"].sum()
    out = (rev.groupby("customer_state", as_index=False)
              .agg(revenue=("revenue", "sum"), orders=("orders", "sum"))
              .sort_values("revenue", ascending=False)
              .head(10))
    out["pct_of_total"] = (100 * out["revenue"] / total).round(2)
    out["revenue"] = out["revenue"].round(2)
    print(out.to_string(index=False))


def q3(rev: pd.DataFrame) -> None:
    banner(3, "How is revenue trending month over month?")
    monthly = (rev.assign(month=pd.to_datetime(rev["order_date"]).dt.to_period("M"))
                  .groupby("month", as_index=False)
                  .agg(revenue=("revenue", "sum"), orders=("orders", "sum")))
    monthly["prev"] = monthly["revenue"].shift(1)
    monthly["growth_pct"] = (100 * (monthly["revenue"] - monthly["prev"])
                             / monthly["prev"]).round(2)
    monthly["revenue"] = monthly["revenue"].round(2)
    monthly["prev"] = monthly["prev"].round(2)
    print(monthly.tail(12).to_string(index=False))
    print("\n  Note: the dataset stops mid-September 2018, so the final month's"
          "\n  decline is a collection artifact, not a business event.")


def q4(dlv: pd.DataFrame) -> None:
    banner(4, "Where are deliveries late?")
    out = (dlv.groupby("customer_state", as_index=False)
              .agg(items=("delivered_items", "sum"),
                   late=("late_items", "sum"),
                   avg_days=("avg_delivery_days", "mean")))
    out["late_pct"] = (100 * out["late"] / out["items"]).round(2)
    out["avg_days"] = out["avg_days"].round(1)
    print(out.sort_values("late_pct", ascending=False).head(10).to_string(index=False))


def q5(dlv: pd.DataFrame, coh: pd.DataFrame) -> None:
    banner(5, "Which sellers are worst for late delivery, and do customers return?")
    sellers = (dlv.groupby("seller_id", as_index=False)
                  .agg(items=("delivered_items", "sum"), late=("late_items", "sum")))
    sellers = sellers[sellers["items"] >= 100]
    sellers["late_pct"] = (100 * sellers["late"] / sellers["items"]).round(2)
    print("  worst sellers (>= 100 delivered items):")
    print(sellers.sort_values("late_pct", ascending=False)
                 .head(5).to_string(index=False))

    print("\n  cohort retention (month 1 after first order):")
    m1 = (coh[coh["month_offset"] == 1]
          .sort_values("cohort_month")
          [["cohort_month", "cohort_size", "customers", "retention_pct"]])
    print(m1.tail(8).to_string(index=False))
    if len(m1):
        overall = 100 * m1["customers"].sum() / m1["cohort_size"].sum()
        print(f"\n  overall month-1 retention: {overall:.2f}%"
              "\n  Low, but normal for a marketplace selling furniture and watches:"
              "\n  nobody rebuys a bed frame monthly.")


def main() -> int:
    if not MARTS.exists():
        print(f"{MARTS} missing -- run `make run` first", file=sys.stderr)
        return 1

    rev = load("mart_revenue_daily")
    dlv = load("mart_delivery_performance")
    coh = load("mart_customer_cohorts")

    print(f"marts loaded: revenue={len(rev):,}  delivery={len(dlv):,}  "
          f"cohorts={len(coh):,}")

    q1(rev)
    q2(rev)
    q3(rev)
    q4(dlv)
    q5(dlv, coh)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
