"""The analyst proposes an order type. The gate keeps capital protection."""

from app.mtf_agent import MtfSnapshot, approve, parse_llm_decision


def _snap(**overrides):
    base = dict(
        symbol="XAUUSD",
        bid=4266.0,
        ask=4266.4,
        spread_pips=4.0,
        max_spread_pips=40.0,
        min_rr=2.0,
        has_position=False,
        has_pending=False,
        is_live=False,
        daily_pnl=0.0,
        max_daily_loss=500.0,
    )
    base.update(overrides)
    return MtfSnapshot(**base)


def _proposal(action, entry, sl, tp, confidence=76, **extra):
    payload = {
        "symbol": "XAUUSD",
        "decision": {
            "action": action,
            "entry": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "confidence": confidence,
        },
        "reasoning": {"main_risk": "structure"},
    }
    payload.update(extra)
    return parse_llm_decision(payload)


def test_sell_limit_above_the_market_is_authorised():
    out = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), _snap())
    assert out.approved is True
    assert out.decision.action == "SELL_LIMIT"
    assert out.decision.order_type == "LIMIT"
    assert out.decision.side == "SELL"
    assert out.decision.risk_reward >= 2


def test_sell_market_into_a_lower_price_waits():
    out = approve(_proposal("SELL_MARKET", 4284.0, 4296.0, 4250.0), _snap())
    assert out.approved is False
    assert out.decision.action == "WAIT"


def test_sell_stop_below_support_is_authorised():
    out = approve(_proposal("SELL_STOP", 4238.0, 4252.0, 4208.0), _snap(bid=4250.0, ask=4250.4))
    assert out.approved is True
    assert out.decision.action == "SELL_STOP"


def test_weak_confidence_and_poor_rr_wait():
    weak = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0, confidence=60), _snap())
    poor = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4278.0, confidence=80), _snap())
    assert weak.approved is False
    assert "Confidence" in weak.gate_reason
    assert poor.approved is False
    assert "RR" in poor.gate_reason


def test_open_position_blocks_a_new_order_but_allows_close():
    new_order = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), _snap(has_position=True))
    close = approve(parse_llm_decision({"decision": {"action": "CLOSE_POSITION"}}), _snap(has_position=True))
    assert new_order.approved is False
    assert close.approved is True
    assert close.decision.action == "CLOSE_POSITION"


def test_existing_pending_blocks_a_second_order_but_allows_cancel():
    second = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), _snap(has_pending=True))
    cancel = approve(
        parse_llm_decision({"decision": {"action": "WAIT"}, "order_management": {"existing_order_action": "CANCEL_PENDING_ORDER"}}),
        _snap(has_pending=True),
    )
    assert second.approved is False
    assert cancel.approved is True
    assert cancel.decision.action == "CANCEL_PENDING_ORDER"


def test_garbage_and_live_account_fail_closed():
    assert parse_llm_decision("not json").decision.action == "WAIT"
    live = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), _snap(is_live=True))
    assert live.approved is False
    assert live.decision.action == "WAIT"
