"""backtest_jobs rows and report files. Runs against a throwaway SQLite file; production uses the same
SQL through app.db's PostgreSQL adapter (INSERT … RETURNING works on both, SQLite >= 3.35)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_store as store  # noqa: E402

FIELDS = {
    "bot_name": "cbot-demo-demo-eurusd-all-flowrsi", "strategy": "flowrsi", "symbol": "EURUSD", "period": "m15",
    "algo": "FlowRsiBot.algo", "algo_sha": "abc", "algo_build_time": "2026-09-24T16:34:43Z",
    "ctid_email": "trader@example.com", "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd",
    "start_date": "2026-07-24", "end_date": "2026-09-23", "data_mode": "ticks", "spread_pips": None,
    "balance": 10000.0, "overrides": {"TargetRiskReward": {"from": "1.5", "to": "2.0"}},
    "params": {"TargetRiskReward": "2.0"}, "note": "rr 2",
}


@pytest.fixture
def db(tmp_path):
    target = tmp_path / "backtests.db"
    with store.connect(target) as conn:
        store.init_schema(conn)
        store.init_schema(conn)          # idempotent
    return target


def test_create_and_get_round_trip(db):
    with store.connect(db) as conn:
        job_id = store.create_job(conn, FIELDS)
        job = store.get_job(conn, job_id)
    assert job["status"] == "queued" and job["created_at"]
    assert job["overrides"] == FIELDS["overrides"] and job["params"] == FIELDS["params"]
    assert job["summary"] is None and job["balance"] == 10000.0


def test_create_refuses_unknown_columns(db):
    with store.connect(db) as conn, pytest.raises(ValueError):
        store.create_job(conn, {**FIELDS, "evil": 1})


def test_lists_newest_first_and_picks_the_oldest_queued(db):
    with store.connect(db) as conn:
        ids = [store.create_job(conn, FIELDS) for _ in range(3)]
        assert [j["id"] for j in store.list_jobs(conn)] == ids[::-1]
        assert [j["id"] for j in store.jobs_with_status(conn, "queued")] == ids
        store.update_job(conn, ids[0], status="running")
        assert [j["id"] for j in store.jobs_with_status(conn, "queued")] == ids[1:]
        assert [j["id"] for j in store.jobs_with_status(conn, "running")] == [ids[0]]


def test_update_with_expected_status_is_a_guard(db):
    with store.connect(db) as conn:
        job_id = store.create_job(conn, FIELDS)
        assert store.update_job(conn, job_id, expect_status="running", status="done") is False
        assert store.get_job(conn, job_id)["status"] == "queued"
        assert store.update_job(conn, job_id, expect_status="queued", status="running", progress=12.5) is True
        job = store.get_job(conn, job_id)
    assert (job["status"], job["progress"]) == ("running", 12.5)


def test_update_encodes_summary(db):
    with store.connect(db) as conn:
        job_id = store.create_job(conn, FIELDS)
        store.update_job(conn, job_id, status="done", summary={"net_profit": -0.98})
        assert store.get_job(conn, job_id)["summary"] == {"net_profit": -0.98}


def test_queue_position_counts_only_older_queued_jobs(db):
    with store.connect(db) as conn:
        first, second, third = (store.create_job(conn, FIELDS) for _ in range(3))
        assert [store.queue_position(conn, i) for i in (first, second, third)] == [1, 2, 3]
        store.update_job(conn, first, status="running")
        assert store.queue_position(conn, second) == 1        # jobs run in parallel: running ones are not ahead
        assert (store.count_status(conn, "running"), store.count_status(conn, "queued")) == (1, 2)


def test_delete_job(db):
    with store.connect(db) as conn:
        job_id = store.create_job(conn, FIELDS)
        store.delete_job(conn, job_id)
        assert store.get_job(conn, job_id) is None


def test_report_files(tmp_path):
    reports = tmp_path / "reports"
    path = store.write_report(5, b'{"main": {}}', reports)
    assert path == reports / "5.json" and store.read_report(5, reports) == {"main": {}}
    store.delete_report(5, reports)
    assert store.read_report(5, reports) is None
    store.delete_report(5, reports)                         # already gone: no error
    (reports / "6.json").write_text("{broken")
    assert store.read_report(6, reports) is None


def test_the_app_database_has_the_table():
    from app.server import app  # noqa: F401  (initialises the portfolio DB, which creates the table)
    with store.connect() as conn:
        conn.execute("SELECT COUNT(*) FROM backtest_jobs").fetchone()
