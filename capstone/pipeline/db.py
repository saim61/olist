"""Connections, watermark state, and run logging."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
EPOCH = datetime(1970, 1, 1)


def dsn() -> dict:
    return dict(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        user=os.getenv("POSTGRES_USER", "olist"),
        password=os.getenv("POSTGRES_PASSWORD", "olist"),
        dbname=os.getenv("POSTGRES_DB", "olist_capstone"),
    )


@contextmanager
def connect(autocommit: bool = False):
    """Commits on success, rolls back on any exception."""
    conn = psycopg2.connect(**dsn())
    conn.autocommit = autocommit
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def get_watermark(cur, table: str) -> datetime:
    cur.execute("select watermark_value from meta.watermarks where table_name = %s",
                (table,))
    row = cur.fetchone()
    return row[0] if row else EPOCH


def set_watermark(cur, table: str, value: datetime) -> None:
    cur.execute(
        """
        insert into meta.watermarks (table_name, watermark_value, updated_at)
        values (%s, %s, now())
        on conflict (table_name) do update
            set watermark_value = excluded.watermark_value, updated_at = now()
        """,
        (table, value),
    )


class StepFailed(Exception):
    """A quality gate rejected the data. Aborts the run."""


class RunLogger:
    """One meta.pipeline_runs row per step."""

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

        c = {"rows_in": None, "rows_out": None, "rows_rejected": 0}
        t0 = time.perf_counter()
        try:
            yield c
        except Exception as exc:
            cur.execute(
                """update meta.pipeline_runs
                   set ended_at = now(), status = 'failed', message = %s
                   where run_id = %s and step = %s""",
                (str(exc)[:1000], self.run_id, name),
            )
            self.conn.commit()
            print(f"  {name:24} FAILED  {str(exc)[:90]}")
            raise
        else:
            cur.execute(
                """update meta.pipeline_runs
                   set ended_at = now(), status = 'success',
                       rows_in = %s, rows_out = %s, rows_rejected = %s
                   where run_id = %s and step = %s""",
                (c["rows_in"], c["rows_out"], c["rows_rejected"], self.run_id, name),
            )
            self.conn.commit()
            rin = c["rows_in"] if c["rows_in"] is not None else "-"
            rout = c["rows_out"] if c["rows_out"] is not None else "-"
            print(f"  {name:24} ok   in={rin:>9} out={rout:>9} "
                  f"{time.perf_counter() - t0:6.2f}s")
