"""SL/TP prices with the P&L at each level, and the close reason, on the dashboard.

Active Positions shows each level as a price plus what the position would net if it closed
there; Recent Trades shows the levels the broker held when the position closed and why it
closed. The P&L-at-level figures come from the bot (cTrader's own pip value and volume) and are
never computed server-side, for the same reason unrealized P&L is not.
"""
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import app  # noqa: E402
from app.portfolio import PortfolioManager  # noqa: E402

client = TestClient(app)


# --- storage ---------------------------------------------------------------------------------

@pytest.fixture
def pm(tmp_path):
    manager = PortfolioManager(db_path=str(tmp_path / "sltp.db"))
    manager.register_position(
        bot_id="xauusd-judas", symbol="XAUUSD", side="Buy", volume=0.1, entry_price=2500.0,
        sl_pips=100.0, tp_pips=200.0, account_id="acct-1", ctrader_id=11,
        sl_price=2490.0, tp_price=2520.0,
    )
    return manager


def _row(manager):
    conn = manager._get_conn()
    try:
        r = conn.execute(
            "SELECT sl_price, tp_price, close_reason, status FROM positions WHERE ctrader_id = 11"
        ).fetchone()
        return {"sl_price": r[0], "tp_price": r[1], "close_reason": r[2], "status": r[3]}
    finally:
        conn.close()


def test_open_records_the_sl_tp_prices(pm):
    row = _row(pm)
    assert row["sl_price"] == 2490.0
    assert row["tp_price"] == 2520.0
    assert row["close_reason"] is None


def test_close_stores_the_final_levels_and_the_reason(pm):
    # The stop was trailed to 2505 before it was hit
    pm.close_position("xauusd-judas", "XAUUSD", 2505.0, 48.5, "acct-1", ctrader_id=11,
                      close_reason="Stop Loss (Trailing / Break-Even)", sl_price=2505.0, tp_price=2520.0)
    row = _row(pm)
    assert row["status"] == "closed"
    assert row["sl_price"] == 2505.0
    assert row["close_reason"] == "Stop Loss (Trailing / Break-Even)"


def test_close_from_an_older_bot_keeps_the_entry_levels(pm):
    pm.close_position("xauusd-judas", "XAUUSD", 2520.0, 99.0, "acct-1", ctrader_id=11)
    row = _row(pm)
    assert row["sl_price"] == 2490.0
    assert row["tp_price"] == 2520.0
    assert row["close_reason"] is None


# --- report endpoint -------------------------------------------------------------------------

def test_report_endpoint_round_trips_levels_and_reason():
    bot = "test-sltp-report-bot"
    account = "990011"
    base = {"bot_id": bot, "symbol": "EURUSD", "account_number": account, "account_type": "demo",
            "ctrader_id": 5501}
    assert client.post("/portfolio/report", json={
        **base, "action": "open", "side": "Sell", "volume": 0.2, "entry_price": 1.1000,
        "sl_pips": 20, "tp_pips": 40, "sl_price": 1.1020, "tp_price": 1.0960,
    }).json()["status"] == "success"
    assert client.post("/portfolio/report", json={
        **base, "action": "close", "exit_price": 1.0960, "pnl": 80.0,
        "close_reason": "Take Profit", "sl_price": 1.1000, "tp_price": 1.0960,
    }).json()["status"] == "success"

    from app.dashboard import get_trade_history
    from app.db import get_db_connection

    try:
        trade = next(t for t in get_trade_history(account_id=f"demo-{account}", page_size=50)["items"]
                     if t["bot_id"] == bot)
        assert trade["close_reason"] == "Take Profit"
        assert trade["sl_price"] == 1.1000  # the stop had been moved to break-even
        assert trade["tp_price"] == 1.0960
        assert trade["sl_pips"] == 20
    finally:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM positions WHERE bot_id = ?", (bot,))
            conn.commit()
        finally:
            conn.close()


def test_flowrsi_reason_field_is_accepted_and_zero_levels_mean_none(pm):
    from app.server import _level_price

    assert _level_price(0) is None
    assert _level_price(None) is None
    assert _level_price("2490.5") == 2490.5


# --- live levels -----------------------------------------------------------------------------

def test_tick_levels_parses_both_payload_shapes():
    from app.dashboard import tick_levels

    # WebSocket tick (AiAgentBot, Judas): sl / tp / sl_pnl / tp_pnl
    assert tick_levels({"sl": 2490.0, "tp": None, "sl_pnl": -101.234, "tp_pnl": None}) == {
        "sl_price": 2490.0, "tp_price": None, "sl_pnl": -101.23, "tp_pnl": None, "no_tp": True}
    # FlowRsiBot position entry: sl_price / tp_price
    assert tick_levels({"sl_price": 1.1, "tp_price": 1.2, "sl_pnl": -5, "tp_pnl": 10})["tp_price"] == 1.2
    # A bot build without levels -> None, so the cached levels are left alone
    assert tick_levels({"pnl": 1.0, "pips": 2.0}) is None


def test_tick_levels_replace_cached_levels_and_snapshots_keep_the_estimate():
    pm = PortfolioManager.__new__(PortfolioManager)  # no DB needed for the cache
    pm._bot_positions_cache = {}

    pm.update_market_price("XAUUSD", 2500.0, 2500.3, bot_id="bot-a", account_id="acc",
                           position_data={"sl_price": 2490.0, "tp_price": 2520.0})
    pm.update_position_metrics("bot-a", 5.0, 50.0, account_id="acc",
                               levels={"sl_price": 2490.0, "tp_price": 2520.0, "sl_pnl": -100.0, "tp_pnl": 200.0})
    assert pm._bot_positions_cache["acc:bot-a"]["sl_pnl"] == -100.0

    # The next bar snapshot has no estimate; it is kept while the level has not moved...
    pm.update_market_price("XAUUSD", 2501.0, 2501.3, bot_id="bot-a", account_id="acc",
                           position_data={"sl_price": 2490.0, "tp_price": 2520.0})
    assert pm._bot_positions_cache["acc:bot-a"]["sl_pnl"] == -100.0
    # ...and dropped once the stop has been trailed, rather than shown next to the wrong price
    pm.update_market_price("XAUUSD", 2510.0, 2510.3, bot_id="bot-a", account_id="acc",
                           position_data={"sl_price": 2500.0, "tp_price": 2520.0})
    assert pm._bot_positions_cache["acc:bot-a"].get("sl_pnl") is None
    assert pm._bot_positions_cache["acc:bot-a"]["tp_pnl"] == 200.0


def test_active_position_prefers_live_levels_over_entry_levels():
    from app.dashboard import _attach_live_metrics

    pos = {"side": "BUY", "entry_price": 2500.0, "sl_price": 2490.0, "tp_price": 2520.0}
    report = {"sl_price": 2500.5, "tp_price": 2520.0, "sl_pnl": 0.4, "tp_pnl": 199.0,
              "unrealized_pnl": 50.0, "unrealized_pnl_pips": 50.0, "_reported_at": time.time()}
    _attach_live_metrics(pos, report, None)
    assert pos["sl_price"] == 2500.5      # moved to break-even since entry
    assert pos["sl_pnl"] == 0.4
    assert pos["tp_pnl"] == 199.0

    # No report yet -> entry levels from the DB, no invented P&L
    pos = {"side": "BUY", "entry_price": 2500.0, "sl_price": 2490.0, "tp_price": 2520.0}
    _attach_live_metrics(pos, None, None)
    assert pos["sl_price"] == 2490.0
    assert pos["sl_pnl"] is None and pos["tp_pnl"] is None


# --- page ------------------------------------------------------------------------------------

def test_dashboard_renders_the_new_columns():
    html = client.get("/demo/dashboard").text
    assert "Close Reason" in html
    assert "function sltpCellHtml" in html
    assert "function closeReasonHtml" in html
    # the live tick handler refreshes SL/TP too, matched per bot
    assert "tick.levels" in html
    assert 'data-bot="${escapeHtml(p.bot_id' in html


# --- cBots -----------------------------------------------------------------------------------

@pytest.mark.parametrize("cs_name,raw_close", [
    ("AiAgentBot.cs", "pos.Close();"),
    ("AsianRangeJudasSweepBot.cs", "ClosePosition(pos);"),
])
def test_bot_reports_close_reason_and_levels(cs_name, raw_close):
    src = (ROOT / "cBot" / cs_name).read_text(encoding="utf-8")
    assert "ResolveCloseReason(args)" in src
    assert "close_reason = closeReason" in src
    assert "sl_price = position.StopLoss" in src
    # Every bot-initiated close goes through CloseWithReason, the only raw close call left
    assert src.count(raw_close) == 1
    assert "ClosePosition(position);" not in src


@pytest.mark.parametrize("cs_name", ["AiAgentBot.cs", "AsianRangeJudasSweepBot.cs"])
def test_tick_stream_carries_levels(cs_name):
    src = (ROOT / "cBot" / cs_name).read_text(encoding="utf-8")
    assert "sl_pnl = hasPnl ? slPnl : null" in src
    assert "slPnl = PnlAtLevel(pos, pos.StopLoss);" in src


def test_flowrsi_tick_and_close_carry_levels():
    src = (ROOT / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")
    assert "sl_pnl = p.StopLoss.HasValue" in src
    assert "exit_price = resolvedExitPrice,\n                    sl_price = slPrice," in src
