"""
The Backtest page's jobs (backtest_jobs table) and their cTrader report files.

Report files are ~0.8 MB each, so they live in webui_data/backtests/<id>.json (git-ignored) and only
the KPI summary goes into the row. Rows are dicts with the JSON columns already decoded.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.db import get_db_connection

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "webui_data" / "backtests"
ACTIVE = ("queued", "running")
JSON_COLUMNS = ("overrides", "params", "summary")
COLUMNS = (
    "bot_name", "strategy", "symbol", "period", "algo", "algo_sha", "algo_build_time",
    "ctid_email", "account", "pwd_file", "start_date", "end_date", "data_mode", "spread_pips", "balance",
    "overrides", "params", "note", "status", "phase", "progress", "error", "summary",
    "created_at", "started_at", "finished_at",
)


def init_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_name TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            algo TEXT NOT NULL,
            algo_sha TEXT,
            algo_build_time TEXT,
            ctid_email TEXT NOT NULL,        -- not `ctid`: PostgreSQL reserves it for a system column
            account TEXT NOT NULL,
            pwd_file TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            data_mode TEXT NOT NULL,
            spread_pips DOUBLE PRECISION,
            balance DOUBLE PRECISION NOT NULL,
            overrides TEXT NOT NULL,
            params TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL,
            phase TEXT,
            progress DOUBLE PRECISION,
            error TEXT,
            summary TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_backtest_jobs_status ON backtest_jobs(status)")


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect(db_target=None):
    conn = get_db_connection(db_target)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _check(fields: Dict) -> None:
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"unknown backtest_jobs columns: {sorted(unknown)}")


def _encode(fields: Dict) -> Dict:
    return {k: (json.dumps(v) if k in JSON_COLUMNS and v is not None else v) for k, v in fields.items()}


def _decode(row) -> Optional[Dict]:
    if row is None:
        return None
    job = dict(row)
    for key in JSON_COLUMNS:
        if job.get(key):
            job[key] = json.loads(job[key])
    return job


def create_job(conn, fields: Dict) -> int:
    _check(fields)
    data = _encode({"status": "queued", "created_at": now_text(), **fields})
    columns = list(data)
    cur = conn.execute(
        f"INSERT INTO backtest_jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) RETURNING id",
        tuple(data[c] for c in columns))
    return int(cur.fetchone()[0])


def get_job(conn, job_id: int) -> Optional[Dict]:
    return _decode(conn.execute("SELECT * FROM backtest_jobs WHERE id = ?", (int(job_id),)).fetchone())


def list_jobs(conn, limit: int = 200) -> List[Dict]:
    rows = conn.execute("SELECT * FROM backtest_jobs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [_decode(r) for r in rows]


def next_queued(conn) -> Optional[Dict]:
    return _decode(conn.execute(
        "SELECT * FROM backtest_jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone())


def jobs_with_status(conn, status: str) -> List[Dict]:
    rows = conn.execute("SELECT * FROM backtest_jobs WHERE status = ? ORDER BY id", (status,)).fetchall()
    return [_decode(r) for r in rows]


def update_job(conn, job_id: int, expect_status: Optional[str] = None, **fields) -> bool:
    """Set `fields`; with expect_status, only while the row still has that status (the worker and the
    cancel button race on the same rows). Returns whether a row changed."""
    _check(fields)
    data = _encode(fields)
    sql = f"UPDATE backtest_jobs SET {', '.join(f'{c} = ?' for c in data)} WHERE id = ?"
    params = [*data.values(), int(job_id)]
    if expect_status is not None:
        sql += " AND status = ?"
        params.append(expect_status)
    return conn.execute(sql, tuple(params)).rowcount > 0


def delete_job(conn, job_id: int) -> None:
    conn.execute("DELETE FROM backtest_jobs WHERE id = ?", (int(job_id),))


def queue_position(conn, job_id: int) -> int:
    """1 = starts as soon as the worker looks; each running or older queued job adds one."""
    row = conn.execute(
        "SELECT COUNT(*) FROM backtest_jobs WHERE status = 'running' OR (status = 'queued' AND id < ?)",
        (int(job_id),)).fetchone()
    return int(row[0]) + 1


def report_path(job_id: int, report_dir: Optional[Path] = None) -> Path:
    return Path(report_dir or REPORT_DIR) / f"{int(job_id)}.json"


def write_report(job_id: int, data: bytes, report_dir: Optional[Path] = None) -> Path:
    path = report_path(job_id, report_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def read_report(job_id: int, report_dir: Optional[Path] = None) -> Optional[Dict]:
    path = report_path(job_id, report_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def delete_report(job_id: int, report_dir: Optional[Path] = None) -> None:
    report_path(job_id, report_dir).unlink(missing_ok=True)
