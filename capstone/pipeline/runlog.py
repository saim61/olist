"""Print the pipeline run history from meta.pipeline_runs."""
from __future__ import annotations

import sys

from .db import connect


def main() -> int:
    with connect(autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("""
            select run_id, step, status,
                   coalesce(rows_in, 0), coalesce(rows_out, 0),
                   coalesce(rows_rejected, 0),
                   round(extract(epoch from (ended_at - started_at))::numeric, 2),
                   left(coalesce(message, ''), 46)
            from meta.pipeline_runs
            order by started_at desc
            limit 40
        """)
        rows = cur.fetchall()

    print(f"{'run_id':>12} {'step':24} {'status':8} {'in':>9} {'out':>9} "
          f"{'rej':>4} {'secs':>7}  message")
    print("-" * 104)
    for r in rows:
        print(f"{r[0]:>12} {r[1]:24} {r[2]:8} {r[3]:>9,} {r[4]:>9,} "
              f"{r[5]:>4} {r[6]:>7}  {r[7]}")

    with connect(autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("""
            select status, count(*) from meta.pipeline_runs group by 1 order by 1""")
        print("\ntotals: " + "  ".join(f"{s}={n}" for s, n in cur.fetchall()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
