"""Manual close, Pause/Resume and Close & Stop from the dashboard (spec 2026-09-24).

cBots cannot be reached by the server, so each one polls GET /api/cbot/commands every 2 s and
reports on POST /api/cbot/commands/{id}/result. The poll must stay in memory: with 45 containers
it runs ~22 times a second on a single uvicorn worker.
"""
import inspect
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.server import app
import app.dashboard as dashboard_module
from app import bot_commands
from app.bot_commands import CommandQueue
from app.portfolio import get_portfolio_manager

client = TestClient(app)
NAME = "cbot-test-manual-xauusd"


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    """A fresh queue per test, so commands left by one test never reach the next."""
    q = CommandQueue()
    monkeypatch.setattr(dashboard_module, "command_queue", q)
    return q


def _poll(bot_id):
    return client.get("/api/cbot/commands", params={"bot_id": bot_id}).json()


# --- bot-facing: poll + result ------------------------------------------------------------------

def test_poll_returns_the_pause_flag_and_pending_commands(queue):
    cmd = queue.enqueue("cbot-test-poll-1", "close_position", 4242)
    assert _poll("cbot-test-poll-1") == {
        "paused": False,
        "commands": [{"id": cmd.id, "action": "close_position", "position_id": 4242}],
    }
    assert _poll("cbot-test-poll-1")["commands"] == []


def test_poll_strips_quotes_the_ctrader_cli_leaves_on_bot_id(queue):
    queue.enqueue("cbot-test-poll-2", "close_all")
    assert len(_poll('"cbot-test-poll-2"')["commands"]) == 1


def test_poll_never_touches_the_db_or_docker(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the 2 s poll must stay in memory")

    monkeypatch.setattr(get_portfolio_manager(), "_get_conn", boom)
    monkeypatch.setattr(dashboard_module, "get_db", boom)
    monkeypatch.setattr(dashboard_module, "docker_manager", None)
    r = client.get("/api/cbot/commands", params={"bot_id": "cbot-test-poll-3"})
    assert r.status_code == 200
    assert inspect.iscoroutinefunction(dashboard_module.api_cbot_poll_commands)


def test_the_bot_reports_a_result(queue):
    cmd = queue.enqueue("cbot-test-result-1", "close_all")
    queue.take_pending("cbot-test-result-1")
    r = client.post(f"/api/cbot/commands/{cmd.id}/result",
                    json={"bot_id": "cbot-test-result-1", "status": "done", "message": "closed 1",
                          "closed": 1, "failed": 0})
    assert r.status_code == 200 and r.json() == {"ok": True}
    status = client.get(f"/api/bot-commands/{cmd.id}").json()
    assert status["status"] == "done" and status["closed"] == 1


@pytest.mark.parametrize("payload, code", [
    ({"bot_id": "someone-else", "status": "done"}, 409),
    ({"bot_id": "cbot-test-result-2", "status": "maybe"}, 422),
])
def test_bad_results_are_refused(queue, payload, code):
    cmd = queue.enqueue("cbot-test-result-2", "close_all")
    assert client.post(f"/api/cbot/commands/{cmd.id}/result", json=payload).status_code == code


def test_unknown_commands_are_404():
    assert client.post("/api/cbot/commands/nope/result",
                       json={"bot_id": "x", "status": "done"}).status_code == 404
    assert client.get("/api/bot-commands/nope").status_code == 404


# --- access log ---------------------------------------------------------------------------------

def _access_record(method, path):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                             ("127.0.0.1:50000", method, path, "1.1", 200), None)


def test_access_log_drops_only_the_command_poll():
    from app.server import SkipCommandPollAccessLog
    f = SkipCommandPollAccessLog()
    assert f.filter(_access_record("GET", "/api/cbot/commands?bot_id=cbot-x")) is False
    assert f.filter(_access_record("POST", "/api/cbot/commands/abc/result")) is True
    assert f.filter(_access_record("GET", "/api/bots")) is True
    assert any(isinstance(x, SkipCommandPollAccessLog) for x in logging.getLogger("uvicorn.access").filters)


# --- close one position -------------------------------------------------------------------------

ACCOUNT = "acct-manual"


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.is_available = True
        self.status = "running"
        self.stop_ok = True

    def get_container_status(self, name):
        self.calls.append(("status", name))
        return {"status": self.status, "id": "abc123def456"}

    def stop_container(self, name):
        self.calls.append(("stop", name))
        return {"success": self.stop_ok, "message": "stopped" if self.stop_ok else "docker down"}


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(dashboard_module, "docker_manager", fake)
    return fake


@pytest.fixture
def bot_config():
    pm = get_portfolio_manager()
    pm.delete_cbot_config(NAME)
    assert pm.add_cbot_config(NAME, "manual close test",
                              f'docker run -d --name {NAME} img run /workspace/cBot/AiAgentBot.algo --BotId="{NAME}"')
    yield NAME
    pm.delete_cbot_config(NAME)
    pm.set_bot_paused(NAME, False)


@pytest.fixture
def cleanup_positions():
    yield
    conn = get_portfolio_manager()._get_conn()
    try:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (ACCOUNT,))
        conn.commit()
    finally:
        conn.close()


def _open_position(ctrader_id=None, bot_id=NAME):
    pm = get_portfolio_manager()
    assert pm.register_position(bot_id=bot_id, symbol="XAUUSD", side="Buy", volume=0.1, entry_price=2000.0,
                                sl_pips=100, tp_pips=200, account_id=ACCOUNT, ctrader_id=ctrader_id)
    conn = pm._get_conn()
    try:
        return conn.execute("SELECT MAX(id) FROM positions WHERE bot_id = ? AND status = 'open'",
                            (bot_id,)).fetchone()[0]
    finally:
        conn.close()


def test_positions_payload_carries_the_row_and_ctrader_ids(cleanup_positions):
    row_id = _open_position(ctrader_id=900001)
    rows = client.get("/api/dashboard/positions", params={"account_id": ACCOUNT}).json()
    row = next(p for p in rows if p["id"] == row_id)
    assert row["ctrader_id"] == 900001


def test_close_queues_a_close_for_that_bot(fake_docker, bot_config, cleanup_positions):
    row_id = _open_position(ctrader_id=900002)
    r = client.post(f"/api/positions/{row_id}/close")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["status"] == "pending"
    assert _poll(NAME)["commands"] == [{"id": body["command_id"], "action": "close_position",
                                        "position_id": 900002}]


def test_close_twice_returns_the_same_command(fake_docker, bot_config, cleanup_positions):
    row_id = _open_position(ctrader_id=900003)
    first = client.post(f"/api/positions/{row_id}/close").json()["command_id"]
    assert client.post(f"/api/positions/{row_id}/close").json()["command_id"] == first


def test_close_unknown_position_is_404(fake_docker):
    assert client.post("/api/positions/99999999/close").status_code == 404


def test_close_without_a_ctrader_id_is_refused(fake_docker, bot_config, cleanup_positions):
    row_id = _open_position(ctrader_id=None)
    r = client.post(f"/api/positions/{row_id}/close")
    assert r.status_code == 409 and "cTrader" in r.json()["message"]


def test_close_is_refused_when_the_container_is_down(fake_docker, bot_config, cleanup_positions):
    fake_docker.status = "exited"
    row_id = _open_position(ctrader_id=900004)
    r = client.post(f"/api/positions/{row_id}/close")
    assert r.status_code == 409 and "exited" in r.json()["message"]
    assert _poll(NAME)["commands"] == []


def test_close_position_handler_runs_off_the_event_loop():
    assert not inspect.iscoroutinefunction(dashboard_module.api_close_position)


# --- container controls -------------------------------------------------------------------------

def _bot(name):
    return next(b for b in client.get("/api/bots").json()["bots"] if b["name"] == name)


def test_pause_and_resume_a_container(fake_docker, bot_config):
    r = client.post(f"/api/bots/{NAME}/pause").json()
    assert r["success"] is True and r["paused"] is True and r["bot_id"] == NAME
    assert _poll(NAME)["paused"] is True
    assert get_portfolio_manager().check_risk("XAUUSD", None, 0.01, account_balance=5000.0,
                                              account_id=ACCOUNT, used_margin=0.0,
                                              bot_id=NAME) == (False, "Paused from dashboard")
    assert client.post(f"/api/bots/{NAME}/resume").json()["paused"] is False
    assert _poll(NAME)["paused"] is False


def test_pause_unknown_bot_is_404(fake_docker):
    assert client.post("/api/bots/cbot-does-not-exist/pause").status_code == 404


def test_pause_and_resume_handlers_run_off_the_event_loop():
    for handler in (dashboard_module.api_pause_bot, dashboard_module.api_resume_bot):
        assert not inspect.iscoroutinefunction(handler), handler.__name__


def test_bots_list_shows_pause_and_open_positions(fake_docker, bot_config, cleanup_positions, monkeypatch):
    from app.cbot_watchdog import cbot_watchdog
    monkeypatch.setattr(cbot_watchdog, "last_health", lambda name: None)
    _open_position(ctrader_id=900010)
    _open_position(ctrader_id=900011)
    client.post(f"/api/bots/{NAME}/pause")
    bot = _bot(NAME)
    assert bot["bot_id"] == NAME and bot["paused"] is True and bot["open_positions"] == 2


def test_bots_list_says_whether_the_bot_polls(fake_docker, bot_config, monkeypatch):
    """A container whose bot never polls (old .algo, CommandPollMs=0, a BotId that differs from
    its --BotId) can't receive Close / Close & Stop: /api/bots says so for the NOT POLLING badge."""
    from app.cbot_watchdog import cbot_watchdog
    monkeypatch.setattr(cbot_watchdog, "last_health", lambda name: None)
    now = [1000.0]
    monkeypatch.setattr(dashboard_module, "command_queue", CommandQueue(clock=lambda: now[0]))

    bot = _bot(NAME)
    assert bot["polling"] is False and bot["last_poll_age_s"] is None

    _poll(NAME)
    now[0] += 3
    bot = _bot(NAME)
    assert bot["polling"] is True and bot["last_poll_age_s"] == 3

    now[0] += bot_commands.POLL_STALE_S
    bot = _bot(NAME)
    assert bot["polling"] is False and bot["last_poll_age_s"] == 3 + bot_commands.POLL_STALE_S


class BotThread:
    """Stands in for the cBot: polls the queue and answers every command it is handed."""

    def __init__(self, queue, status="done", closed=2, failed=0):
        self.queue, self.status, self.closed, self.failed = queue, status, closed, failed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            for cmd in self.queue.take_pending(NAME):
                message = "broker says no" if self.status == "failed" else f"closed {self.closed}"
                self.queue.record_result(cmd.id, NAME, self.status, message, self.closed, self.failed)
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(1)


@pytest.fixture
def fast_waits(monkeypatch):
    monkeypatch.setattr(bot_commands, "WAIT_POLL_S", 0.02)
    monkeypatch.setattr(bot_commands, "CLOSE_AND_STOP_WAIT_S", 1.0)


def test_close_and_stop_closes_then_stops(fake_docker, bot_config, fast_waits, queue):
    with BotThread(queue, status="done", closed=2):
        body = client.post(f"/api/bots/{NAME}/close-and-stop").json()
    assert body["success"] is True and body["closed"] == 2, body
    assert ("stop", NAME) in fake_docker.calls
    assert get_portfolio_manager().is_bot_paused(NAME) is True      # stays paused until Resume


def test_close_and_stop_keeps_the_container_when_a_close_fails(fake_docker, bot_config, fast_waits, queue):
    with BotThread(queue, status="failed", closed=1, failed=1):
        body = client.post(f"/api/bots/{NAME}/close-and-stop").json()
    assert body["success"] is False and body["stage"] == "close" and body["paused"] is True
    assert "broker says no" in body["message"]
    assert ("stop", NAME) not in fake_docker.calls


def test_close_and_stop_keeps_the_container_when_the_bot_never_answers(fake_docker, bot_config, fast_waits):
    body = client.post(f"/api/bots/{NAME}/close-and-stop").json()      # nobody polls
    assert body["success"] is False and body["stage"] == "close"
    assert ("stop", NAME) not in fake_docker.calls
    assert get_portfolio_manager().is_bot_paused(NAME) is True


def test_close_and_stop_refuses_a_stopped_container(fake_docker, bot_config, queue):
    fake_docker.status = "exited"
    r = client.post(f"/api/bots/{NAME}/close-and-stop")
    assert r.status_code == 409
    assert get_portfolio_manager().is_bot_paused(NAME) is False
    assert queue.take_pending(NAME) == []


def test_close_and_stop_reports_a_failed_docker_stop(fake_docker, bot_config, fast_waits, queue):
    fake_docker.stop_ok = False
    with BotThread(queue, status="done", closed=1):
        body = client.post(f"/api/bots/{NAME}/close-and-stop").json()
    assert body["success"] is False and body["stage"] == "stop" and "docker down" in body["message"]


def test_close_and_stop_unknown_bot_is_404(fake_docker):
    assert client.post("/api/bots/cbot-does-not-exist/close-and-stop").status_code == 404
