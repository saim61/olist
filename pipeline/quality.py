"""Data quality gates. A failure aborts the run; it does not warn.

The failure mode being designed against is silently loading bad data,
because that is the one nobody notices for weeks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .db import StepFailed


@dataclass
class Check:
    name: str
    phase: str          # which layer it guards
    sql: str
    passes: Callable[[int], bool]
    detail: str


CHECKS: list[Check] = [
    # ---------------- staging ----------------
    Check(
        "staging.orders not empty", "staging",
        "select count(*) from staging.orders",
        lambda v: v > 0,
        "staging.orders has no rows; the extract produced nothing",
    ),
    Check(
        "staging.orders unique on order_id", "staging",
        "select count(*) - count(distinct order_id) from staging.orders",
        lambda v: v == 0,
        "duplicate order_id reached staging",
    ),
    Check(
        "staging.orders purchase ts not null", "staging",
        "select count(*) from staging.orders where order_purchase_timestamp is null",
        lambda v: v == 0,
        "null purchase timestamp cannot be assigned a date_key",
    ),
    Check(
        "staging.order_items prices non-negative", "staging",
        "select count(*) from staging.order_items where price < 0 or freight_value < 0",
        lambda v: v == 0,
        "negative money",
    ),
    Check(
        "staging.order_items orders exist", "staging",
        """select count(*) from staging.order_items i
           where not exists (
               select 1 from staging.orders o where o.order_id = i.order_id)""",
        lambda v: v == 0,
        "order_items reference orders absent from staging",
    ),
    Check(
        "staging.customers state is 2 chars", "staging",
        "select count(*) from staging.customers where length(trim(state)) <> 2",
        lambda v: v == 0,
        "malformed state code",
    ),
    Check(
        "staging.customers zip preserved", "staging",
        "select count(*) from staging.customers where zip_code_prefix !~ '^[0-9]+$'",
        lambda v: v == 0,
        "zip prefix is not numeric; leading zeros may have been lost",
    ),

    # ---------------- warehouse ----------------
    Check(
        "fact matches staging row count", "warehouse",
        """select (select count(*) from staging.order_items)
                - (select count(*) from warehouse.fact_order_items)""",
        lambda v: v == 0,
        "fact row count differs from staging; rows were dropped or duplicated",
    ),
    Check(
        "fact grain is unique", "warehouse",
        """select count(*) - count(distinct (order_id, order_item_id))
           from warehouse.fact_order_items""",
        lambda v: v == 0,
        "duplicate (order_id, order_item_id) violates the declared grain",
    ),
    Check(
        "no fact on unknown geography", "warehouse",
        "select count(*) from warehouse.fact_order_items where geo_key = -1",
        lambda v: v == 0,
        "facts fell back to the unknown geography member",
    ),
    Check(
        "dim_customer one current row per customer", "warehouse",
        """select coalesce(max(n), 0) from (
               select count(*) n from warehouse.dim_customer
               where is_current group by customer_unique_id
           ) t""",
        lambda v: v <= 1,
        "SCD2 invariant broken: more than one current row",
    ),
    Check(
        "dim_customer validity ranges ordered", "warehouse",
        "select count(*) from warehouse.dim_customer where valid_from >= valid_to",
        lambda v: v == 0,
        "valid_from is not before valid_to",
    ),
    Check(
        "fact revenue is positive", "warehouse",
        "select count(*) from warehouse.fact_order_items where price < 0",
        lambda v: v == 0,
        "negative price in the fact table",
    ),
]


def run(cur, log, phase: str) -> None:
    """Run every check for a phase. Raises StepFailed on the first violation."""
    checks = [c for c in CHECKS if c.phase == phase]
    with log.step(f"quality.{phase}") as counters:
        failures = []
        for chk in checks:
            cur.execute(chk.sql)
            value = cur.fetchone()[0]
            ok = chk.passes(value)
            print(f"    {'PASS' if ok else 'FAIL'}  {chk.name:45} = {value}")
            if not ok:
                failures.append(f"{chk.name} (got {value}): {chk.detail}")

        counters["rows_in"] = len(checks)
        counters["rows_out"] = len(checks) - len(failures)
        counters["rows_rejected"] = len(failures)

        if failures:
            raise StepFailed(
                f"{len(failures)} of {len(checks)} {phase} checks failed: "
                + " | ".join(failures)
            )
