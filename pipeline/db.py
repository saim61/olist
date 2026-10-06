"""Connection handling, run logging, and watermark state."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EPOCH = datetime(1970, 1, 1)


def dsn() -> dict:
    return dict(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5433"),
        user=os.getenv("POSTGRES_USER", "olist"),
        password=os.getenv("POSTGRES_PASSWORD", "olist"),
        dbname=os.getenv("POSTGRES_DB", "olist"),
    )


@contextmanager
def connect():
    """Transactional connection: commits on success, rolls back on error."""
    conn = psycopg2.connect(**dsn())
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Watermarks
# ----------------------------------------------------------------------

def get_watermark(cur, table: str) -> datetime:
    cur.execute(
        "select watermark_value from meta.watermarks where table_name = %s", (table,)
    )
    row = cur.fetchone()
    return row[0] if row else EPOCH


def set_watermark(cur, table: str, value: datetime) -> None:
    cur.execute(
        """
        insert into meta.watermarks (table_name, watermark_value, updated_at)
        values (%s, %s, now())
        on conflict (table_name)
        do update set watermark_value = excluded.watermark_value,
                      updated_at      = now()
        """,
        (table, value),
    )


def reset_watermarks(cur) -> None:
    cur.execute("delete from meta.watermarks")


# ----------------------------------------------------------------------
# Run logging
# ----------------------------------------------------------------------

class StepFailed(Exception):
    """Raised when a step fails a quality check. Aborts the run."""


class RunLogger:
    """Writes one meta.pipeline_runs row per step."""

    def __init__(self, conn, run_id: int | None = None):
        self.conn = conn
        self.run_id = run_id or int(time.time())

    @contextmanager
    def step(self, name: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            insert into meta.pipeline_runs (run_id, step, started_at, status)
            values (%s, %s, now(), 'running')
            on conflict (run_id, step) do update
                set started_at = now(), status = 'running',
                    ended_at = null, message = null
            """,
            (self.run_id, name),
        )
        self.conn.commit()

        counters = {"rows_in": None, "rows_out": None, "rows_rejected": 0}
        t0 = time.perf_counter()
        try:
            yield counters
        except Exception as exc:
            cur.execute(
                """
                update meta.pipeline_runs
                set ended_at = now(), status = 'failed', message = %s
                where run_id = %s and step = %s
                """,
                (str(exc)[:1000], self.run_id, name),
            )
            self.conn.commit()
            print(f"  {name:22} FAILED  {exc}")
            raise
        else:
            cur.execute(
                """
                update meta.pipeline_runs
                set ended_at = now(), status = 'success',
                    rows_in = %s, rows_out = %s, rows_rejected = %s
                where run_id = %s and step = %s
                """,
                (counters["rows_in"], counters["rows_out"],
                 counters["rows_rejected"], self.run_id, name),
            )
            self.conn.commit()
            secs = time.perf_counter() - t0
            rin = counters["rows_in"]
            rout = counters["rows_out"]
            print(
                f"  {name:22} ok    "
                f"in={rin if rin is not None else '-':>8} "
                f"out={rout if rout is not None else '-':>8} "
                f"{secs:6.2f}s"
            )
