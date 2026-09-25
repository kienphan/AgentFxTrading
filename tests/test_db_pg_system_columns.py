"""A column named like a PostgreSQL system column (2026-09-25).

backtest_jobs had a `ctid` column. PostgreSQL reserves ctid (and the names below) for system
columns, so CREATE TABLE failed with "column name "ctid" conflicts with a system column name",
_init_db raised, and agentfx.service could not start. SQLite accepts these names, so the tests,
which run on SQLite, never saw it. This checks every table the app creates.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_db_connection  # noqa: E402

POSTGRES_SYSTEM_COLUMNS = {"tableoid", "xmin", "cmin", "xmax", "cmax", "ctid", "oid"}


def test_no_app_table_has_a_postgres_system_column_name():
    from app.server import app  # noqa: F401  (initialises the app DB and creates every table)
    conn = get_db_connection()
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        clashes = {
            f"{table}.{column[1]}"
            for table in tables
            for column in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if column[1].lower() in POSTGRES_SYSTEM_COLUMNS
        }
    finally:
        conn.close()
    assert "backtest_jobs" in tables
    assert clashes == set()
