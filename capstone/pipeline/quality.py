"""Data quality gates. A violation aborts the run; it does not warn.

The failure being designed against is silently loading bad data, which is
the one nobody notices for weeks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .db import StepFailed


@dataclass
class Check:
    name: str
    phase: str
    sql: str
    passes: Callable[[int], bool]
    detail: str


CHECKS: list[Check] = [
    # ---------------- staging ----------------
    Check("staging.orders not empty", "staging",
          "select count(*) from staging.orders",
          lambda v: v > 0, "extract produced nothing"),
    Check("staging.orders unique on order_id", "staging",
          "select count(*) - count(distinct order_id) from staging.orders",
          lambda v: v == 0, "duplicate order_id reached staging"),
    Check("staging.orders purchase ts present", "staging",
          "select count(*) from staging.orders where order_purchase_timestamp is null",
          lambda v: v == 0, "null timestamp cannot map to a date_key"),
    Check("staging.order_items money non-negative", "staging",
          "select count(*) from staging.order_items where price < 0 or freight_value < 0",
          lambda v: v == 0, "negative money"),
    Check("staging.order_items parents exist", "staging",
          """select count(*) from staging.order_items i where not exists
             (select 1 from staging.orders o where o.order_id = i.order_id)""",
          lambda v: v == 0, "orphan order_items"),
    Check("staging.customers state well formed", "staging",
          "select count(*) from staging.customers where length(trim(state)) <> 2",
          lambda v: v == 0, "malformed state code"),
    Check("staging.customers zip numeric", "staging",
          "select count(*) from staging.customers where zip_code_prefix !~ '^[0-9]+$'",
          lambda v: v == 0, "zip is non-numeric"),
    Check("staging.customers zip leading zeros kept", "staging",
          "select count(*) from staging.customers where zip_code_prefix like '0%'",
          lambda v: v > 0, "no zip starts with 0 -- integer coercion destroyed them"),

    # ---------------- warehouse ----------------
    Check("fact row count matches staging", "warehouse",
          """select (select count(*) from staging.order_items)
                  - (select count(*) from warehouse.fact_order_items)""",
          lambda v: v == 0, "rows dropped or duplicated during load"),
    Check("fact grain is unique", "warehouse",
          """select count(*) - count(distinct (order_id, order_item_id))
             from warehouse.fact_order_items""",
          lambda v: v == 0, "duplicate grain key"),
    Check("fact has no orphan dimension keys", "warehouse",
          """select count(*) from warehouse.fact_order_items f
             where not exists (select 1 from warehouse.dim_customer d
                               where d.customer_key = f.customer_key)""",
          lambda v: v == 0, "fact references a missing dimension row"),
    Check("dim_customer one current row per customer", "warehouse",
          """select coalesce(max(n), 0) from (
                 select count(*) n from warehouse.dim_customer
                 where is_current group by customer_unique_id) t""",
          lambda v: v <= 1, "SCD2 invariant broken"),
    Check("dim_customer validity ranges ordered", "warehouse",
          "select count(*) from warehouse.dim_customer where valid_from >= valid_to",
          lambda v: v == 0, "valid_from is not before valid_to"),
    Check("fact prices non-negative", "warehouse",
          "select count(*) from warehouse.fact_order_items where price < 0",
          lambda v: v == 0, "negative price in the fact table"),
    Check("fact revenue is plausible", "warehouse",
          "select coalesce(sum(price), 0)::bigint from warehouse.fact_order_items",
          lambda v: v > 0, "total revenue is zero or negative"),
]


def evaluate(cur, phase: str) -> list[tuple[Check, int, bool]]:
    out = []
    for chk in (c for c in CHECKS if c.phase == phase):
        cur.execute(chk.sql)
        value = cur.fetchone()[0]
        out.append((chk, value, chk.passes(value)))
    return out


def run(cur, log, phase: str) -> None:
    with log.step(f"quality.{phase}") as counters:
        results = evaluate(cur, phase)
        failures = []
        for chk, value, ok in results:
            print(f"    {'PASS' if ok else 'FAIL'}  {chk.name:45} = {value}")
            if not ok:
                failures.append(f"{chk.name} (got {value}): {chk.detail}")

        counters["rows_in"] = len(results)
        counters["rows_out"] = len(results) - len(failures)
        counters["rows_rejected"] = len(failures)

        if failures:
            raise StepFailed(
                f"{len(failures)} of {len(results)} {phase} checks failed: "
                + " | ".join(failures))
