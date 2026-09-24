"""
Tests for FlowRSI TMS Cross-Lock Directional Bias Gatekeeper and Swing Market Structure.
Ensures:
1. A bot's macro TMS cross lock is shared per (symbol, macro timeframe) until the macro bar closes.
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
    get_tms_cross_lock,
    detect_swing_market_structure,
    _TMS_CROSS_LOCK_REGISTRY,
    app,
)
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cross_lock_registry():
    _TMS_CROSS_LOCK_REGISTRY.clear()
    yield
    _TMS_CROSS_LOCK_REGISTRY.clear()


def _mock_llm(monkeypatch, payload: dict):
    import app.server as server_mod
    monkeypatch.setattr(
        server_mod.llm_client, "chat", AsyncMock(return_value=json.dumps(payload))
    )


def _flowrsi_snapshot(symbol="EURUSD", **extra):
    return MarketSnapshot(
        bot_id=f"cbot-demo-demo-{symbol.lower()}-all-flowrsi",
        symbol=symbol,
        timeframe="Minute15",
        bid=1.1000,
        ask=1.1001,
        **extra,
    )


def test_update_and_get_tms_cross_lock():
    update_tms_cross_lock("TESTUSD", "BEARISH", bars_since_cross=5, source="Test Bot")
    assert _TMS_CROSS_LOCK_REGISTRY["TESTUSD"]["Hour"]["bias"] == "BEARISH"
    assert _TMS_CROSS_LOCK_REGISTRY["TESTUSD"]["Hour"]["bars_since_cross"] == 5

    snap = MarketSnapshot(
        bot_id="FlowRSI-TESTUSD-M15",
        symbol="TESTUSD",
        timeframe="Minute15",
        bid=1.2000,
        ask=1.2002,
    )
    bias, age, src = get_tms_cross_lock(snap)
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
    assert data["cross_locks"]["EURUSD"]["Hour"]["bias"] == "BULLISH"


def test_cross_lock_expires_when_the_macro_bar_closes(monkeypatch):
    """A bot's H1 lock describes the H1 bars closed so far; once the next H1 bar
    closes it may have flipped, so it must not keep gating (it used to for 4 hours)."""
    import app.server as server_mod
    top_of_hour = 1_790_000_000 // 3600 * 3600
    monkeypatch.setattr(server_mod.time, "time", lambda: top_of_hour + 3590)
    update_tms_cross_lock("EURUSD", "BEARISH", bars_since_cross=4, source="session bot")
    assert get_tms_cross_lock(_flowrsi_snapshot())[0] == "BEARISH"

    monkeypatch.setattr(server_mod.time, "time", lambda: top_of_hour + 3610)
    assert get_tms_cross_lock(_flowrsi_snapshot())[0] == "NEUTRAL"


def test_cross_lock_is_keyed_by_macro_timeframe():
    update_tms_cross_lock("EURUSD", "BEARISH", bars_since_cross=4, source="H4 bot", timeframe="Hour4")
    assert get_tms_cross_lock(_flowrsi_snapshot(tms_timeframe="Hour"))[0] == "NEUTRAL"
    assert get_tms_cross_lock(_flowrsi_snapshot(tms_timeframe="Hour4"))[0] == "BEARISH"


@pytest.mark.anyio
async def test_trade_decision_records_lock_under_the_senders_macro_timeframe():
    snap = _flowrsi_snapshot(
        tms_timeframe="Hour4",
        tms=TmsSignals(bias="BEARISH", bars_since_cross=3),
        candidate_action="NONE",
    )
    await trade_decision(snap)
    assert _TMS_CROSS_LOCK_REGISTRY["EURUSD"]["Hour4"]["bias"] == "BEARISH"
    assert "Hour" not in _TMS_CROSS_LOCK_REGISTRY["EURUSD"]


def test_bot_own_lock_wins_over_registry():
    update_tms_cross_lock("EURUSD", "BEARISH", bars_since_cross=4, source="session bot")
    snap = _flowrsi_snapshot(tms=TmsSignals(bias="BULLISH", bars_since_cross=2))
    assert get_tms_cross_lock(snap)[:2] == ("BULLISH", 2)


def test_neutral_own_lock_falls_back_to_another_bots_lock():
    update_tms_cross_lock("EURUSD", "BEARISH", bars_since_cross=4, source="session bot")
    snap = _flowrsi_snapshot(tms=TmsSignals(bias="NEUTRAL"))
    assert get_tms_cross_lock(snap) == ("BEARISH", 4, "session bot")


def test_h1_trend_bias_does_not_stand_in_for_the_cross_lock():
    """Audit 3.1 repro: a trend_bias from multi_timeframe was cached as the lock and
    returned for hours after H1 had turned the other way."""
    def snap(h1_bias):
        return MarketSnapshot.model_validate({
            "request_id": "x", "bot_id": "cbot-demo-demo-eurusd-all-flowrsi", "symbol": "EURUSD",
            "timeframe": "Minute15", "bid": 1.1, "ask": 1.1001,
            "multi_timeframe": {"h1_tf": {"timeframe": "Hour", "trend_bias": h1_bias}},
        })

    get_tms_cross_lock(snap("BEARISH"))
    assert get_tms_cross_lock(snap("BULLISH"))[0] == "NEUTRAL"
    assert _TMS_CROSS_LOCK_REGISTRY == {}


def test_chart_bars_do_not_stand_in_for_the_macro_lock():
    """FlowRSI sends its M15 bars; a cross-lock computed from them is an M15 signal,
    not the H1 lock the gate claims to apply."""
    bars, p = [], 1.1000
    for i in range(35):
        p += 0.0006 if i % 6 < 4 else -0.0003
        bars.append(BarData(time=f"2026-09-24T00:{i:02d}:00Z", open=p - 0.0002,
                            high=p + 0.0003, low=p - 0.0004, close=p, volume=100.0))
    snap = _flowrsi_snapshot(bars=list(reversed(bars)))
    assert get_tms_cross_lock(snap)[0] == "NEUTRAL"
    assert _TMS_CROSS_LOCK_REGISTRY == {}
