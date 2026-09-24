import pytest
from app.server import (
    MarketSnapshot,
    StrategyData,
    BarData,
    is_flow_rsi_bot,
    trade_decision,
)

def test_is_flow_rsi_bot():
    snap1 = MarketSnapshot(
        bot_id="FlowRSI-EURUSD",
        symbol="EURUSD",
        timeframe="Minute15",
        ask=1.0850,
        bid=1.0849,
    )
    assert is_flow_rsi_bot(snap1) is True

    snap2 = MarketSnapshot(
        bot_id="custom_bot",
        symbol="EURUSD",
        timeframe="Minute15",
        ask=1.0850,
        bid=1.0849,
        fast_rsi=35.0,
        slow_rsi=40.0,
    )
    assert is_flow_rsi_bot(snap2) is True

    snap3 = MarketSnapshot(
        bot_id="usdjpy_m15",
        symbol="USDJPY",
        timeframe="Minute15",
        ask=150.0,
        bid=149.98,
    )
    assert is_flow_rsi_bot(snap3) is False

@pytest.mark.anyio
async def test_flow_rsi_snapshot_routing(monkeypatch):
    import json
    mock_result = json.dumps({
        "action": "BUY",
        "volume_lots": 0.0,
        "sl_pips": 20.0,
        "tp_pips": 30.0,
        "confidence": 85.0,
        "reason": "Nested RSI Golden Cross in Discount FVG Zone"
    })
    from unittest.mock import AsyncMock
    mock_chat = AsyncMock(return_value=mock_result)
    import app.server as server_mod
    monkeypatch.setattr(server_mod.llm_client, "chat", mock_chat)

    snap = MarketSnapshot(
        request_id="req-flow-1",
        bot_id="FlowRSI-EURUSD",
        symbol="EURUSD",
        timeframe="Minute15",
        ask=1.0850,
        bid=1.0849,
        fast_rsi=30.0,
        slow_rsi=35.0,
        rsi_cross_signal="Bullish_Cross",
        in_fvg_zone=True,
        fvg_type="Bullish_FVG",
        is_discount=True,
        candidate_action="BUY",
        technical_sl_price=1.0820,
        technical_tp_price=1.0895,
        technical_risk_reward=1.5,
        strategy=StrategyData(
            recent_high=1.0895,
            recent_low=1.0820,
            bias_direction="BUY"
        ),
        bars=[BarData(time="2026-09-17 15:00:00")]
    )
    decision = await trade_decision(snap)
    assert decision.action == "BUY"
    assert decision.confidence == 85.0
    assert "Nested RSI" in decision.reason

    # Verify prompt received by llm_client contains SMC Swing Structure
    messages = mock_chat.call_args[0][0]
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "SMC Swing Structure" in user_content
    assert "High=1.08950 (Resistance/BSL)" in user_content
    assert "Low=1.08200 (Support/SSL)" in user_content
    assert "[Struct: " in user_content
