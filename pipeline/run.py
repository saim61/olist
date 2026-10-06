"""Orchestrator: extract -> transform -> quality(staging) -> load -> quality(warehouse).

    python -m pipeline.run --reset              rebuild from scratch
    python -m pipeline.run --max-ts 2018-01-01  simulate a partial arrival
    python -m pipeline.run                      incremental, picks up the rest

Running twice with no new source data changes nothing: every write is an
upsert keyed on a natural key.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import extract, load, quality, transform
from .db import RunLogger, StepFailed, connect, reset_watermarks


def reset(cur) -> None:
    cur.execute("truncate raw.orders, raw.order_items, raw.customers")
    cur.execute("truncate staging.orders, staging.order_items, staging.customers cascade")
    cur.execute("truncate warehouse.fact_order_items")
    cur.execute("truncate warehouse.dim_customer cascade")
    cur.execute("delete from warehouse.dim_geography where geo_key <> -1")
    reset_watermarks(cur)


def summary(cur, run_id: int) -> None:
    print("\n  layer counts")
    for table in [
        "raw.orders", "raw.order_items", "raw.customers",
        "staging.orders", "staging.order_items", "staging.customers",
        "warehouse.dim_customer", "warehouse.fact_order_items",
    ]:
        cur.execute(f"select count(*) from {table}")
        print(f"    {table:30} {cur.fetchone()[0]:>9,}")

    cur.execute(
        "select watermark_value from meta.watermarks where table_name = 'raw.orders'"
    )
    row = cur.fetchone()
    print(f"\n  watermark: {row[0] if row else '(none)'}")

    cur.execute(
        """
        select count(*) filter (where status = 'success'),
               count(*) filter (where status = 'failed')
        from meta.pipeline_runs where run_id = %s
        """,
        (run_id,),
    )
    ok, failed = cur.fetchone()
    print(f"  steps: {ok} succeeded, {failed} failed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="truncate raw/staging/warehouse and clear watermarks")
    ap.add_argument("--max-ts", type=str, default=None,
                    help="only extract orders up to this timestamp")
    args = ap.parse_args()

    max_ts = datetime.fromisoformat(args.max_ts) if args.max_ts else None
    run_id = int(time.time())

    print(f"run_id={run_id}"
          + (f"  max_ts={max_ts}" if max_ts else "")
          + ("  [RESET]" if args.reset else ""))

    try:
        with connect() as conn:
            cur = conn.cursor()
            log = RunLogger(conn, run_id)

            if args.reset:
                reset(cur)
                conn.commit()

            extracted = extract.run(cur, log, run_id, max_ts)
            if extracted == 0:
                print("  no new source rows; staging and warehouse unchanged")

            transform.run(cur, log, run_id)
            quality.run(cur, log, "staging")
            load.run(cur, log, run_id)
            quality.run(cur, log, "warehouse")

            summary(cur, run_id)
    except StepFailed as exc:
        print(f"\nRUN FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nRUN ERRORED: {exc}", file=sys.stderr)
        return 2

    print("\nrun complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
