"""Running the pipeline again on unchanged data must change nothing.

This is the property that makes a pipeline safe to retry after a failure.
It is tested by actually re-running it, not by inspection.
"""
from __future__ import annotations

import subprocess
import sys

from .conftest import scalar

TABLES = [
    "staging.orders",
    "staging.order_items",
    "staging.customers",
    "warehouse.dim_customer",
    "warehouse.dim_geography",
    "warehouse.fact_order_items",
]


def snapshot(cur) -> dict[str, float]:
    snap = {t: scalar(cur, f"select count(*) from {t}") for t in TABLES}
    snap["revenue"] = float(
        scalar(cur, "select coalesce(sum(price), 0) from warehouse.fact_order_items"))
    return snap


def rerun() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pipeline.run"],
        capture_output=True, text=True, cwd="/app",
    )


def test_rerun_changes_nothing(cur):
    before = snapshot(cur)

    result = rerun()
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]

    after = snapshot(cur)
    drift = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not drift, f"pipeline is not idempotent: {drift}"


def test_second_rerun_also_changes_nothing(cur):
    """Twice, because some idempotency bugs only appear on a later run."""
    before = snapshot(cur)
    for _ in range(2):
        result = rerun()
        assert result.returncode == 0, result.stderr[-2000:]
    assert snapshot(cur) == before


def test_extract_finds_nothing_new(cur):
    """The watermark sits at the end of the data, so an extract yields zero."""
    result = rerun()
    assert result.returncode == 0
    assert "no new source rows" in result.stdout
