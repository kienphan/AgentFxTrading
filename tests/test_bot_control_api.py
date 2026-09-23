"""Docker bot control endpoints: /api/bots list + start/stop/restart/delete.

Background (2026-09-21): with 22 containers the dashboard polled /api/bots every 10 s, each poll
doing 44 blocking Docker calls inside an `async def` handler. That pinned the event loop for
4-10 s per poll, so every button click (and every cBot /trade call) queued behind it.
"""
import inspect
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app
import app.dashboard as dashboard_module
from app.portfolio import get_portfolio_manager
from app.cbot_watchdog import cbot_watchdog

client = TestClient(app)
NAME = "cbot-test-control-xauusd"


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.is_available = True
        self.status = "running"
        self.remove_ok = True

    def get_container_status(self, name):
        return {"status": self.status, "id": "abc123def456"}

    def check_cbot_health(self, name):
        raise AssertionError("/api/bots must not read container logs on every poll")

    def stop_container(self, name):
        self.calls.append(("stop", name))
        return {"success": True, "message": "stopped"}

    def remove_container(self, name):
        self.calls.append(("remove", name))
        return {"success": self.remove_ok, "message": "removed" if self.remove_ok else "docker down"}


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(dashboard_module, "docker_manager", fake)
    return fake


@pytest.fixture
def bot_config():
    pm = get_portfolio_manager()
    pm.delete_cbot_config(NAME)
    assert pm.add_cbot_config(NAME, "control test", f"docker run -d --name {NAME} img")
    yield NAME
    pm.delete_cbot_config(NAME)


def test_adding_a_duplicate_bot_name_is_refused_not_a_500(bot_config):
    # add_cbot_config caught sqlite3.IntegrityError without importing sqlite3, so the
    # duplicate raised NameError (and on PostgreSQL the error is psycopg2's anyway).
    r = client.post("/api/bots", json={"name": NAME, "run_command": "docker run -d img"})
    assert r.status_code == 200
    assert r.json() == {"success": False, "message": "Bot name already exists"}


def _bot(name):
    return next(b for b in client.get("/api/bots").json()["bots"] if b["name"] == name)


# --- 1. never block the event loop -------------------------------------------------------------

def test_bot_control_endpoints_run_off_the_event_loop():
    # Plain `def` handlers run in FastAPI's threadpool; `async def` + blocking docker calls stall
    # /trade and /ws/cbot for every running cBot. Same rule api_setup_instances already follows.
    for handler in (dashboard_module.api_get_bots, dashboard_module.api_start_bot,
                    dashboard_module.api_stop_bot, dashboard_module.api_restart_bot,
                    dashboard_module.api_remove_bot, dashboard_module.api_update_bot,
                    dashboard_module.api_delete_bot):
        assert not inspect.iscoroutinefunction(handler), handler.__name__


# --- 1b. /api/bots reuses the watchdog's health instead of reading every container's logs ------

def test_bots_list_uses_watchdog_health_instead_of_reading_logs(fake_docker, bot_config, monkeypatch):
    cached = {"status": "running", "healthy": False, "stuck": True,
              "reason": "Reconnect loop: 20 'connection has been lost' in the last 40 lines without re-establishing"}
    monkeypatch.setattr(cbot_watchdog, "last_health", lambda name: cached if name == NAME else None)
    bot = _bot(NAME)
    assert bot["status"] == "running"
    assert bot["stuck"] is True and bot["healthy"] is False
    assert bot["health_reason"] == cached["reason"]


def test_bots_list_reports_awaiting_watchdog_when_no_health_yet(fake_docker, bot_config, monkeypatch):
    monkeypatch.setattr(cbot_watchdog, "last_health", lambda name: None)
    bot = _bot(NAME)
    assert bot["healthy"] is True and bot["stuck"] is False
    assert bot["health_reason"] == "Awaiting first watchdog check"


def test_bots_list_derives_health_from_status_when_not_running(fake_docker, bot_config, monkeypatch):
    # A cached "stuck" verdict from a minute ago must not outlive the container itself.
    fake_docker.status = "exited"
    monkeypatch.setattr(cbot_watchdog, "last_health", lambda name: {"stuck": True, "healthy": False, "reason": "old"})
    bot = _bot(NAME)
    assert bot["status"] == "exited"
    assert bot["healthy"] is False and bot["stuck"] is False
    assert bot["health_reason"] == "Container is exited"


# --- 2. Delete removes the container, not just the row ----------------------------------------

def test_delete_bot_stops_and_removes_its_container(fake_docker, bot_config):
    res = client.delete(f"/api/bots/{NAME}").json()
    assert res["success"] is True, res
    assert fake_docker.calls == [("stop", NAME), ("remove", NAME)]
    assert get_portfolio_manager().get_cbot_config(NAME) is None


def test_delete_bot_keeps_config_when_container_removal_fails(fake_docker, bot_config):
    fake_docker.remove_ok = False
    res = client.delete(f"/api/bots/{NAME}").json()
    assert res["success"] is False
    assert "docker down" in res["message"]
    assert get_portfolio_manager().get_cbot_config(NAME) is not None   # still visible, user can retry


def test_delete_unknown_bot_reports_not_found(fake_docker):
    get_portfolio_manager().delete_cbot_config(NAME)
    res = client.delete(f"/api/bots/{NAME}").json()
    assert res == {"success": False, "message": "Bot config not found"}
    assert fake_docker.calls == []
