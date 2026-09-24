"""
Regression guard for daily-check 2026-09-24 #1: lost open reports ran two trades off the books.

    14:15:16 VN  us30-all-flowrsi  #675558107  "Failed to report position open"
    15:13:48 VN  "Close position ignored"  (no row with that ctrader_id)        -11.62 $
    14:45:37 VN  eurjpy-london     #675569508  same story                        -1.91 $

The bots now retry /portfolio/report, so the server must take a replayed open, partial
close or close without booking the trade twice. A replay changes nothing and returns False,
like any report that matched no position, so no second TRADE_* event is broadcast (the bot
only looks at the HTTP status, which stays 200). A close report whose open never arrived
carries enough of the position (side, volume, entry) to rebuild the row, so the P&L lands
in the daily loss, the leaderboard and the dashboard instead of being dropped.
"""

from fastapi.testclient import TestClient

from app.portfolio import PortfolioManager

ACCT = "acct-1"
COLUMNS = ["status", "volume", "pnl", "side", "entry_price", "entry_time",
           "close_reason", "sl_pips", "initial_volume"]


def _pm(tmp_path):
    return PortfolioManager(db_path=str(tmp_path / "reports.db"))


def _rows(pm, ctrader_id):
    conn = pm._get_conn()
    try:
        cur = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM positions WHERE ctrader_id = ?", (ctrader_id,))
        return [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _open(pm, cid=111, volume=0.1):
    return pm.register_position(bot_id="bot-a", symbol="US30", side="Buy", volume=volume,
                                entry_price=51404.8, sl_pips=1151.6, tp_pips=1659.5,
                                account_id=ACCT, ctrader_id=cid)


def _close(pm, cid=111, **extra):
    return pm.close_position(bot_id="bot-a", symbol="US30", exit_price=51288.6, pnl=-11.62,
                             account_id=ACCT, ctrader_id=cid, close_reason="StopLoss", **extra)


def test_replayed_open_report_does_not_create_a_second_row(tmp_path):
    pm = _pm(tmp_path)
    assert _open(pm)
    assert not _open(pm)
    assert len(_rows(pm, 111)) == 1


def test_replayed_partial_close_is_banked_once(tmp_path):
    pm = _pm(tmp_path)
    _open(pm, volume=0.2)
    results = [
        pm.record_partial_close(bot_id="bot-a", symbol="US30", remaining_volume=0.1,
                                realized_pnl=5.0, account_id=ACCT, ctrader_id=111)
        for _ in range(2)
    ]
    assert results == [True, False]
    row = _rows(pm, 111)[0]
    assert row["volume"] == 0.1
    assert row["pnl"] == 5.0


def test_second_partial_close_still_banks(tmp_path):
    pm = _pm(tmp_path)
    _open(pm, volume=0.3)
    assert pm.record_partial_close(bot_id="bot-a", symbol="US30", remaining_volume=0.2,
                                   realized_pnl=5.0, account_id=ACCT, ctrader_id=111)
    assert pm.record_partial_close(bot_id="bot-a", symbol="US30", remaining_volume=0.1,
                                   realized_pnl=4.0, account_id=ACCT, ctrader_id=111)
    row = _rows(pm, 111)[0]
    assert row["volume"] == 0.1
    assert row["pnl"] == 9.0


def test_replayed_close_report_changes_nothing(tmp_path):
    pm = _pm(tmp_path)
    _open(pm)
    assert _close(pm)
    assert not _close(pm, side="Buy", volume=0.1, entry_price=51404.8)
    rows = _rows(pm, 111)
    assert len(rows) == 1
    assert rows[0]["pnl"] == -11.62


def test_close_of_an_unknown_position_rebuilds_the_row(tmp_path):
    pm = _pm(tmp_path)
    assert pm.close_position(
        bot_id="bot-a", symbol="US30", exit_price=51288.6, pnl=-11.62, account_id=ACCT,
        ctrader_id=675558107, close_reason="StopLoss (Initial Structural Reversal)",
        side="Buy", volume=0.1, entry_price=51404.8,
        entry_time="2026-09-24T07:15:14.1234567Z", sl_pips=1151.6)
    row = _rows(pm, 675558107)[0]
    assert row["status"] == "closed"
    assert row["pnl"] == -11.62
    assert row["side"] == "Buy"
    assert row["entry_price"] == 51404.8
    assert row["volume"] == 0.1
    assert row["initial_volume"] == 0.1
    assert str(row["entry_time"]).startswith("2026-09-24 07:15:14")
    assert row["sl_pips"] == 1151.6
    assert row["close_reason"] == "StopLoss (Initial Structural Reversal) (recovered: open report lost)"

    # The same close arriving again, then the open report of that trade arriving late,
    # must neither double the P&L nor reopen the position.
    assert not pm.close_position(
        bot_id="bot-a", symbol="US30", exit_price=51288.6, pnl=-11.62, account_id=ACCT,
        ctrader_id=675558107, close_reason="StopLoss (Initial Structural Reversal)",
        side="Buy", volume=0.1, entry_price=51404.8)
    assert not _open(pm, cid=675558107)
    rows = _rows(pm, 675558107)
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"


def test_close_without_entry_fields_is_still_ignored(tmp_path):
    """An older .algo sends no side/entry on close: nothing to rebuild the row from."""
    pm = _pm(tmp_path)
    assert not _close(pm, cid=999)
    assert _rows(pm, 999) == []


def test_close_of_a_row_held_by_another_bot_is_not_taken_as_a_replay(tmp_path):
    pm = _pm(tmp_path)
    _open(pm, cid=111)
    assert not pm.close_position(bot_id="bot-b", symbol="US30", exit_price=1.0, pnl=-1.0,
                                 account_id=ACCT, ctrader_id=111, side="Buy", volume=0.1,
                                 entry_price=51404.8)
    rows = _rows(pm, 111)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"


def test_report_endpoint_forwards_entry_fields_on_close(tmp_path, monkeypatch):
    import app.server as server_mod

    pm = _pm(tmp_path)
    monkeypatch.setattr(server_mod, "portfolio_manager", pm)
    resp = TestClient(server_mod.app).post("/portfolio/report", json={
        "action": "close", "bot_id": "bot-a", "symbol": "EURJPY", "ctrader_id": 675569508,
        "side": "Sell", "volume": 0.01, "entry_price": 180.191,
        "entry_time": "2026-09-24T07:45:37Z", "sl_pips": 28.6,
        "exit_price": 180.494, "pnl": -1.91, "close_reason": "StopLoss",
        "account_number": "10115236", "account_type": "demo",
    })
    assert resp.json()["status"] == "success"
    row = _rows(pm, 675569508)[0]
    assert row["status"] == "closed"
    assert row["side"] == "Sell"
    assert row["sl_pips"] == 28.6
