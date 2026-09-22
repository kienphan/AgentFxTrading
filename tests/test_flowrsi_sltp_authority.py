"""
Regression guard: on FlowRSI the LLM was setting the stop and the target.

The documented contract is "LLM proposes, Code disposes" -- direction and timing
from the model, SL/TP and sizing from the deterministic ATR/structural engine. The
TMS prompt states it and ``AiAgentBot`` enforces it with an ATR clamp; the Judas
decision is harmonised server-side. The FlowRSI prompt (``trade_decision``) said
nothing about SL/TP at all: it handed the engine's levels over as a "Proposed
Technical Setup" and the bot then preferred whatever the model returned --

    double finalSL = decision.new_sl_price > 0 ? decision.new_sl_price : fallbackSL;

All four positions opened 2026-09-22 were stopped and targeted by the model, not
the engine. The model also emits ``sl_pips`` on its own scale rather than
``Symbol.PipSize``: ETHUSD 295.4p against the 2891.3p actually placed (10x),
BTCUSD 731.02p against 76042.4p (100x), US30 and UK100 ~10x. Harmless only while
``new_sl_price`` is present -- drop the price and ``FlowRsiBot.cs`` turns 731.02
into a $7.31 stop on BTCUSD.

Entry: strip the model's levels so the engine's fallback wins.
ADJUST: keep the model's authority over an open stop, but anchor the pips to the
price it gave, at the broker's own pip size.
"""

import json
from unittest.mock import AsyncMock

import pytest

from app.server import MarketSnapshot, PositionInfo, trade_decision

# ETHUSD as the broker reports it: pip 0.01, the spread seen on the 23:00 entry.
ETH_BID = 2743.29
ETH_ASK = 2746.25
ETH_PIP = 0.01


def _mock_llm(monkeypatch, payload: dict):
    import app.server as server_mod

    monkeypatch.setattr(
        server_mod.llm_client, "chat", AsyncMock(return_value=json.dumps(payload))
    )


def _snapshot(**overrides) -> MarketSnapshot:
    base = dict(
        request_id="req-eth-1",
        bot_id="cbot-demo-demo-ethusd-all-flowrsi",
        symbol="ETHUSD",
        timeframe="Minute15",
        ask=ETH_ASK,
        bid=ETH_BID,
        pip_size=ETH_PIP,
        fast_rsi=49.14,
        slow_rsi=50.10,
        rsi_cross_signal="Bearish_Cross",
        is_premium=True,
        candidate_action="SELL",
        technical_sl_price=2772.80,
        technical_tp_price=2701.49,
        technical_risk_reward=1.5,
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def test_snapshot_keeps_the_pip_size_the_bot_reported():
    """FlowRsiBot sends pip_size at the top level; the model dropped it silently."""
    snap = _snapshot()
    assert getattr(snap, "pip_size", None) == ETH_PIP


@pytest.mark.anyio
async def test_the_prompt_tells_the_model_the_engine_owns_entry_levels(monkeypatch):
    """
    The guard below discards the model's entry levels either way, but a prompt that
    still invites them makes every entry burn tokens on a number that is thrown away
    and logged as discarded. The TMS prompt states the contract; FlowRSI's did not.
    """
    import app.server as server_mod

    captured = {}

    async def _capture(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        return json.dumps({"action": "HOLD", "confidence": 60.0, "reason": "no setup"})

    monkeypatch.setattr(server_mod.llm_client, "chat", _capture)
    await trade_decision(_snapshot())

    system_prompt = captured["system"].lower()
    assert "sl_pips=0" in system_prompt.replace(" ", "")
    assert "engine" in system_prompt


@pytest.mark.anyio
async def test_entry_strips_the_models_levels_so_the_engine_stop_wins(monkeypatch):
    _mock_llm(monkeypatch, {
        "action": "SELL",
        "volume_lots": 0.0,
        "sl_pips": 295.4,      # the model's own scale, 10x under Symbol.PipSize
        "tp_pips": 418.0,
        "new_sl_price": 2772.83,
        "new_tp_price": 2701.49,
        "confidence": 76.0,
        "reason": "Bearish cross in premium",
    })

    decision = await trade_decision(_snapshot())

    assert decision.action == "SELL"
    assert decision.sl_pips == 0
    assert decision.tp_pips == 0
    assert not decision.new_sl_price, "entry stop must come from the engine, not the model"
    assert not decision.new_tp_price, "entry target must come from the engine, not the model"


@pytest.mark.anyio
async def test_adjust_reanchors_the_pips_to_the_price_at_the_brokers_pip_size(monkeypatch):
    _mock_llm(monkeypatch, {
        "action": "ADJUST",
        "new_sl_price": 2760.0,
        "sl_pips": 137.5,      # same 10x scale error
        "confidence": 80.0,
        "reason": "Lock in profit behind structure",
    })

    snap = _snapshot(position=PositionInfo(
        side="SELL", entry_price=2743.89, pnl=5.37, sl_price=2772.80, tp_price=2701.49,
    ))
    decision = await trade_decision(snap)

    assert decision.action == "ADJUST"
    assert decision.new_sl_price == 2760.0
    # A short is closed at the ask, which is what FlowRsiBot measures the stop from.
    assert decision.sl_pips == pytest.approx(abs(2760.0 - ETH_ASK) / ETH_PIP, rel=1e-6)


@pytest.mark.anyio
async def test_adjust_without_a_price_is_refused_rather_than_guessed(monkeypatch):
    """
    A bare sl_pips carries no scale the server can trust. Acting on it would place
    a $1.375 stop on ETHUSD; forwarding it as ADJUST would log a stop move that
    never happened. Neither is acceptable -- refuse the adjustment.
    """
    _mock_llm(monkeypatch, {
        "action": "ADJUST",
        "sl_pips": 137.5,
        "confidence": 80.0,
        "reason": "Tighten the stop",
    })

    snap = _snapshot(position=PositionInfo(
        side="SELL", entry_price=2743.89, pnl=5.37, sl_price=2772.80, tp_price=2701.49,
    ))
    decision = await trade_decision(snap)

    assert decision.action == "HOLD"
    assert decision.sl_pips == 0
