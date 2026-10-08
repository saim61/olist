"""Warehouse, quality-gate, and mart assertions."""
from __future__ import annotations

import pandas as pd
import pytest

from pipeline.quality import CHECKS, evaluate

from .conftest import scalar


# ---------------------------------------------------------------- quality

@pytest.mark.parametrize("phase", ["staging", "warehouse"])
def test_all_quality_checks_pass(cur, phase):
    failures = [f"{c.name} = {v} ({c.detail})"
                for c, v, ok in evaluate(cur, phase) if not ok]
    assert not failures, "\n".join(failures)


def test_check_count_meets_requirement():
    # The capstone asks for at least 8.
    assert len(CHECKS) >= 8, f"only {len(CHECKS)} checks defined"


# ---------------------------------------------------------------- warehouse

def test_fact_grain_is_unique(cur):
    dupes = scalar(cur, """
        select count(*) - count(distinct (order_id, order_item_id))
        from warehouse.fact_order_items""")
    assert dupes == 0


def test_fact_matches_staging(cur):
    diff = scalar(cur, """
        select (select count(*) from staging.order_items)
             - (select count(*) from warehouse.fact_order_items)""")
    assert diff == 0


def test_every_fact_resolves_all_dimensions(cur):
    orphans = scalar(cur, """
        select count(*) from warehouse.fact_order_items f
        where not exists (select 1 from warehouse.dim_customer d
                          where d.customer_key = f.customer_key)
           or not exists (select 1 from warehouse.dim_product p
                          where p.product_key = f.product_key)
           or not exists (select 1 from warehouse.dim_seller s
                          where s.seller_key = f.seller_key)
           or not exists (select 1 from warehouse.dim_geography g
                          where g.geo_key = f.geo_key)
           or not exists (select 1 from warehouse.dim_date dt
                          where dt.date_key = f.date_key)""")
    assert orphans == 0


def test_scd2_single_current_row_per_customer(cur):
    worst = scalar(cur, """
        select coalesce(max(n), 0) from (
            select count(*) n from warehouse.dim_customer
            where is_current group by customer_unique_id) t""")
    assert worst == 1


def test_scd2_validity_windows_are_ordered(cur):
    bad = scalar(cur,
                 "select count(*) from warehouse.dim_customer "
                 "where valid_from >= valid_to")
    assert bad == 0


def test_dim_date_is_contiguous(cur):
    gaps = scalar(cur, """
        select count(*) from (
            select full_date,
                   lead(full_date) over (order by full_date) as nxt
            from warehouse.dim_date) t
        where nxt is not null and nxt <> full_date + 1""")
    assert gaps == 0


def test_zip_leading_zeros_survived(cur):
    # The classic silent corruption: integer typing eats the leading zero.
    with_zero = scalar(cur, "select count(*) from staging.customers "
                            "where zip_code_prefix like '0%'")
    assert with_zero > 0


# ---------------------------------------------------------------- observability

def test_pipeline_runs_recorded(cur):
    steps = scalar(cur, "select count(*) from meta.pipeline_runs")
    assert steps > 0


def test_no_step_left_running(cur):
    stuck = scalar(cur,
                   "select count(*) from meta.pipeline_runs where status = 'running'")
    assert stuck == 0


def test_watermark_advanced(cur):
    wm = scalar(cur, "select watermark_value from meta.watermarks "
                     "where table_name = 'raw.orders'")
    assert wm is not None and wm.year >= 2018


# ---------------------------------------------------------------- marts

@pytest.mark.parametrize("name", [
    "mart_revenue_daily", "mart_delivery_performance", "mart_customer_cohorts"])
def test_mart_exists_and_is_partitioned(marts_dir, name):
    path = marts_dir / name
    assert path.exists(), f"{name} was not written"
    partitions = list(path.glob("year=*/month=*"))
    assert partitions, f"{name} is not partitioned by year/month"


def test_mart_revenue_reconciles_with_warehouse(cur, marts_dir):
    """The mart is derived, so its total must match its source."""
    mart_total = float(
        pd.read_parquet(marts_dir / "mart_revenue_daily")["revenue"].sum())
    wh_total = float(scalar(cur, """
        select coalesce(sum(price), 0) from warehouse.fact_order_items
        where order_status <> 'canceled'"""))
    assert abs(mart_total - wh_total) < 1.0, (
        f"mart {mart_total:,.2f} vs warehouse {wh_total:,.2f}")


def test_mart_money_is_not_decimal(marts_dir):
    """Decimal columns surface in pandas as objects and break consumers."""
    rev = pd.read_parquet(marts_dir / "mart_revenue_daily")
    assert rev["revenue"].dtype.kind == "f"


def test_cohort_retention_bounded(marts_dir):
    coh = pd.read_parquet(marts_dir / "mart_customer_cohorts")
    assert coh["retention_pct"].between(0, 100).all()
    base = coh[coh["month_offset"] == 0]
    assert (base["retention_pct"] == 100).all(), "month 0 must be 100%"


def test_late_pct_bounded(marts_dir):
    dlv = pd.read_parquet(marts_dir / "mart_delivery_performance")
    assert dlv["late_pct"].between(0, 100).all()
    assert (dlv["late_items"] <= dlv["delivered_items"]).all()
