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

if __name__ == "__main__":
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
    print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
