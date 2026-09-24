"""
Tests for FlowRSI TMS Cross-Lock Directional Bias Gatekeeper and Swing Market Structure.
Ensures:
1. TMS cross lock is properly tracked, cached, and computed from bars.
2. True fractal swing market structure is detected (fixing the false BULLISH_HH_HL bug).
3. Pre-LLM gatekeeper blocks counter-trend entries (BUY when BEARISH, SELL when BULLISH).
4. Post-LLM guard intercepts and overrides any counter-trend hallucinations.
5. /api/tms/cross-locks endpoint returns live status.
"""

import pytest
import time
import json
from unittest.mock import AsyncMock
from app.server import (
    MarketSnapshot,
    BarData,
    TmsSignals,
    trade_decision,
    update_tms_cross_lock,
    get_or_compute_tms_cross_lock,
    compute_tms_cross_lock_from_bars,
    detect_swing_market_structure,
    _TMS_CROSS_LOCK_REGISTRY,
    app,
)
from fastapi.testclient import TestClient

client = TestClient(app)


def _mock_llm(monkeypatch, payload: dict):
    import app.server as server_mod
    monkeypatch.setattr(
        server_mod.llm_client, "chat", AsyncMock(return_value=json.dumps(payload))
    )


def test_update_and_get_tms_cross_lock():
    update_tms_cross_lock("TESTUSD", "BEARISH", bars_since_cross=5, source="Test Bot")
    assert "TESTUSD" in _TMS_CROSS_LOCK_REGISTRY
    assert _TMS_CROSS_LOCK_REGISTRY["TESTUSD"]["bias"] == "BEARISH"
    assert _TMS_CROSS_LOCK_REGISTRY["TESTUSD"]["bars_since_cross"] == 5

    snap = MarketSnapshot(
        bot_id="FlowRSI-TESTUSD-M15",
        symbol="TESTUSD",
        timeframe="Minute15",
        bid=1.2000,
        ask=1.2002,
    )
    bias, age, src = get_or_compute_tms_cross_lock(snap)
    assert bias == "BEARISH"
    assert age == 5
    assert src == "Test Bot"


def test_detect_swing_market_structure_bearish_trend():
    """Bearish trending waves must be detected as BEARISH_LH_LL, not BULLISH."""
    waves = []
    p = 2000.0
    # Create 3 distinct lower highs and lower lows
    for i in range(30):
        step = -6.0 if (i % 6 < 4) else +3.0
        p += step
        waves.append(BarData(
            time=f"2026-09-24T{i:02d}:00:00Z",
            open=p,
            high=p + 2.0,
            low=p - 2.0,
            close=p + 1.0,
            volume=100.0
        ))

    # newest first
    bars_newest = list(reversed(waves))
    struct, high, low = detect_swing_market_structure(bars_newest, cur_p=p)
    assert struct == "BEARISH_LH_LL"
    assert high > low


def test_detect_swing_market_structure_bullish_trend():
    """Bullish trending waves must be detected as BULLISH_HH_HL."""
    waves = []
    p = 2000.0
    for i in range(30):
        step = +6.0 if (i % 6 < 4) else -3.0
        p += step
        waves.append(BarData(
            time=f"2026-09-24T{i:02d}:00:00Z",
            open=p,
            high=p + 2.0,
            low=p - 2.0,
            close=p + 1.0,
            volume=100.0
        ))

    bars_newest = list(reversed(waves))
    struct, high, low = detect_swing_market_structure(bars_newest, cur_p=p)
    assert struct == "BULLISH_HH_HL"
    assert high > low


@pytest.mark.anyio
async def test_flowrsi_pre_llm_gate_blocks_counter_trend_buy_when_bearish(monkeypatch):
    """When Macro TMS is locked BEARISH, FlowRSI BUY candidate must be gated HOLD immediately."""
    called_llm = False
    async def _fail_if_called(*args, **kwargs):
        nonlocal called_llm
        called_llm = True
        return json.dumps({"action": "BUY"})

    import app.server as server_mod
    monkeypatch.setattr(server_mod.llm_client, "chat", _fail_if_called)

    # Set authoritative TMS cross lock to BEARISH
    update_tms_cross_lock("XAUUSD", "BEARISH", bars_since_cross=14, source="cbot-xauusd (Macro TMS)")

    snap = MarketSnapshot(
        bot_id="FlowRSI-XAUUSD-M5",
        symbol="XAUUSD",
        timeframe="Minute5",
        bid=4285.0,
        ask=4285.5,
        fast_rsi=35.0,
        slow_rsi=32.0,
        rsi_cross_signal="Bullish_Cross",
        candidate_action="BUY",
        technical_sl_price=4275.0,
        technical_tp_price=4300.0,
        is_discount=True,
    )

    decision = await trade_decision(snap)
    assert decision.action == "HOLD"
    assert "TMS Cross-Lock Gatekeeper" in decision.reason
    assert "BEARISH" in decision.reason
    assert not called_llm, "LLM must NOT be called when pre-LLM gate intercepts counter-trend entry"


@pytest.mark.anyio
async def test_flowrsi_pre_llm_gate_blocks_counter_trend_sell_when_bullish(monkeypatch):
    """When Macro TMS is locked BULLISH, FlowRSI SELL candidate must be gated HOLD immediately."""
    called_llm = False
    async def _fail_if_called(*args, **kwargs):
        nonlocal called_llm
        called_llm = True
        return json.dumps({"action": "SELL"})

    import app.server as server_mod
    monkeypatch.setattr(server_mod.llm_client, "chat", _fail_if_called)

    # Set authoritative TMS cross lock to BULLISH
    update_tms_cross_lock("XAUUSD", "BULLISH", bars_since_cross=7, source="cbot-xauusd (Macro TMS)")

    snap = MarketSnapshot(
        bot_id="FlowRSI-XAUUSD-M5",
        symbol="XAUUSD",
        timeframe="Minute5",
        bid=4350.0,
        ask=4350.5,
        fast_rsi=65.0,
        slow_rsi=68.0,
        rsi_cross_signal="Bearish_Cross",
        candidate_action="SELL",
        technical_sl_price=4360.0,
        technical_tp_price=4335.0,
        is_premium=True,
    )

    decision = await trade_decision(snap)
    assert decision.action == "HOLD"
    assert "TMS Cross-Lock Gatekeeper" in decision.reason
    assert "BULLISH" in decision.reason
    assert not called_llm


@pytest.mark.anyio
async def test_flowrsi_allows_trend_aligned_entry(monkeypatch):
    """When FlowRSI candidate aligns with Macro TMS, it proceeds to LLM evaluation."""
    _mock_llm(monkeypatch, {
        "action": "BUY",
        "confidence": 85.0,
        "reason": "Strong confluence aligned with trend",
    })

    # Macro TMS is BULLISH, FlowRSI proposes BUY
    update_tms_cross_lock("GBPUSD", "BULLISH", bars_since_cross=3, source="Macro TMS")

    snap = MarketSnapshot(
        bot_id="FlowRSI-GBPUSD-M15",
        symbol="GBPUSD",
        timeframe="Minute15",
        bid=1.3300,
        ask=1.3301,
        fast_rsi=45.0,
        slow_rsi=40.0,
        rsi_cross_signal="Bullish_Cross",
        candidate_action="BUY",
        technical_sl_price=1.3280,
        technical_tp_price=1.3330,
        is_discount=True,
    )

    decision = await trade_decision(snap)
    assert decision.action == "BUY"
    assert decision.confidence == 85.0


@pytest.mark.anyio
async def test_flowrsi_post_llm_guard_overrides_hallucinated_counter_trend(monkeypatch):
    """If the LLM hallucinates BUY when TMS is BEARISH (e.g. candidate was NONE/unspecified), guard overrides to HOLD."""
    _mock_llm(monkeypatch, {
        "action": "BUY",
        "confidence": 90.0,
        "reason": "Model decided to buy despite everything",
    })

    update_tms_cross_lock("USDJPY", "BEARISH", bars_since_cross=10, source="Macro TMS")

    snap = MarketSnapshot(
        bot_id="FlowRSI-USDJPY-M15",
        symbol="USDJPY",
        timeframe="Minute15",
        bid=155.0,
        ask=155.02,
        fast_rsi=30.0,
        slow_rsi=28.0,
        rsi_cross_signal="Bullish_Cross",
        candidate_action="MANAGE_ONLY",  # Passes pre-LLM candidate gate
        technical_sl_price=154.5,
        technical_tp_price=156.0,
    )

    decision = await trade_decision(snap)
    assert decision.action == "HOLD"
    assert "TMS Cross-Lock Guard" in decision.reason
    assert "BEARISH" in decision.reason


def test_api_tms_cross_locks_endpoint():
    update_tms_cross_lock("EURUSD", "BULLISH", bars_since_cross=2, source="Test")
    resp = client.get("/api/tms/cross-locks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "EURUSD" in data["cross_locks"]
    assert data["cross_locks"]["EURUSD"]["bias"] == "BULLISH"
