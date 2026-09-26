"""Backtest worker: bt-<id> containers up to max_parallel at once, progress from the CLI's log, the report collected
from the exited container, timeouts, cancel, and restart reconciliation — all against a fake Docker
client and a throwaway SQLite DB."""
import io
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_store as store  # noqa: E402
from app.backtest_worker import (DEFAULT_MAX_PARALLEL, MAX_PARALLEL_CAP, TIMEOUT_S, BacktestWorker,  # noqa: E402
                                 max_parallel_from_env, parse_progress)

REPORT = (ROOT / "tests" / "fixtures" / "backtest" / "report_small.json").read_bytes()
# The fake clock sits before any real run date: a job the worker starts gets started_at = the real
# now, so its elapsed time on this clock is negative and only the timeout test (which sets
# started_at itself) can cross TIMEOUT_S.
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
JOB = {
    "bot_name": "cbot-demo-demo-eurusd-all-flowrsi", "strategy": "flowrsi", "symbol": "EURUSD", "period": "m15",
    "algo": "FlowRsiBot.algo", "algo_sha": "abc", "algo_build_time": "2026-09-24T16:34:43Z",
    "ctid_email": "trader@example.com", "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd",
    "start_date": "2026-07-24", "end_date": "2026-09-23", "data_mode": "ticks", "spread_pips": None,
    "balance": 10000.0, "overrides": {}, "params": {"TargetRiskReward": "1.5"}, "note": None,
}


class FakeContainer:
    def __init__(self, box, name, labels):
        self.box, self.name, self.labels = box, name, labels
        self.status = "running"
        self.attrs = {"State": {"ExitCode": 0}}
        self.report = None
        self.log_bytes = b""

    def logs(self, tail=None):
        return self.log_bytes

    def get_archive(self, path):
        if self.report is None:
            raise RuntimeError(f"Could not find the file {path} in container")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("report.json")
            info.size = len(self.report)
            tar.addfile(info, io.BytesIO(self.report))
        return iter([buf.getvalue()]), {"name": "report.json"}

    def finish(self, exit_code=0, report=None):
        self.status = "exited"
        self.attrs["State"]["ExitCode"] = exit_code
        self.report = report

    def remove(self, force=False):
        self.box.removed.append(self.name)
        self.box.items.pop(self.name, None)


class FakeContainers:
    def __init__(self):
        self.items, self.runs, self.removed = {}, [], []
        self.fail_run = None

    def run(self, **spec):
        if self.fail_run:
            raise self.fail_run
        self.runs.append(spec)
        container = FakeContainer(self, spec["name"], spec["labels"])
        self.items[spec["name"]] = container
        return container

    def list(self, all=False, filters=None):
        return [c for c in self.items.values() if filters["label"] in c.labels]

    def orphan(self, job_id):
        container = FakeContainer(self, f"bt-{job_id}", {"agentfx.backtest": str(job_id)})
        self.items[container.name] = container
        return container


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "FlowRsiBot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_demo-demo_pwd").write_text("pw")
    db = tmp_path / "bt.db"
    with store.connect(db) as conn:
        store.init_schema(conn)
    client, clock = FakeClient(), Clock()
    worker = BacktestWorker(client_provider=lambda: client, db_target=db, report_dir=tmp_path / "reports",
                            project_root=project, ctrader_home_dir=ctrader, clock=clock, max_parallel=2)

    class Env:
        pass
    e = Env()
    e.db, e.client, e.clock, e.worker, e.reports, e.ctrader = db, client, clock, worker, tmp_path / "reports", ctrader
    return e


def queue(env, **over):
    with store.connect(env.db) as conn:
        return store.create_job(conn, {**JOB, **over})


def job(env, job_id):
    with store.connect(env.db) as conn:
        return store.get_job(conn, job_id)


def set_fields(env, job_id, **fields):
    with store.connect(env.db) as conn:
        store.update_job(conn, job_id, **fields)


def test_parse_progress_takes_the_last_line():
    logs = ("Progress | Loading EURUSD, m15 | 33.87 % |\nProgress | Backtesting | 18.93 % |\n"
            "23/09/2026 18:00:00.000 | Info | [Order Executed] Buy 0.19 lots\nProgress | Backtesting | 20.50 % |\n")
    assert parse_progress(logs) == ("backtesting", 20.5)
    assert parse_progress("Progress | Loading EURUSD, m15 | 22.58 % |") == ("loading", 22.58)
    assert parse_progress("Logged in.") is None


def test_a_queued_job_starts_in_its_bt_container(env):
    job_id = queue(env)
    env.worker.tick()
    [spec] = env.client.containers.runs
    assert spec["name"] == f"bt-{job_id}" and spec["network_mode"] == "bridge"
    row = job(env, job_id)
    assert (row["status"], row["phase"], row["progress"]) == ("running", "starting", 0.0)
    assert row["started_at"]


def test_jobs_of_different_symbols_run_in_parallel_up_to_the_limit(env):
    eurusd, gbpusd, usdjpy = queue(env), queue(env, symbol="GBPUSD"), queue(env, symbol="USDJPY")
    env.worker.tick()
    assert [spec["name"] for spec in env.client.containers.runs] == [f"bt-{eurusd}", f"bt-{gbpusd}"]
    assert [job(env, i)["status"] for i in (eurusd, gbpusd, usdjpy)] == ["running", "running", "queued"]
    env.client.containers.items[f"bt-{eurusd}"].finish(0, REPORT)
    env.worker.tick()
    assert job(env, eurusd)["status"] == "done" and job(env, usdjpy)["status"] == "running"
    assert len(env.client.containers.runs) == 3


def test_a_job_waits_while_its_symbol_loads_the_same_dates_then_reads_the_cache(env):
    first, second = queue(env), queue(env, start_date="2026-09-01", end_date="2026-09-30")
    env.worker.tick()
    env.worker.tick()
    assert len(env.client.containers.runs) == 1
    assert job(env, first)["status"] == "running" and job(env, second)["status"] == "queued"
    container = env.client.containers.items[f"bt-{first}"]
    container.log_bytes = b"Progress | Loading EURUSD, m15 | 33.87 % |\n"
    env.worker.tick()
    assert job(env, second)["status"] == "queued"
    container.log_bytes += b"Progress | Backtesting | 1.50 % |\n"
    env.worker.tick()                    # this tick sees "backtesting" and starts the second run
    assert job(env, first)["status"] == "running" and job(env, second)["status"] == "running"
    assert [spec["name"] for spec in env.client.containers.runs] == [f"bt-{first}", f"bt-{second}"]


def test_runs_of_one_symbol_over_separate_dates_start_together(env):
    july = queue(env, start_date="2026-07-01", end_date="2026-07-31")
    august = queue(env, start_date="2026-08-01", end_date="2026-08-31")
    touching = queue(env, start_date="2026-08-31", end_date="2026-09-15")      # shares Aug 31 with `august`
    with store.connect(env.db) as conn:
        env.worker.set_max_parallel(conn, 3)
    env.worker.tick()
    assert [job(env, i)["status"] for i in (july, august, touching)] == ["running", "running", "queued"]


def test_a_job_waiting_on_its_symbol_does_not_hold_back_other_symbols(env):
    first, same, other = queue(env), queue(env), queue(env, symbol="GBPUSD")
    env.worker.tick()
    assert [job(env, i)["status"] for i in (first, same, other)] == ["running", "queued", "running"]


def test_the_limit_saved_on_the_page_replaces_the_default(env):
    ids = [queue(env, symbol=s) for s in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD")]
    with store.connect(env.db) as conn:
        assert env.worker.max_parallel(conn) == 2                  # the default until one is saved
        env.worker.set_max_parallel(conn, 3)
        assert env.worker.max_parallel(conn) == 3
    env.worker.tick()
    assert [job(env, i)["status"] for i in ids] == ["running", "running", "running", "queued"]


def test_lowering_the_limit_lets_running_jobs_finish(env):
    ids = [queue(env, symbol=s) for s in ("EURUSD", "GBPUSD", "USDJPY")]
    env.worker.tick()
    with store.connect(env.db) as conn:
        env.worker.set_max_parallel(conn, 1)
    env.client.containers.items[f"bt-{ids[0]}"].finish(0, REPORT)
    env.worker.tick()
    assert [job(env, i)["status"] for i in ids] == ["done", "running", "queued"]    # 1 running: no new start
    env.client.containers.items[f"bt-{ids[1]}"].finish(0, REPORT)
    env.worker.tick()
    assert job(env, ids[2])["status"] == "running"


@pytest.mark.parametrize("value", [0, -1, MAX_PARALLEL_CAP + 1])
def test_a_limit_out_of_range_is_refused(env, value):
    with store.connect(env.db) as conn:
        with pytest.raises(ValueError):
            env.worker.set_max_parallel(conn, value)
        assert env.worker.max_parallel(conn) == 2


def test_a_single_slot_runs_one_job_at_a_time(env):
    with store.connect(env.db) as conn:
        env.worker.set_max_parallel(conn, 1)
    first, second = queue(env), queue(env, symbol="GBPUSD")
    env.worker.tick()
    env.worker.tick()
    assert len(env.client.containers.runs) == 1 and job(env, second)["status"] == "queued"


def test_a_failed_start_frees_its_slot_for_the_next_job(env):
    broken = queue(env, pwd_file="/root/ctrader_data/missing_pwd")
    first, second = queue(env, symbol="GBPUSD"), queue(env, symbol="USDJPY")
    env.worker.tick()
    assert job(env, broken)["status"] == "failed"
    assert job(env, first)["status"] == "running" and job(env, second)["status"] == "running"


@pytest.mark.parametrize("raw, expected", [
    ("", DEFAULT_MAX_PARALLEL), ("abc", DEFAULT_MAX_PARALLEL), ("4", 4), (" 2 ", 2),
    ("0", 1), ("-3", 1), ("99", MAX_PARALLEL_CAP),
])
def test_max_parallel_comes_from_the_environment(monkeypatch, raw, expected):
    monkeypatch.setenv("BACKTEST_MAX_PARALLEL", raw)
    assert max_parallel_from_env() == expected
    assert BacktestWorker(client_provider=lambda: None).default_max_parallel == expected


def test_progress_is_read_from_the_log(env):
    job_id = queue(env)
    env.worker.tick()
    env.client.containers.items[f"bt-{job_id}"].log_bytes = b"Progress | Backtesting | 42.50 % |\n"
    env.worker.tick()
    row = job(env, job_id)
    assert (row["phase"], row["progress"]) == ("backtesting", 42.5)


def test_a_finished_job_is_collected_and_the_next_one_starts(env):
    first, second = queue(env), queue(env)
    env.worker.tick()
    env.client.containers.items[f"bt-{first}"].finish(0, REPORT)
    env.worker.tick()
    row = job(env, first)
    assert row["status"] == "done" and row["progress"] == 100.0 and row["finished_at"]
    assert row["summary"]["net_profit"] == -0.98 and row["summary"]["total_trades"] == 4
    assert store.read_report(first, env.reports)["main"]["symbol"] == "EURUSD"
    assert f"bt-{first}" in env.client.containers.removed
    assert job(env, second)["status"] == "running"


def test_a_non_zero_exit_fails_with_the_log_tail(env):
    job_id = queue(env)
    env.worker.tick()
    container = env.client.containers.items[f"bt-{job_id}"]
    container.log_bytes = b"Establishing connection...\nLogin failed: wrong password\n"
    container.finish(1)
    env.worker.tick()
    row = job(env, job_id)
    assert row["status"] == "failed" and row["error"].startswith("exit code 1")
    assert "Login failed" in row["error"] and f"bt-{job_id}" in env.client.containers.removed


# Tail of backtest #1 (AUDJPY 01/01-24/09/2026, 2026-09-25): the first order waited while cTrader
# fetched the conversion symbol's ticks into a cold cache, and the bot was aborted.
ABORT_LOG = (
    b"02/01/2026 06:15:00.729 | Trade | Executing Market Order to Sell 43000 AUDJPY (SL: 18.4, TP: 27)\n"
    b"02/01/2026 06:15:00.729 | Info | [OnBarClosed Error] Exception of type "
    b"'cTrader.Automate.Host.Dispatcher.Exceptions.AutomateDispatcherUnhandledException' was thrown.\n"
    b"Error | CBot instance [FlowRsiBot, AUDJPY, m15] aborted by timeout.\n"
    b"Message expected\nSystem.InvalidOperationException: Message expected\n"
)


def test_a_job_aborted_by_timeout_is_rerun_once_ahead_of_the_queue(env):
    first, second = queue(env), queue(env)
    env.worker.tick()
    container = env.client.containers.items[f"bt-{first}"]
    container.log_bytes = ABORT_LOG
    container.finish(1)
    env.worker.tick()
    assert [spec["name"] for spec in env.client.containers.runs] == [f"bt-{first}", f"bt-{first}"]
    assert f"bt-{first}" in env.client.containers.removed
    assert job(env, first)["status"] == "running" and job(env, second)["status"] == "queued"
    env.client.containers.items[f"bt-{first}"].finish(0, REPORT)
    env.worker.tick()
    row = job(env, first)
    assert row["status"] == "done" and row["summary"]["total_trades"] == 4


def test_a_second_timeout_abort_fails_the_job(env):
    job_id = queue(env)
    env.worker.tick()
    for _ in range(2):
        container = env.client.containers.items[f"bt-{job_id}"]
        container.log_bytes = ABORT_LOG
        container.finish(1)
        env.worker.tick()
    env.worker.tick()
    assert len(env.client.containers.runs) == 2
    row = job(env, job_id)
    assert row["status"] == "failed" and "aborted by timeout" in row["error"]
    assert "retried" in row["error"]


def test_exit_zero_without_a_report_fails(env):
    job_id = queue(env)
    env.worker.tick()
    env.client.containers.items[f"bt-{job_id}"].finish(0, None)
    env.worker.tick()
    assert job(env, job_id)["error"].startswith("no report written")


def test_an_unparsable_report_fails_but_keeps_the_file(env):
    job_id = queue(env)
    env.worker.tick()
    env.client.containers.items[f"bt-{job_id}"].finish(0, b"{not json")
    env.worker.tick()
    assert job(env, job_id)["error"].startswith("report unparsable")
    assert store.report_path(job_id, env.reports).read_bytes() == b"{not json"


def test_a_report_with_null_sections_fails_but_the_queue_continues(env):
    # Valid JSON, but "tradeStatistics": null makes summarize() raise AttributeError, which the
    # narrow (ValueError, KeyError, TypeError) catch used to miss, escaping _collect and tick().
    first, second = queue(env), queue(env)
    env.worker.tick()
    raw = b'{"main": null, "tradeStatistics": null, "equity": null}'
    env.client.containers.items[f"bt-{first}"].finish(0, raw)
    env.worker.tick()
    row = job(env, first)
    assert row["status"] == "failed" and row["error"].startswith("report unparsable")
    assert store.report_path(first, env.reports).read_bytes() == raw
    assert f"bt-{first}" in env.client.containers.removed
    assert job(env, second)["status"] == "running"


def test_a_report_that_cannot_be_saved_fails_the_job(env, monkeypatch):
    job_id = queue(env)
    env.worker.tick()
    env.client.containers.items[f"bt-{job_id}"].finish(0, REPORT)
    monkeypatch.setattr(store, "write_report", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    env.worker.tick()
    row = job(env, job_id)
    assert row["status"] == "failed" and row["error"].startswith("report not saved")
    assert f"bt-{job_id}" in env.client.containers.removed


def test_an_unexpected_error_handling_a_running_job_fails_it_and_the_queue_continues(env):
    # Simulates a Docker SDK access failing unexpectedly (not the specific cases _collect already
    # guards): the whole running-jobs branch must be wrapped so one job can never block the others.
    first, second = queue(env), queue(env)
    env.worker.tick()
    container = env.client.containers.items[f"bt-{first}"]
    container.status = "exited"
    del container.attrs
    env.worker.tick()
    row = job(env, first)
    assert row["status"] == "failed" and row["error"].startswith("worker error")
    assert f"bt-{first}" in env.client.containers.removed
    assert job(env, second)["status"] == "running"


def test_a_job_past_the_timeout_is_stopped(env):
    job_id = queue(env)
    env.worker.tick()
    set_fields(env, job_id, started_at=START.strftime("%Y-%m-%d %H:%M:%S"))
    env.clock.now = START + timedelta(seconds=TIMEOUT_S - 60)
    env.worker.tick()
    assert job(env, job_id)["status"] == "running"
    env.clock.now = START + timedelta(seconds=TIMEOUT_S + 60)
    env.worker.tick()
    row = job(env, job_id)
    assert row["status"] == "failed" and row["error"] == "timeout (45 min)"
    assert f"bt-{job_id}" in env.client.containers.removed


def test_a_cancelled_queued_job_never_starts(env):
    job_id = queue(env)
    set_fields(env, job_id, status="cancelled")
    env.worker.tick()
    assert env.client.containers.runs == []


def test_cancelling_a_running_job_removes_its_container(env):
    job_id = queue(env)
    env.worker.tick()
    set_fields(env, job_id, status="cancelled")
    env.worker.tick()
    assert f"bt-{job_id}" in env.client.containers.removed
    assert job(env, job_id)["status"] == "cancelled"


def test_reconcile_marks_a_running_job_without_container_lost(env):
    job_id = queue(env)
    set_fields(env, job_id, status="running", started_at=START.strftime("%Y-%m-%d %H:%M:%S"))
    env.worker.tick()
    row = job(env, job_id)
    assert (row["status"], row["error"]) == ("failed", "container lost")


def test_reconcile_collects_a_job_that_finished_while_the_app_was_down(env):
    job_id = queue(env)
    set_fields(env, job_id, status="running", started_at=START.strftime("%Y-%m-%d %H:%M:%S"))
    env.client.containers.orphan(job_id).finish(0, REPORT)
    env.worker.tick()
    assert job(env, job_id)["status"] == "done"


def test_reconcile_removes_labelled_containers_without_a_running_job(env):
    env.client.containers.orphan(999)
    env.worker.tick()
    assert env.client.containers.removed == ["bt-999"]


def test_a_start_failure_marks_the_job_failed(env):
    job_id = queue(env)
    env.client.containers.fail_run = RuntimeError("image not found")
    env.worker.tick()
    row = job(env, job_id)
    assert row["status"] == "failed" and row["error"] == "start failed: image not found"


def test_a_cancel_that_wins_the_start_race_removes_the_just_started_container(env):
    # Between containers.run() and the "queued" -> "running" update, the cancel button can flip
    # the row to "cancelled"; the update then returns False and the container must not be left running.
    job_id = queue(env)
    run = env.client.containers.run

    def run_then_cancel(**spec):
        container = run(**spec)
        set_fields(env, job_id, status="cancelled")
        return container

    env.client.containers.run = run_then_cancel
    env.worker.tick()
    assert job(env, job_id)["status"] == "cancelled"
    assert f"bt-{job_id}" in env.client.containers.removed


def test_a_missing_password_file_fails_at_start(env):
    job_id = queue(env)
    (env.ctrader / "ctrader_data" / "ctid_demo-demo_pwd").unlink()
    env.worker.tick()
    assert job(env, job_id)["error"] == "start failed: password file not found"
    assert env.client.containers.runs == []


def test_without_docker_the_tick_does_nothing(env):
    job_id = queue(env)
    env.worker.client_provider = lambda: None
    env.worker.tick()
    assert job(env, job_id)["status"] == "queued"


def test_start_up_adds_position_stats_to_summaries_saved_before_they_existed(env):
    old = queue(env)
    set_fields(env, old, status="done", summary={"net_profit": -0.98, "total_trades": 4})
    store.write_report(old, REPORT, env.reports)
    current = queue(env)
    set_fields(env, current, status="done", summary={"total_trades": 4, "positions": 3})
    store.write_report(current, REPORT, env.reports)
    no_report = queue(env)
    set_fields(env, no_report, status="done", summary={"total_trades": 1})
    unreadable = queue(env)
    set_fields(env, unreadable, status="done", summary={"total_trades": 1})
    store.write_report(unreadable, b'{"main": {}}', env.reports)
    failed = queue(env)
    set_fields(env, failed, status="failed", error="exit code 1")

    env.worker.refresh_summaries()

    refreshed = job(env, old)["summary"]
    assert (refreshed["positions"], refreshed["position_win_rate"], refreshed["full_loss_pct"]) == (4, 50.0, 50.0)
    assert refreshed["net_profit"] == -0.98 and refreshed["total_trades"] == 4
    assert job(env, current)["summary"] == {"total_trades": 4, "positions": 3}      # already has them
    assert job(env, no_report)["summary"] == {"total_trades": 1}
    assert job(env, unreadable)["summary"] == {"total_trades": 1}
    assert job(env, failed)["summary"] is None


def test_run_loop_refreshes_old_summaries_before_the_first_tick(env, monkeypatch):
    import asyncio
    calls = []
    monkeypatch.setattr(env.worker, "refresh_summaries", lambda: calls.append("refresh"))

    def tick():
        calls.append("tick")
        env.worker.stop()
    monkeypatch.setattr(env.worker, "tick", tick)
    monkeypatch.setattr("app.backtest_worker.POLL_S", 0)
    asyncio.run(env.worker.run_loop())
    assert calls == ["refresh", "tick"]
