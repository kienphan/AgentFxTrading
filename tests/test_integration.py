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

def test_eth_crypto_classification():
    from app.server import evaluate_cycle_gate, MarketSnapshot, TmsSignals, OrbData
    snap_eth = MarketSnapshot(
        symbol="ETHUSD",
        timeframe="Minute15",
        ask=2400.5,
        bid=2400.0,
        atr_pips=1500.0,
        tms=TmsSignals(bias="BULLISH"),
        orb=OrbData(
            or_complete=True,
            breakout_direction="up",
            breakout_distance_pips=1200.0,  # $12 breakout on ETH is 1200 pips
            is_decisive=True,
            in_entry_window=True,
            bars_since_breakout=1
        )
    )
    decision = evaluate_cycle_gate(snap_eth)
    # With ETH in crypto, 1200 pips is well within max 15000.0p limit (unlike Forex 60p limit)
    # It should not be blocked by overextension
    if decision is not None:
        assert "Breakout overextended" not in decision.reason

def test_exhaustion_breakout_guard_ustec():
    from app.server import evaluate_cycle_gate, MarketSnapshot, TmsSignals, OrbData

    # Case 1: USTEC on 2026-09-04 scenario:
    # Breakout distance = 695p, decisive, in entry window, but NO bounce.
    # 695p > max_direct_breakout_dist (450p) -> Must be BLOCKED as exhaustion breakout!
    snap_ustec_exhausted = MarketSnapshot(
        symbol="USTEC",
        timeframe="Minute15",
        ask=29507.5,
        bid=29507.3,
        atr_pips=500.0,
        tms=TmsSignals(bias="BEARISH"),
        chart_tms=TmsSignals(bias="BEARISH", tdi_bounce_bear=False, price_below_ema=True),
        orb=OrbData(
            or_complete=True,
            breakout_direction="down",
            breakout_distance_pips=695.0,
            is_decisive=True,
            in_entry_window=True,
            bars_since_breakout=0
        )
    )
    decision1 = evaluate_cycle_gate(snap_ustec_exhausted)
    assert decision1 is not None
    assert decision1.action == "HOLD"
    assert "Breakout candle exhausted" in decision1.reason

    # Case 2: Clean, fresh breakout (200p <= 450p) -> Model 1 allowed (returns None for LLM processing)
    snap_ustec_fresh = MarketSnapshot(
        symbol="USTEC",
        timeframe="Minute15",
        ask=29557.5,
        bid=29557.3,
        atr_pips=500.0,
        tms=TmsSignals(bias="BEARISH"),
        chart_tms=TmsSignals(bias="BEARISH", tdi_bounce_bear=False, price_below_ema=True),
        orb=OrbData(
            or_complete=True,
            breakout_direction="down",
            breakout_distance_pips=200.0,
            is_decisive=True,
            in_entry_window=True,
            bars_since_breakout=1
        )
    )
    decision2 = evaluate_cycle_gate(snap_ustec_fresh)
    assert decision2 is None  # Allowed to proceed to LLM!

    # Case 3: Retest + TDI Bounce (Model 2) with distance = 695p -> Allowed!
    snap_ustec_bounce = MarketSnapshot(
        symbol="USTEC",
        timeframe="Minute15",
        ask=29507.5,
        bid=29507.3,
        atr_pips=500.0,
        tms=TmsSignals(bias="BEARISH"),
        chart_tms=TmsSignals(bias="BEARISH", tdi_bounce_bear=True, price_below_ema=True),
        orb=OrbData(
            or_complete=True,
            breakout_direction="down",
            breakout_distance_pips=695.0,
            is_decisive=True,
            in_entry_window=True,
            bars_since_breakout=3
        )
    )
    decision3 = evaluate_cycle_gate(snap_ustec_bounce)
    assert decision3 is None  # Model 2 Bounce allowed!

    # Case 4: Extreme overextension (> 1200p) even with bounce -> Blocked!
    snap_ustec_overextended = MarketSnapshot(
        symbol="USTEC",
        timeframe="Minute15",
        ask=29400.0,
        bid=29399.0,
        atr_pips=500.0,
        tms=TmsSignals(bias="BEARISH"),
        chart_tms=TmsSignals(bias="BEARISH", tdi_bounce_bear=True, price_below_ema=True),
        orb=OrbData(
            or_complete=True,
            breakout_direction="down",
            breakout_distance_pips=1350.0,
            is_decisive=True,
            in_entry_window=True,
            bars_since_breakout=3
        )
    )
    decision4 = evaluate_cycle_gate(snap_ustec_overextended)
    assert decision4 is not None
    assert decision4.action == "HOLD"
    assert "Breakout overextended" in decision4.reason

def test_chart_tms_exit_signal_cycle_gate():
    from app.server import evaluate_cycle_gate, MarketSnapshot, PositionInfo, TmsSignals

    # Holding SELL position, chart_tms signals exit_short=True (e.g. tdi_cross_up)
    snap_pos = MarketSnapshot(
        symbol="USTEC",
        timeframe="Minute15",
        ask=29566.5,
        bid=29566.5,
        position=PositionInfo(
            side="SELL",
            entry_price=29503.4,
            unrealized_pnl=-5.68,
            unrealized_pnl_pips=-631.0,
            mfe_pips=55.0,
            giveback_pips=686.0
        ),
        tms=TmsSignals(bias="BEARISH", exit_short=False),  # Macro TMS doesn't have exit
        chart_tms=TmsSignals(bias="BEARISH", exit_short=True, exit_reason="tdi_cross_up")
    )
    decision = evaluate_cycle_gate(snap_pos)
    assert decision is not None
    assert decision.action == "CLOSE_ALL"
    assert "TMS exit signal triggered (tdi_cross_up)" in decision.reason

def test_format_price_and_prompt_precision():
    from app.server import format_price, build_judas_sweep_user_prompt, MarketSnapshot, BarData, StrategyData
    assert format_price(1.35034, "GBPUSD") == "1.35034"
    assert format_price(1.15786, "EURUSD") == "1.15786"
    assert format_price(215.340, "GBPJPY") == "215.340"
    assert format_price(184.978, "EURJPY") == "184.978"
    assert format_price(4321.72, "XAUUSD") == "4321.72"
    assert format_price(29078.30, "USTEC") == "29078.30"

    snap = MarketSnapshot(
        symbol="GBPUSD",
        timeframe="Minute15",
        ask=1.35036,
        bid=1.35034,
        bars=[BarData(open=1.35034, high=1.35080, low=1.35010, close=1.35070, volume=100.0)],
        strategy=StrategyData(atr=0.0015, asian_high=1.35167, asian_low=1.35006, asian_range_pips=16.0)
    )
    prompt = build_judas_sweep_user_prompt(snap)
    # Bar should contain 5 decimals, not truncated 1.35
    assert "O=1.35034" in prompt
    # ATR should be converted to pips (15.0 pips) rather than 0 pips
    assert "ATR (14 Volatility): 15.0 pips" in prompt

def test_cbot_event_telemetry():
    payload = {
        "bot_id": "eurjpy_m15",
        "account_number": "10101649",
        "event_type": "GUARDRAIL_BLOCKED",
        "message": "[BreakoutOverextended] 64.4p > 60.0p threshold"
    }
    res = client.post("/api/cbot_event", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

def test_judas_gate_forex_asian_range_19p():
    from app.server import evaluate_judas_sweep_gate, MarketSnapshot, StrategyData
    snap_eur = MarketSnapshot(
        symbol="EURUSD",
        timeframe="Minute15",
        ask=1.15927,
        bid=1.15928,
        strategy=StrategyData(
            asian_high=1.15932,
            asian_low=1.15737,
            asian_range_pips=19.0,  # 19 pips on EURUSD should be accepted now (valid: 12-100p)
            killzone_session="New York Overlap Killzone",
            bias_direction="SELL",
            traditional_signal="JUDAS_SWEEP_SELL",
            signal_window_bars=1
        )
    )
    decision = evaluate_judas_sweep_gate(snap_eur)
    # Should NOT be gated as abnormal Asian Range
    if decision is not None:
        assert "Asian Range width abnormal" not in decision.reason

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
        test_eth_crypto_classification()
        print("✓ test_eth_crypto_classification PASSED")
        test_format_price_and_prompt_precision()
        print("✓ test_format_price_and_prompt_precision PASSED")
        test_cbot_event_telemetry()
        print("✓ test_cbot_event_telemetry PASSED")
        test_judas_gate_forex_asian_range_19p()
        print("✓ test_judas_gate_forex_asian_range_19p PASSED")
        print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
    finally:
        cleanup_test_data()
