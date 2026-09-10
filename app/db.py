"""
Database abstraction layer for AgentFxTrading.
Supports PostgreSQL (production) with automatic fallback to SQLite (local / testing).
Provides a unified interface compatible with sqlite3.Row and conn.execute().
"""

import os
import re
import sqlite3
import logging
from pathlib import Path
from typing import Optional, Union, Any, List, Dict, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Default Database URL (can be overridden via environment)
DEFAULT_PG_URL = "postgresql://agentfx:kaz%40112358.@127.0.0.1:5432/agentfx"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "portfolio.db"

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


def _adapt_query_for_pg(sql: str) -> str:
    """
    Adapts SQLite query dialect to PostgreSQL:
    - Replaces parameter placeholders '?' with '%s' (when not inside string literals).
    - Replaces SQLite datetime('now') with CURRENT_TIMESTAMP.
    - Replaces AUTOINCREMENT with SERIAL.
    """
    # Ignore SQLite PRAGMAs
    stripped = sql.strip()
    if stripped.upper().startswith("PRAGMA "):
        return "SELECT 1"

    # Replace datetime('now')
    adapted = re.sub(r"datetime\s*\(\s*['\"]now['\"]\s*\)", "CURRENT_TIMESTAMP", sql, flags=re.IGNORECASE)

    # Replace INTEGER PRIMARY KEY AUTOINCREMENT
    adapted = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", adapted, flags=re.IGNORECASE)

    # Replace CREATE VIEW IF NOT EXISTS with CREATE OR REPLACE VIEW
    adapted = re.sub(r"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS", "CREATE OR REPLACE VIEW", adapted, flags=re.IGNORECASE)

    # Replace ROUND(expr, N) with ROUND((expr)::numeric, N) for PostgreSQL
    adapted = re.sub(r"ROUND\s*\(\s*([^,]+?)\s*,\s*(\d+)\s*\)", r"ROUND((\1)::numeric, \2)", adapted, flags=re.IGNORECASE)

    # Replace '?' with '%s' outside single/double quotes
    parts = []
    in_quote = None
    i = 0
    while i < len(adapted):
        char = adapted[i]
        if in_quote:
            parts.append(char)
            if char == in_quote:
                # Check for escaped quote (e.g. '')
                if i + 1 < len(adapted) and adapted[i + 1] == in_quote:
                    parts.append(adapted[i + 1])
                    i += 1
                else:
                    in_quote = None
        else:
            if char in ("'", '"'):
                in_quote = char
                parts.append(char)
            elif char == "?":
                parts.append("%s")
            else:
                parts.append(char)
        i += 1

    return "".join(parts)
class RowWrapper:
    """Wraps a psycopg2 DictRow to behave exactly like sqlite3.Row (index and key access, dict conversion)."""
    __slots__ = ("_dict", "_tuple")

    def __init__(self, raw_row):
        self._tuple = tuple(raw_row)
        d = {}
        if hasattr(raw_row, "keys"):
            for idx, k in enumerate(raw_row.keys()):
                v = raw_row[idx]
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                    v = float(v)
                d[k] = v
        self._dict = d

    def __getitem__(self, key):
        if isinstance(key, int):
            v = self._tuple[key]
            if hasattr(v, "isoformat"):
                return v.isoformat()
            if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                return float(v)
            return v
        return self._dict[key]

    def get(self, key, default=None):
        return self._dict.get(key, default)

    def keys(self):
        return self._dict.keys()

    def values(self):
        return self._dict.values()

    def items(self):
        return self._dict.items()

    def __iter__(self):
        return iter(self._tuple)

    def __len__(self):
        return len(self._tuple)

    def __repr__(self):
        return f"<Row {self._dict}>"


class PostgresCursorWrapper:
    """Wraps psycopg2 cursor to provide sqlite3-compatible execute & row behavior."""
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql: str, params: Optional[Union[Tuple, List, Dict]] = None):
        adapted_sql = _adapt_query_for_pg(sql)
        try:
            if params is not None:
                if isinstance(params, (list, tuple)):
                    self._cursor.execute(adapted_sql, tuple(params))
                else:
                    self._cursor.execute(adapted_sql, params)
            else:
                self._cursor.execute(adapted_sql)
            return self
        except Exception:
            try:
                self._cursor.connection.rollback()
            except Exception:
                pass
            raise
    def fetchone(self) -> Optional[RowWrapper]:
        row = self._cursor.fetchone()
        return RowWrapper(row) if row is not None else None

    def fetchall(self) -> List[RowWrapper]:
        rows = self._cursor.fetchall()
        return [RowWrapper(r) for r in rows]

    def fetchmany(self, size=None) -> List[RowWrapper]:
        rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
        return [RowWrapper(r) for r in rows]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __iter__(self):
        for row in self._cursor:
            yield RowWrapper(row)


class PostgresConnectionWrapper:
    """Wraps psycopg2 connection to mimic sqlite3.Connection."""
    def __init__(self, pg_conn):
        self._conn = pg_conn
        self.row_factory = None  # mimic sqlite3 attribute

    def cursor(self):
        raw_cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        return PostgresCursorWrapper(raw_cur)

    def execute(self, sql: str, params: Optional[Union[Tuple, List, Dict]] = None) -> PostgresCursorWrapper:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


def is_postgres_target(db_target: Optional[Union[str, Path]]) -> bool:
    """Determines whether to use PostgreSQL based on target or environment."""
    if db_target is not None:
        target_str = str(db_target).strip()
        if target_str.startswith(("postgresql://", "postgres://")):
            return True
        # If it specifically refers to default portfolio.db, allow PostgreSQL usage if configured
        if target_str in ("portfolio.db", str(DEFAULT_SQLITE_PATH)):
            db_url = os.getenv("DATABASE_URL", "").strip()
            if db_url.startswith(("postgresql://", "postgres://")) or HAS_PSYCOPG2:
                return True
            return False
        # If it's another file path ending with .db (e.g. temporary test db), use SQLite
        if target_str.endswith((".db", ".sqlite", ".sqlite3")) or "/" in target_str or "\\" in target_str:
            return False
    # Check env
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url.startswith(("postgresql://", "postgres://")):
        return True
    
    # If psycopg2 is available and default PG URL connects, prefer PostgreSQL
    if HAS_PSYCOPG2:
        return True

    return False


def get_db_connection(db_target: Optional[Union[str, Path]] = None, timeout: float = 30.0):
    """
    Returns an active database connection (PostgreSQL wrapper or SQLite connection).
    Matches sqlite3 interface so existing queries work unchanged.
    """
    # Test isolation: a pytest run MUST never touch the production database. When
    # AGENTFX_TEST_DB is set, the default DB target is redirected to that throwaway SQLite
    # file; explicitly-passed targets (e.g. tmp_path fixtures) are left untouched.
    test_db = os.environ.get("AGENTFX_TEST_DB", "").strip()
    if test_db and (db_target is None or str(db_target).strip() in ("portfolio.db", str(DEFAULT_SQLITE_PATH))):
        db_target = test_db

    use_pg = is_postgres_target(db_target)
    
    if use_pg and HAS_PSYCOPG2:
        pg_url = None
        if db_target and str(db_target).startswith(("postgresql://", "postgres://")):
            pg_url = str(db_target)
        else:
            pg_url = os.getenv("DATABASE_URL") or DEFAULT_PG_URL
            
        try:
            raw_conn = psycopg2.connect(pg_url, connect_timeout=int(timeout))
            raw_conn.autocommit = False
            return PostgresConnectionWrapper(raw_conn)
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed ({e}), falling back to SQLite: {DEFAULT_SQLITE_PATH}")

    # SQLite fallback / SQLite test database
    sqlite_path = db_target if db_target and not str(db_target).startswith(("postgresql://", "postgres://")) else DEFAULT_SQLITE_PATH
    conn = sqlite3.connect(str(sqlite_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn
