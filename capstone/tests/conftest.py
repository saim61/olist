"""Shared fixtures. Tests run against the live warehouse inside the container."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline.db import connect

MARTS = Path(os.getenv("MARTS_DIR", "/app/marts"))


@pytest.fixture(scope="session")
def conn():
    with connect(autocommit=True) as c:
        yield c


@pytest.fixture()
def cur(conn):
    c = conn.cursor()
    yield c
    c.close()


@pytest.fixture(scope="session")
def marts_dir() -> Path:
    if not MARTS.exists():
        pytest.skip(f"{MARTS} not built -- run `make run` first")
    return MARTS


def scalar(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]
