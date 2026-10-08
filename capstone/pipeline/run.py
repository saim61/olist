"""Orchestrator.

    python -m pipeline.run --full       batch 1, batch 2, then marts
    python -m pipeline.run              one incremental cycle
    python -m pipeline.run --max-ts X   extract only orders before X
    python -m pipeline.run --marts      rebuild the Spark marts only

One ELT cycle is:
    extract -> transform -> quality(staging) -> load -> quality(warehouse)

The quality gates sit between layers, so bad data is caught in staging and
never reaches the warehouse.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from . import extract, load, quality, transform
from .db import ROOT, RunLogger, StepFailed, connect

CUTOFF = os.getenv("BATCH_CUTOFF", "2018-01-01")


def elt_cycle(label: str, max_ts: str | None = None) -> int:
    """One extract-to-warehouse pass. Returns rows extracted."""
    run_id = int(time.time() * 1000) % 2_000_000_000
    print(f"\n{'=' * 68}\n{label}   run_id={run_id}"
          + (f"   max_ts={max_ts}" if max_ts else "")
          + f"\n{'=' * 68}")

    with connect() as conn:
        cur = conn.cursor()
        log = RunLogger(conn, run_id)

        extracted = extract.run(cur, log, run_id, max_ts)
        if extracted == 0:
            print("  no new source rows -- downstream layers unchanged")

        transform.run(cur, log)
        quality.run(cur, log, "staging")
        load.run(cur, log)
        quality.run(cur, log, "warehouse")
    return extracted


def build_marts() -> int:
    print(f"\n{'=' * 68}\nSPARK MARTS\n{'=' * 68}")
    return subprocess.call(
        [sys.executable, str(ROOT / "spark" / "build_marts.py")], cwd=str(ROOT))


def summary() -> None:
    with connect() as conn:
        cur = conn.cursor()
        print("\n  layer counts")
        for t in ("source.orders", "raw.orders", "staging.orders",
                  "staging.order_items", "warehouse.dim_customer",
                  "warehouse.fact_order_items"):
            cur.execute(f"select count(*) from {t}")
            print(f"    {t:32} {cur.fetchone()[0]:>9,}")

        cur.execute("select watermark_value from meta.watermarks "
                    "where table_name = 'raw.orders'")
        row = cur.fetchone()
        print(f"\n  watermark: {row[0] if row else '(none)'}")

        cur.execute("select coalesce(sum(price), 0) from warehouse.fact_order_items")
        print(f"  fact revenue: {cur.fetchone()[0]:,.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="batch 1, then batch 2, then marts")
    ap.add_argument("--max-ts", default=None, help="extract only orders before this")
    ap.add_argument("--marts", action="store_true", help="rebuild marts only")
    args = ap.parse_args()

    try:
        if args.marts:
            return build_marts()

        if args.full:
            elt_cycle("BATCH 1 -- orders before the cutoff", CUTOFF)
            elt_cycle("BATCH 2 -- the rest arrives")
            if build_marts() != 0:
                print("\nmart build failed", file=sys.stderr)
                return 3
        else:
            elt_cycle("INCREMENTAL CYCLE", args.max_ts)

        summary()
    except StepFailed as exc:
        print(f"\nRUN FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nRUN ERRORED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("\nrun complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
