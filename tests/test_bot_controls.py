"""Dashboard pause (Bots tab): a paused bot takes no new entries until Resume.

The pause lives in the bot_controls table so it survives service and container restarts.
check_risk refuses entries for a paused bot_id; the 2 s command poll reads an in-memory copy
(PortfolioManager.is_bot_paused) because it must never touch the DB.
"""
import pytest

from app import bot_controls
from app.portfolio import PortfolioManager

ACCOUNT = "acct-pause"


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(db_path=str(tmp_path / "pause.db"))


def _check(pm, bot_id):
    return pm.check_risk("EURUSD", None, 0.01, account_balance=2000.0, account_id=ACCOUNT,
                         used_margin=0.0, bot_id=bot_id)


def test_a_paused_bot_is_refused_new_entries(pm):
    pm.set_bot_paused("cbot-a", True)
    assert _check(pm, "cbot-a") == (False, "Paused from dashboard")
    assert _check(pm, "cbot-b") == (True, "OK")


def test_resume_allows_entries_again(pm):
    pm.set_bot_paused("cbot-a", True)
    pm.set_bot_paused("cbot-a", False)
    assert _check(pm, "cbot-a") == (True, "OK")
    assert pm.is_bot_paused("cbot-a") is False


def test_pause_survives_a_restart(tmp_path):
    path = str(tmp_path / "pause.db")
    PortfolioManager(db_path=path).set_bot_paused("cbot-a", True)
    reopened = PortfolioManager(db_path=path)
    assert reopened.is_bot_paused("cbot-a") is True
    assert _check(reopened, "cbot-a")[0] is False


def test_is_bot_paused_never_reads_the_db(pm, monkeypatch):
    pm.set_bot_paused("cbot-a", True)

    def boom():
        raise AssertionError("is_bot_paused must answer from memory")

    monkeypatch.setattr(pm, "_get_conn", boom)
    assert pm.is_bot_paused("cbot-a") is True
    assert pm.is_bot_paused("cbot-b") is False


def test_pausing_twice_keeps_one_row(pm):
    pm.set_bot_paused("cbot-a", True)
    pm.set_bot_paused("cbot-a", True)
    conn = pm._get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM bot_controls WHERE bot_id = 'cbot-a'").fetchone()[0] == 1
    finally:
        conn.close()


def test_check_risk_without_a_bot_id_ignores_pauses(pm):
    pm.set_bot_paused("cbot-a", True)
    assert pm.check_risk("EURUSD", None, 0.01, account_balance=2000.0, account_id=ACCOUNT,
                         used_margin=0.0) == (True, "OK")


@pytest.mark.parametrize("raw, clean", [
    ('"cbot-x"', "cbot-x"),
    ("cbot-x --symbol=EURUSD", "cbot-x"),
    ("", "default"),
    (None, "default"),
])
def test_sanitize_bot_id(raw, clean):
    assert bot_controls.sanitize_bot_id(raw) == clean


def test_server_still_exports_sanitize_bot_id():
    from app.server import sanitize_bot_id
    assert sanitize_bot_id is bot_controls.sanitize_bot_id


@pytest.mark.parametrize("cfg, bot_id", [
    ({"name": "cbot-demo-main-xauusd",
      "run_command": 'docker run -d img run x.algo --BotId="cbot-demo-main-xauusd" --x=1'}, "cbot-demo-main-xauusd"),
    ({"name": "cbot-usdjpy", "run_command": "docker run -d img run x.algo --BotId=usdjpy-tokyo --x=1"}, "usdjpy-tokyo"),
    ({"name": "cbot-handmade", "run_command": "docker run -d img"}, "cbot-handmade"),
])
def test_bot_id_for_config(cfg, bot_id):
    assert bot_controls.bot_id_for_config(cfg) == bot_id
