"""The analyst proposes an order type. The gate keeps capital protection."""

import json
import math
import pytest
from app.mtf_agent import (
    BarOHLC, MtfSnapshot, approve, compute_regime, parse_llm_decision,
)


def _wave_bars(mid=1.3350, amp=0.0050, n=40, period=20, body=0.0005):
    return [
        BarOHLC(t=str(i), o=mid + amp * math.sin(2 * math.pi * i / period),
                h=mid + amp * math.sin(2 * math.pi * i / period) + body / 2,
                l=mid + amp * math.sin(2 * math.pi * i / period) - body / 2,
                c=mid + amp * math.sin(2 * math.pi * i / period))
        for i in range(n)
    ]


def _trend_bars(start=1.3300, step=0.0005, n=40):
    out = []
    for i in range(n):
        c = start + step * i
        out.append(BarOHLC(t=str(i), o=c, h=c + 0.0002, l=c - 0.0002, c=c))
    return out


def _chop_bars(mid=1.3350, body=0.0020, swing=0.0002, n=40):
    out = []
    for i in range(n):
        c = mid + (swing if i % 2 else -swing)
        out.append(BarOHLC(t=str(i), o=c, h=c + body, l=c - body, c=c))
    return out


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


# --- a market order is judged at the price it will fill at, not at the planned entry -------------

def test_buy_market_is_refused_when_the_market_is_already_past_the_stop():
    # The analyst planned 4266/4250/4300 but the book has fallen to 4245: the stop measured from the
    # fill would sit below the trade's own invalidation level.
    out = approve(_proposal("BUY_MARKET", 4266.0, 4250.0, 4300.0), _snap(bid=4245.0, ask=4245.4))
    assert out.approved is False
    assert out.decision.action == "WAIT"
    assert "past the stop" in out.gate_reason


def test_sell_market_is_refused_when_the_market_is_already_past_the_stop():
    out = approve(_proposal("SELL_MARKET", 4284.0, 4296.0, 4250.0), _snap(bid=4299.0, ask=4299.4))
    assert out.approved is False
    assert "past the stop" in out.gate_reason


def test_buy_market_rr_is_measured_from_the_fill_price():
    # Gold: 1 pip is a cent. Planned 4266/4250/4300, but the ask is 4262, so the trade risks 12 and
    # targets 38 from the fill (3.17R) instead of the 34/16 the planned entry would have suggested.
    out = approve(_proposal("BUY_MARKET", 4266.0, 4250.0, 4300.0), _snap(bid=4261.6, ask=4262.0))
    assert out.approved is True
    assert out.decision.risk_reward == 3.17


def test_market_order_is_refused_when_the_fill_leaves_too_little_reward():
    # Ask 4297: reward 3 pips against 47 of risk, far under the 2.0 minimum.
    out = approve(_proposal("BUY_MARKET", 4266.0, 4250.0, 4300.0), _snap(bid=4296.6, ask=4297.0))
    assert out.approved is False
    assert "past the stop" in out.gate_reason or "RR" in out.gate_reason


def test_a_limit_far_from_the_market_is_refused_when_the_distance_cap_is_set():
    # 1 pip = $0.01 on gold, so the cap of 2000 pips is $20: the near order is $4 away, the far one $334.
    snap = _snap(pip_size=0.01, max_entry_distance_pips=2000.0)
    near = approve(_proposal("SELL_LIMIT", 4270.0, 4282.0, 4246.0), snap)
    far = approve(_proposal("SELL_LIMIT", 4600.0, 4612.0, 4576.0), snap)
    assert near.approved is True
    assert far.approved is False
    assert "from the market" in far.gate_reason


# --- MODIFY_PENDING_ORDER re-prices the order that is there, or is refused ------------------------

def _modify(action, entry, sl, tp, confidence=76):
    return parse_llm_decision({
        "symbol": "XAUUSD",
        "decision": {"action": action, "entry": entry, "stop_loss": sl, "take_profit": tp,
                     "confidence": confidence},
        "order_management": {"existing_order_action": "MODIFY_PENDING_ORDER"},
        "reasoning": {"main_risk": "structure"},
    })


def test_a_pending_order_can_be_modified_on_its_own_side():
    snap = _snap(has_pending=True, pending={"side": "SELL", "order_type": "LIMIT", "entry": 4284.0})
    out = approve(_modify("SELL_LIMIT", 4278.0, 4290.0, 4254.0), snap)
    assert out.approved is True
    assert out.decision.action == "MODIFY_PENDING_ORDER"


def test_a_modify_may_not_flip_the_side_of_the_pending_order():
    snap = _snap(has_pending=True, pending={"side": "SELL", "order_type": "LIMIT", "entry": 4284.0})
    out = approve(_modify("BUY_LIMIT", 4250.0, 4238.0, 4274.0), snap)
    assert out.approved is False
    assert "own side" in out.gate_reason


def test_a_modify_without_a_pending_order_is_refused():
    out = approve(_modify("SELL_LIMIT", 4278.0, 4290.0, 4254.0), _snap())
    assert out.approved is False
    assert "No pending order to modify" in out.gate_reason


def test_a_bare_modify_action_is_read_from_its_own_bracket():
    # The model may answer with MODIFY_PENDING_ORDER alone: the side comes from the stop and target,
    # and the order type is inherited from the order that is already there.
    snap = _snap(has_pending=True, pending={"side": "SELL", "order_type": "LIMIT", "entry": 4284.0})
    bare = parse_llm_decision({
        "decision": {"action": "MODIFY_PENDING_ORDER", "entry": 4278.0, "stop_loss": 4290.0,
                     "take_profit": 4254.0, "confidence": 76},
    })
    out = approve(bare, snap)
    assert out.approved is True
    assert out.decision.action == "MODIFY_PENDING_ORDER"


def test_a_bare_modify_without_a_bracket_is_refused():
    snap = _snap(has_pending=True, pending={"side": "SELL", "order_type": "LIMIT", "entry": 4284.0})
    bare = parse_llm_decision({"decision": {"action": "MODIFY_PENDING_ORDER", "confidence": 76}})
    out = approve(bare, snap)
    assert out.approved is False
    assert "bracket" in out.gate_reason


def test_minimum_lot_risk_above_the_budget_is_refused_before_approval():
    # 0.01 lot XAUUSD (1 unit) with a 1200-pip stop risks $12 at $0.01/pip, while
    # 0.2% of a $705 demo account is $1.41. The cBot would refuse it after approval;
    # the gate must refuse it with the arithmetic instead.
    snap = _snap(pip_size=0.01, equity=705.0, risk_percent=0.2,
                 min_volume_units=1.0, pip_value=0.01)
    out = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), snap)
    assert out.approved is False
    assert "Minimum lot risk $12.00" in out.gate_reason
    assert "$1.41 budget" in out.gate_reason


def test_fixed_lots_bypasses_the_percentage_budget():
    # The same order that is refused above is allowed when the cBot is explicitly told to
    # trade 0.01 lot: the operator chose the size, and the fill journals the real risk.
    snap = _snap(pip_size=0.01, equity=705.0, risk_percent=0.2, fixed_lots=0.01,
                 min_volume_units=1.0, pip_value=0.01)
    out = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), snap)
    assert out.approved is True
    assert out.decision.action == "SELL_LIMIT"


def test_regime_classifier_separates_trend_range_and_chop():
    assert compute_regime(_trend_bars())["mode"] == "TREND"
    assert compute_regime(_wave_bars())["mode"] == "RANGE"
    assert compute_regime(_chop_bars())["mode"] == "CHOP"
    assert compute_regime([])["mode"] == "UNKNOWN"


def _range_snap(**overrides):
    base = dict(
        symbol="GBPUSD", bid=1.3385, ask=1.3386, spread_pips=0.1,
        max_spread_pips=4.0, min_rr=2.0, pip_size=0.0001, m15=_wave_bars(),
    )
    base.update(overrides)
    return _snap(**base)


def test_range_regime_accepts_a_sell_at_the_upper_boundary():
    # range 1.32975-1.34025, mid 1.335; entry in the upper half, SL above
    # high + 0.3 ATR, TP between low and mid, 2.4R, within 15 pips of market.
    out = approve(_proposal("SELL_LIMIT", 1.3390, 1.3415, 1.3330), _range_snap())
    assert out.approved is True
    assert out.decision.action == "SELL_LIMIT"


def test_range_regime_rejects_a_sell_in_the_lower_half():
    out = approve(_proposal("SELL_LIMIT", 1.3340, 1.3415, 1.3330), _range_snap())
    assert out.approved is False
    assert "upper half" in out.gate_reason


def test_range_regime_rejects_a_target_outside_the_range():
    # 1.3280 is below range_low 1.32975: that is a breakout thesis, not a range fade.
    out = approve(_proposal("SELL_LIMIT", 1.3390, 1.3415, 1.3280), _range_snap())
    assert out.approved is False
    assert "between range_low and mid" in out.gate_reason


def test_range_regime_rejects_a_market_order_and_a_far_limit():
    out = approve(_proposal("SELL_MARKET", 1.3380, 1.3415, 1.3330), _range_snap())
    assert out.approved is False
    assert "LIMIT order" in out.gate_reason
    # 1.3400 is 15 pips from the bid; cap is 15 so it still passes the distance check,
    # but 1.3410 (25 pips) is beyond the cap and is refused before geometry.
    out2 = approve(_proposal("SELL_LIMIT", 1.3410, 1.3435, 1.3330), _range_snap())
    assert out2.approved is False
    assert "pip cap" in out2.gate_reason


def test_chop_regime_blocks_new_entries():
    snap = _range_snap(m15=_chop_bars())
    out = approve(_proposal("SELL_LIMIT", 1.3370, 1.3400, 1.3300), snap)
    assert out.approved is False
    assert "Regime CHOP" in out.gate_reason


def test_trend_regime_rejects_a_stop_inside_the_recent_swing():
    snap = _range_snap(
        bid=1.3440, ask=1.3441, pip_size=0.0001, m15=_trend_bars(),
    )
    out = approve(_proposal("SELL_LIMIT", 1.3450, 1.3470, 1.3400), snap)
    assert out.approved is False
    assert "swing_high" in out.gate_reason


def test_trend_regime_accepts_a_stop_beyond_the_swing():
    snap = _range_snap(
        bid=1.3440, ask=1.3441, pip_size=0.0001, m15=_trend_bars(),
    )
    # swing_high ~1.3497; 1.3510 clears it, and 120/60 pips is 2R.
    out = approve(_proposal("SELL_LIMIT", 1.3450, 1.3510, 1.3330), snap)
    assert out.approved is True


def test_a_minimum_lot_that_fits_the_budget_is_authorised():
    snap = _snap(pip_size=0.01, equity=10000.0, risk_percent=0.5,
                 min_volume_units=1.0, pip_value=0.01)
    out = approve(_proposal("SELL_LIMIT", 4284.0, 4296.0, 4250.0), snap)
    assert out.approved is True
    assert out.decision.action == "SELL_LIMIT"


@pytest.mark.anyio
async def test_decide_blocks_during_news_blackout(monkeypatch):
    import app.news_service as news_mod
    from unittest.mock import AsyncMock
    from app.mtf_agent import decide

    monkeypatch.setattr(news_mod, "is_news_blackout_active",
                        AsyncMock(return_value=(True, "US Non-Farm Payrolls", 28)))

    llm = AsyncMock(spec=["chat"])
    snap = _snap(has_position=False)
    out = await decide(snap, llm)

    assert out.approved is False
    assert out.decision.action == "WAIT"
    assert "News blackout shield active" in out.gate_reason
    assert "US Non-Farm Payrolls" in out.gate_reason
    llm.chat.assert_not_called()


@pytest.mark.anyio
async def test_decide_enriches_upcoming_news_into_prompt(monkeypatch):
    import app.news_service as news_mod
    from unittest.mock import AsyncMock
    from app.mtf_agent import decide

    monkeypatch.setattr(news_mod, "is_news_blackout_active",
                        AsyncMock(return_value=(False, "", 0)))
    monkeypatch.setattr(news_mod, "get_upcoming_symbol_events",
                        AsyncMock(return_value=["13:30 UTC: USD CPI m/m (High)"]))

    received_messages = []
    async def fake_chat(messages, **kwargs):
        received_messages.extend(messages)
        return json.dumps({
            "symbol": "XAUUSD",
            "decision": {"action": "WAIT", "confidence": 50},
            "reasoning": {"main_risk": "CPI approaching"}
        })

    llm = AsyncMock(spec=["chat"])
    llm.chat = AsyncMock(side_effect=fake_chat)

    snap = _snap(has_position=False, news=[])
    out = await decide(snap, llm)

    assert "13:30 UTC: USD CPI m/m (High)" in snap.news
    user_prompt = received_messages[1]["content"]
    assert "Supplied calendar: 13:30 UTC: USD CPI m/m (High)" in user_prompt
