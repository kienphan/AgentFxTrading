import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app as fastapi_app
import app.server

client = TestClient(fastapi_app)

def test_judas_sweep_gated():
    payload = {
        "request_id": "test_req_001",
        "bot_id": "cbot-xauusd-judas",
        "symbol": "XAUUSD",
        "timeframe": "Minute15",
        "ask": 2900.50,
        "bid": 2900.20,
        "bars": [
            {"time": "2026-09-01T07:00:00Z", "open": 2895.0, "high": 2901.0, "low": 2894.0, "close": 2900.0, "volume": 150.0}
        ],
        "strategy": {
            "tema1": 2898.0,
            "tema2": 2895.0,
            "rsi": 55.0,
            "adx": 20.0,
            "atr": 15.0,
            "recent_high": 2910.0,
            "recent_low": 2880.0,
            "asian_high": 2905.0,
            "asian_low": 2885.0,
            "asian_range_pips": 200.0,
            "killzone_session": "London Open Killzone",
            "bias_direction": "NONE",
            "traditional_signal": "NONE",
            "signal_window_bars": 0
        },
        "account_number": "123456",
        "account_type": "demo",
        "account_label": "ICMarkets-Demo",
        "account_balance": 10000.0,
        "account_equity": 10000.0
    }
    res = client.post("/trade", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "HOLD"
    assert data["request_id"] == "test_req_001"
    assert data["bot_id"] == "cbot-xauusd-judas"
    assert data["symbol"] == "XAUUSD"

def test_telemetry_tick():
    payload = {
        "bot_id": "cbot-xauusd-judas",
        "account_number": "123456",
        "symbol": "XAUUSD",
        "bid": 2901.0,
        "ask": 2901.3,
        "equity": 10250.0,
        "balance": 10000.0
    }
    res = client.post("/api/tick", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

def test_portfolio_reports():
    open_payload = {
        "bot_id": "cbot-xauusd-judas",
        "action": "open",
        "symbol": "XAUUSD",
        "side": "BUY",
        "volume": 0.05,
        "entry_price": 2900.5,
        "sl_pips": 200.0,
        "tp_pips": 400.0,
        "account_number": "123456",
        "account_balance": 10000.0,
        "account_equity": 10000.0
    }
    res_open = client.post("/portfolio/report", json=open_payload)
    assert res_open.status_code == 200
    assert res_open.json()["status"] == "success"

    close_payload = {
        "bot_id": "cbot-xauusd-judas",
        "action": "close",
        "symbol": "XAUUSD",
        "exit_price": 2904.5,
        "pnl": 200.0,
        "account_number": "123456",
        "account_balance": 10200.0,
        "account_equity": 10200.0
    }
    res_close = client.post("/portfolio/report", json=close_payload)
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "success"

def test_tms_orb_backward_compatibility():
    tms_payload = {
        "bot_id": "cbot-eurusd",
        "symbol": "EURUSD",
        "timeframe": "Minute15",
        "ask": 1.08505,
        "bid": 1.08500,
        "bars": [
            {"ha_color": "green", "tdi_green": 55.0, "tdi_red": 50.0, "stoch_k": 65.0, "stoch_d": 60.0}
        ],
        "tms": {
            "bias": "BULLISH",
            "bars_since_cross": 1,
            "cross_up": True,
            "price_above_ema": True,
            "long_entry": True
        },
        "orb": {
            "or_high": 1.08450,
            "or_low": 1.08350,
            "or_mid": 1.08400,
            "or_width": 10.0,
            "or_complete": True,
            "breakout_direction": "up",
            "breakout_price": 1.08480,
            "breakout_distance_pips": 5.5,
            "bars_since_breakout": 1,
            "in_entry_window": True,
            "is_decisive": True
        },
        "account_number": "999999",
        "account_balance": 5000.0,
        "account_equity": 5000.0
    }
    with patch.object(app.server.llm_client, 'chat', new=AsyncMock(return_value='{"action": "BUY", "volume_lots": 0.05, "sl_pips": 15, "tp_pips": 30, "reason": "Confirmed TMS Bullish & ORB breakout"}')):
        res_tms = client.post("/trade", json=tms_payload)
        assert res_tms.status_code == 200
        assert res_tms.json()["action"] == "BUY"

def test_judas_sweep_llm_active_entry():
    judas_active_payload = {
        "request_id": "test_req_002",
        "bot_id": "cbot-xauusd-judas",
        "symbol": "XAUUSD",
        "timeframe": "Minute15",
        "ask": 2900.50,
        "bid": 2900.20,
        "bars": [
            {"time": "2026-09-01T07:00:00Z", "open": 2884.0, "high": 2900.5, "low": 2883.0, "close": 2900.0, "volume": 250.0}
        ],
        "strategy": {
            "tema1": 2895.0,
            "tema2": 2890.0,
            "rsi": 58.0,
            "adx": 25.0,
            "atr": 18.0,
            "recent_high": 2915.0,
            "recent_low": 2880.0,
            "asian_high": 2905.0,
            "asian_low": 2885.0,
            "asian_range_pips": 200.0,
            "killzone_session": "London Open Killzone",
            "bias_direction": "BUY",
            "traditional_signal": "JUDAS_SWEEP_BUY",
            "signal_window_bars": 1
        },
        "account_number": "123456",
        "account_type": "demo",
        "account_label": "ICMarkets-Demo",
        "account_balance": 10000.0,
        "account_equity": 10000.0
    }
    with patch.object(app.server.llm_client, 'chat', new=AsyncMock(return_value='{"action": "BUY", "volume_lots": 0.0, "sl_pips": 200, "tp_pips": 450, "new_sl_price": 2880.5, "new_tp_price": 2905.0, "confidence": 88.5, "reason": "Bullish Judas Sweep validated with Pinbar rejection at Asian Low"}')):
        res_judas = client.post("/trade", json=judas_active_payload)
        assert res_judas.status_code == 200
        data = res_judas.json()
        assert data["action"] == "BUY"
        assert data["confidence"] == 88.5
        assert data["request_id"] == "test_req_002"
        assert data["new_sl_price"] == 2880.5

def test_cycle_gate_giveback_index_vs_forex():
    from app.server import evaluate_cycle_gate, MarketSnapshot, PositionInfo, TmsSignals

    # 1. US30 with MFE = 500p (50 pts, below 1000p threshold), giveback = 250p (50%), HA turned red
    snap_us30_small_mfe = MarketSnapshot(
        symbol="US30",
        timeframe="Minute15",
        ask=52800.0,
        bid=52795.0,
        atr_pips=800.0,
        tms=TmsSignals(bias="BULLISH", exit_long=False, exit_short=False, ha_turned_red=True),
        position=PositionInfo(
            side="BUY",
            entry_price=52750.0,
            volume_lots=0.1,
            mfe_pips=500.0,
            giveback_pips=250.0,
            unrealized_pnl=2.5,
            unrealized_pnl_pips=250.0
        )
    )
    decision = evaluate_cycle_gate(snap_us30_small_mfe)
    # Should NOT trigger CLOSE_ALL because MFE (500p) < activation_mfe (max(1.5*800, 1000) = 1200p)
    assert decision is None

    # 2. US30 with MFE = 1600p (160 pts, >= 1200p activation), giveback = 900p (56.25% >= 55%), HA turned red
    snap_us30_large_mfe = MarketSnapshot(
        symbol="US30",
        timeframe="Minute15",
        ask=52800.0,
        bid=52795.0,
        atr_pips=800.0,
        tms=TmsSignals(bias="BULLISH", exit_long=False, exit_short=False, ha_turned_red=True),
        position=PositionInfo(
            side="BUY",
            entry_price=52700.0,
            volume_lots=0.1,
            mfe_pips=1600.0,
            giveback_pips=900.0,
            unrealized_pnl=7.0,
            unrealized_pnl_pips=700.0
        )
    )
    decision_large = evaluate_cycle_gate(snap_us30_large_mfe)
    assert decision_large is not None
    assert decision_large.action == "CLOSE_ALL"
    assert "Profit lock-in triggered" in decision_large.reason

    # 3. EURUSD with MFE = 30p (>= 0.8*30 = 24p), giveback = 13p (43.3% >= 40%), HA turned red
    snap_eurusd = MarketSnapshot(
        symbol="EURUSD",
        timeframe="Minute15",
        ask=1.0850,
        bid=1.0849,
        atr_pips=30.0,
        tms=TmsSignals(bias="BULLISH", exit_long=False, exit_short=False, ha_turned_red=True),
        position=PositionInfo(
            side="BUY",
            entry_price=1.0820,
            volume_lots=0.05,
            mfe_pips=30.0,
            giveback_pips=13.0,
            unrealized_pnl=8.5,
            unrealized_pnl_pips=17.0
        )
    )
    decision_eur = evaluate_cycle_gate(snap_eurusd)
    assert decision_eur is not None
    assert decision_eur.action == "CLOSE_ALL"
    assert "Profit lock-in triggered" in decision_eur.reason

def cleanup_test_data():
    import sqlite3
    try:
        conn = sqlite3.connect(root / 'portfolio.db', timeout=5.0)
        c = conn.cursor()
        c.execute("DELETE FROM positions WHERE account_id IN ('demo-123456', 'demo-999999')")
        c.execute("DELETE FROM accounts WHERE account_id IN ('demo-123456', 'demo-999999')")
        conn.commit()
        conn.close()
    except Exception:
        pass

if __name__ == "__main__":
    try:
        test_judas_sweep_gated()
        print("✓ test_judas_sweep_gated PASSED")
        test_telemetry_tick()
        print("✓ test_telemetry_tick PASSED")
        test_portfolio_reports()
        print("✓ test_portfolio_reports PASSED")
        test_tms_orb_backward_compatibility()
        print("✓ test_tms_orb_backward_compatibility PASSED")
        test_judas_sweep_llm_active_entry()
        print("✓ test_judas_sweep_llm_active_entry PASSED")
        test_cycle_gate_giveback_index_vs_forex()
        print("✓ test_cycle_gate_giveback_index_vs_forex PASSED")
        print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
    finally:
        cleanup_test_data()
