"""
Unit tests for the TMS/ORB CLOSE_ALL guard.

Regression: 2026-09-10 USDJPY BUY @153.686 (SL 22.1p) was closed by the LLM with
CLOSE_ALL at -10 pips citing "exit_short=true (tdi_cross_up)" - the wrong-side exit
flag for a long. The guard must downgrade such an exit to HOLD while still allowing
protective exits after real drawdown or genuine side-correct reversal signals.
"""

from app.server import (
    MarketSnapshot,
    PositionInfo,
    TmsSignals,
    validate_tms_close_decision,
    TMS_CLOSE_MIN_ADVERSE_SL_RATIO,
)


def _snapshot(side: str = "BUY", entry: float = 153.686, sl: float = 153.465,
              bid: float = 153.598, ask: float = 153.600,
              exit_long: bool = False, exit_short: bool = False) -> MarketSnapshot:
    return MarketSnapshot(
        bot_id="usdjpy_m15",
        symbol="USDJPY",
        timeframe="Minute15",
        ask=ask,
        bid=bid,
        position=PositionInfo(side=side, entry_price=entry, sl_price=sl, sl=sl),
        chart_tms=TmsSignals(exit_long=exit_long, exit_short=exit_short),
    )


def test_wrong_side_flag_small_drawdown_rejected():
    """The 2026-09-10 case: BUY closed on exit_short (cross up) at -10 pips / 22p SL."""
    snap = _snapshot(exit_short=True)
    reason = validate_tms_close_decision(snap, {"action": "CLOSE_ALL"})
    assert reason is not None
    assert "no BUY-side exit signal" in reason
    assert "exit_long=False" in reason


def test_correct_side_flag_allowed():
    snap = _snapshot(exit_long=True)
    assert validate_tms_close_decision(snap, {"action": "CLOSE_ALL"}) is None


def test_adverse_excursion_allowed():
    """Loss beyond the protective threshold may exit regardless of flags."""
    loss = 22.1 * TMS_CLOSE_MIN_ADVERSE_SL_RATIO + 1.0  # pips down in price terms
    snap = _snapshot(bid=153.686 - loss * 0.01, ask=153.687 - loss * 0.01)
    assert validate_tms_close_decision(snap, {"action": "CLOSE_ALL"}) is None


def test_profit_taking_allowed():
    """>= 1:1 R profit may exit to lock gains."""
    snap = _snapshot(bid=153.686 + 22.1 * 0.01, ask=153.687 + 22.1 * 0.01)
    assert validate_tms_close_decision(snap, {"action": "CLOSE_ALL"}) is None


def test_sell_wrong_side_flag_rejected_and_correct_allowed():
    sell_snap = _snapshot(side="SELL", entry=153.686, sl=153.907,
                          bid=153.698, ask=153.700, exit_long=True)
    reason = validate_tms_close_decision(sell_snap, {"action": "CLOSE_ALL"})
    assert reason is not None and "no SELL-side exit signal" in reason

    ok_snap = _snapshot(side="SELL", entry=153.686, sl=153.907,
                        bid=153.698, ask=153.700, exit_short=True)
    assert validate_tms_close_decision(ok_snap, {"action": "CLOSE_ALL"}) is None


def test_flat_position_not_guarded():
    snap = _snapshot()
    snap.position = None
    assert validate_tms_close_decision(snap, {"action": "CLOSE_ALL"}) is None


def test_close_all_guard_wired_through_trade_endpoint():
    """End-to-end: a wrong-side CLOSE_ALL from the LLM must come back as HOLD."""
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    import app.server
    from app.server import app as fastapi_app

    client = TestClient(fastapi_app)
    payload = {
        "request_id": "tms_close_1",
        "bot_id": "usdjpy_m15",
        "symbol": "USDJPY",
        "timeframe": "Minute15",
        "ask": 153.600,
        "bid": 153.598,
        "tms": {"bias": "BULLISH", "bars_since_cross": 1, "exit_long": False, "exit_short": False},
        "chart_tms": {"exit_long": False, "exit_short": True},
        "position": {
            "side": "BUY",
            "entry_price": 153.686,
            "sl_price": 153.465,
            "tp_price": 153.981,
            "unrealized_pnl_pips": -8.8,
        },
        "account_number": "999001",
        "account_type": "demo",
        "account_balance": 588.0,
        "account_equity": 588.0,
    }
    close_json = (
        '{"action": "CLOSE_ALL", "volume_lots": 0.01, "sl_pips": 0, "tp_pips": 0, '
        '"confidence": 80.0, "reason": "exit_short=true (tdi_cross_up) shifted against BUY"}'
    )
    with patch.object(app.server.llm_client, "chat", new=AsyncMock(return_value=close_json)):
        res = client.post("/trade", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["action"] == "HOLD"
    assert "[CLOSE Guard]" in data["reason"]
