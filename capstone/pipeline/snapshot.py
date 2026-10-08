"""One-line warehouse snapshot, used by the live demo to show the gate held."""
from __future__ import annotations

import sys

from .db import connect


def main() -> int:
    with connect(autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("""
            select (select count(*) from warehouse.fact_order_items),
                   (select coalesce(sum(price), 0) from warehouse.fact_order_items),
                   (select count(*) from warehouse.dim_customer),
                   (select count(*) from staging.order_items)
        """)
        fact, revenue, dim, stg = cur.fetchone()
    print(f"   fact={fact:,}  revenue={revenue:,.2f}  "
          f"dim_customer={dim:,}  staging_items={stg:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
