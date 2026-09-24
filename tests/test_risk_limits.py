"""
Three-layer daily loss limits (audit 2026-09-24, item 2.2).

On 2026-09-23 the only limit summed closed P&L for the whole account: it tripped at
13:12 UTC with 11 positions still open, the day ended at -281 $, and FlowRSI's losses
blocked the TMS bots too. Each layer now sums the day's closed P&L plus what the open
positions would add at their stops (the bots' sl_pnl), and blocks new entries only for
its own scope:

- account:   every bot on the account
- strategy:  FlowRSI / TMS / Judas bots on the account
- container: one bot, a fuse for a single misbehaving container
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.portfolio import PortfolioManager
from app.risk_limits import strategy_of

ROOT = Path(__file__).resolve().parent.parent
ACCOUNT = "acct-risk"

FLOW_A = "cbot-demo-demo-eurusd-all-flowrsi"
FLOW_B = "cbot-demo-demo-gbpusd-all-flowrsi"
TMS = "cbot-demo-demo-gbpusd-london"
JUDAS = "cbot-demo-demo-xauusd-judas"


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(db_path=str(tmp_path / "risk.db"))


def _closed(pm, bot_id, pnl, symbol="EURUSD", ctrader_id=None):
    pm.register_position(bot_id=bot_id, symbol=symbol, side="Buy", volume=0.05,
                         entry_price=1.1, sl_pips=15, tp_pips=22, account_id=ACCOUNT,
                         ctrader_id=ctrader_id)
    assert pm.close_position(bot_id=bot_id, symbol=symbol, exit_price=1.1, pnl=pnl,
                             account_id=ACCOUNT, ctrader_id=ctrader_id)


def _open(pm, bot_id, symbol="EURUSD", sl_pnl=None, unrealized=0.0):
    pm.register_position(bot_id=bot_id, symbol=symbol, side="Buy", volume=0.05,
                         entry_price=1.1, sl_pips=15, tp_pips=22, account_id=ACCOUNT)
    levels = {"sl_pnl": sl_pnl} if sl_pnl is not None else None
    pm.update_position_metrics(bot_id, unrealized, 0.0, account_id=ACCOUNT, levels=levels)


def _check(pm, bot_id):
    return pm.check_risk("EURUSD", None, 0.01, account_balance=2000.0, account_id=ACCOUNT,
                         used_margin=0.0, bot_id=bot_id)


# --- strategy of a bot ------------------------------------------------------------------

@pytest.mark.parametrize("bot_id, strategy", [
    (FLOW_A, "flowrsi"),
    ("cbot-demo-demo-btcusd-all-flowrsi", "flowrsi"),
    (JUDAS, "judas"),
    ("cbot-demo-demo-usdjpy-tokyo", "tms"),
    (TMS, "tms"),
    ("cbot-demo-demo-ustec-newyork", "tms"),
    (None, None),
    ("", None),
])
def test_strategy_of(bot_id, strategy):
    assert strategy_of(bot_id) == strategy


# --- defaults ---------------------------------------------------------------------------

def test_default_limits_are_seeded(pm):
    limits = {(r["scope"], r["target"]): r for r in pm.get_risk_limits()}
    assert {k: v["max_daily_loss"] for k, v in limits.items()} == {
        ("account", "*"): 200.0,
        ("strategy", "flowrsi"): 100.0,
        ("strategy", "tms"): 60.0,
        ("strategy", "judas"): 60.0,
        ("container", "*"): 30.0,
    }
    assert all(r["enabled"] for r in limits.values())


def test_reopening_the_database_keeps_edited_limits(tmp_path):
    first = PortfolioManager(db_path=str(tmp_path / "risk.db"))
    first.update_risk_limits([{"scope": "strategy", "target": "flowrsi", "max_daily_loss": 150}])
    again = PortfolioManager(db_path=str(tmp_path / "risk.db"))
    limits = {(r["scope"], r["target"]): r["max_daily_loss"] for r in again.get_risk_limits()}
    assert limits[("strategy", "flowrsi")] == 150.0


# --- each layer blocks its own scope ----------------------------------------------------

def test_container_layer_blocks_only_that_bot(pm):
    _closed(pm, FLOW_A, -18.0)
    _closed(pm, FLOW_A, -13.0)

    allowed, reason = _check(pm, FLOW_A)
    assert not allowed
    assert reason.startswith("Daily loss limit (container")
    assert FLOW_A in reason

    allowed, reason = _check(pm, FLOW_B)
    assert allowed, reason


def test_strategy_layer_blocks_that_strategy_and_spares_the_others(pm):
    # 5 FlowRSI bots at -21 $ each: every container is inside its 30 $, FlowRSI is at -105.
    for i in range(5):
        _closed(pm, f"cbot-demo-demo-sym{i}-all-flowrsi", -21.0, symbol=f"SYM{i}")

    allowed, reason = _check(pm, FLOW_A)
    assert not allowed
    assert reason.startswith("Daily loss limit (strategy flowrsi)")

    assert _check(pm, TMS)[0]
    assert _check(pm, JUDAS)[0]


def test_account_layer_blocks_every_bot(pm):
    # 4 x -24 FlowRSI (-96), 2 x -29 TMS (-58), 2 x -25 Judas (-50): each strategy and each
    # container is inside its limit, the account is at -204.
    for i in range(4):
        _closed(pm, f"cbot-demo-demo-f{i}-all-flowrsi", -24.0, symbol=f"F{i}")
    for i in range(2):
        _closed(pm, f"cbot-demo-demo-t{i}-london", -29.0, symbol=f"T{i}")
        _closed(pm, f"cbot-demo-demo-j{i}-judas", -25.0, symbol=f"J{i}")

    for bot in (FLOW_A, TMS, JUDAS):
        allowed, reason = _check(pm, bot)
        assert not allowed
        assert reason.startswith("Daily loss limit (account)")


def test_without_a_bot_id_only_the_account_layer_applies(pm):
    _closed(pm, FLOW_A, -35.0)
    allowed, reason = pm.check_risk("EURUSD", None, 0.01, account_balance=2000.0,
                                    account_id=ACCOUNT, used_margin=0.0)
    assert allowed, reason


# --- what counts as the day's loss ------------------------------------------------------

def test_open_positions_count_at_their_stop(pm):
    _closed(pm, FLOW_B, -25.0)
    _open(pm, FLOW_A, sl_pnl=-9.0)
    allowed, reason = _check(pm, FLOW_B)
    assert allowed, reason  # the container: -25 closed, no open position of its own

    _closed(pm, "cbot-demo-demo-audusd-all-flowrsi", -28.0, symbol="AUDUSD")
    _closed(pm, "cbot-demo-demo-nzdusd-all-flowrsi", -29.0, symbol="NZDUSD")
    _open(pm, "cbot-demo-demo-usdcad-all-flowrsi", symbol="USDCAD", sl_pnl=-10.0)
    # FlowRSI closed -82, open risk -19: -101 against 100.
    allowed, reason = _check(pm, "cbot-demo-demo-usdchf-all-flowrsi")
    assert not allowed
    assert "closed -82.00" in reason and "open risk -19.00" in reason


def test_a_stop_past_break_even_offsets_the_loss(pm):
    _closed(pm, FLOW_A, -35.0)
    _open(pm, FLOW_A, sl_pnl=+8.0)
    allowed, reason = _check(pm, FLOW_A)
    assert allowed, reason  # -35 + 8 = -27, inside 30


def test_missing_stop_estimate_falls_back_to_current_pnl(pm):
    _closed(pm, FLOW_A, -22.0)
    _open(pm, FLOW_A, sl_pnl=None, unrealized=-9.0)
    allowed, reason = _check(pm, FLOW_A)
    assert not allowed  # -22 + -9 = -31
    assert "open risk -9.00" in reason


def test_yesterdays_losses_do_not_count(pm):
    _closed(pm, FLOW_A, -50.0)
    conn = pm._get_conn()
    try:
        conn.execute("UPDATE positions SET exit_time = '2020-01-01 10:00:00', entry_time = '2020-01-01 09:00:00'")
        conn.commit()
    finally:
        conn.close()
    assert _check(pm, FLOW_A)[0]


def test_a_disabled_layer_is_not_enforced(pm):
    _closed(pm, FLOW_A, -40.0)
    pm.update_risk_limits([{"scope": "container", "target": "*", "enabled": False}])
    assert _check(pm, FLOW_A)[0]


def test_other_accounts_do_not_count(pm):
    pm.register_position(bot_id=FLOW_A, symbol="EURUSD", side="Buy", volume=0.05, entry_price=1.1,
                         sl_pips=15, tp_pips=22, account_id="other-acct")
    pm.close_position(bot_id=FLOW_A, symbol="EURUSD", exit_price=1.1, pnl=-80.0, account_id="other-acct")
    assert _check(pm, FLOW_A)[0]


# --- editing ----------------------------------------------------------------------------

@pytest.mark.parametrize("update", [
    {"scope": "strategy", "target": "scalper", "max_daily_loss": 50},
    {"scope": "desk", "target": "*", "max_daily_loss": 50},
    {"scope": "account", "target": "*", "max_daily_loss": 0},
    {"scope": "account", "target": "*", "max_daily_loss": -10},
    {"scope": "account", "target": "*", "max_daily_loss": "lots"},
])
def test_invalid_updates_are_refused_and_nothing_changes(pm, update):
    before = pm.get_risk_limits()
    with pytest.raises(ValueError):
        pm.update_risk_limits([{"scope": "container", "target": "*", "max_daily_loss": 45}, update])
    assert pm.get_risk_limits() == before


def test_usage_reports_each_layer(pm):
    _closed(pm, FLOW_A, -12.5)
    _closed(pm, TMS, 7.0, symbol="GBPUSD")
    _open(pm, FLOW_B, symbol="GBPUSD", sl_pnl=-9.0)

    usage = pm.daily_loss_usage(ACCOUNT)
    assert usage["account"] == {"closed": -5.5, "open_risk": -9.0, "total": -14.5}
    assert usage["strategy"]["flowrsi"] == {"closed": -12.5, "open_risk": -9.0, "total": -21.5}
    assert usage["strategy"]["tms"] == {"closed": 7.0, "open_risk": 0.0, "total": 7.0}
    assert usage["strategy"]["judas"] == {"closed": 0.0, "open_risk": 0.0, "total": 0.0}
    assert usage["container"][FLOW_B] == {"closed": 0.0, "open_risk": -9.0, "total": -9.0}


# --- API --------------------------------------------------------------------------------

@pytest.fixture
def api():
    from app.server import app
    from app.portfolio import get_portfolio_manager
    pm = get_portfolio_manager()
    original = pm.get_risk_limits()
    yield TestClient(app), pm
    pm.update_risk_limits(original)


def test_api_reads_limits_and_usage(api):
    client, pm = api
    data = client.get("/api/risk-limits").json()
    assert {(r["scope"], r["target"]) for r in data["limits"]} == {
        ("account", "*"), ("strategy", "flowrsi"), ("strategy", "tms"),
        ("strategy", "judas"), ("container", "*"),
    }
    assert "date" in data and isinstance(data["accounts"], list)


def test_api_saves_limits(api):
    client, pm = api
    resp = client.put("/api/risk-limits", json={"limits": [
        {"scope": "strategy", "target": "tms", "max_daily_loss": 75, "enabled": False},
    ]})
    assert resp.status_code == 200
    saved = {(r["scope"], r["target"]): r for r in resp.json()["limits"]}
    assert saved[("strategy", "tms")]["max_daily_loss"] == 75.0
    assert saved[("strategy", "tms")]["enabled"] is False


def test_api_refuses_invalid_limits(api):
    client, pm = api
    before = pm.get_risk_limits()
    resp = client.put("/api/risk-limits", json={"limits": [
        {"scope": "account", "target": "*", "max_daily_loss": -5},
    ]})
    assert resp.status_code == 400
    assert pm.get_risk_limits() == before


# --- wiring -----------------------------------------------------------------------------

def test_server_passes_the_bot_to_every_risk_check():
    src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    calls = src.split("portfolio_manager.check_risk(")[1:]
    assert len(calls) >= 2
    for call in calls:
        depth, end = 1, 0
        while depth:
            depth += {"(": 1, ")": -1}.get(call[end], 0)
            end += 1
        assert "bot_id=snapshot.bot_id" in call[:end]


def test_dashboard_has_a_risk_limits_view():
    html = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-target="view-risk"' in html
    assert 'id="view-risk"' in html
    assert "/api/risk-limits" in html
