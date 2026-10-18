"""D1 → H4 → H1 → M15 analyst. The model proposes an order type; this module authorises it."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AgentFxTrading")

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.llm_client import (
    JSONResponseParser, _clean_env_bool, _clean_env_float, _clean_env_int, describe_llm_error,
)

ENTRY_ACTIONS = {
    "BUY_MARKET",
    "SELL_MARKET",
    "BUY_LIMIT",
    "SELL_LIMIT",
    "BUY_STOP",
    "SELL_STOP",
}
MANAGE_ACTIONS = {"WAIT", "CANCEL_PENDING_ORDER", "HOLD_POSITION", "CLOSE_POSITION", "MODIFY_PENDING_ORDER"}
MIN_CONFIDENCE = _clean_env_float("MTF_MIN_CONFIDENCE", 70.0)

# A thinking MTF reply took ~69 s, so the cBot waits AiTimeoutSeconds (default 120 s). The server
# side is bounded the way /trade does it: without a deadline the client default (90 s x 4 attempts)
# outlives the bot, which then journals an approved order that no cBot ever received.
MTF_LLM_TIMEOUT = _clean_env_float("LLM_MTF_TIMEOUT", 40.0)
MTF_LLM_MAX_RETRIES = _clean_env_int("LLM_MTF_MAX_RETRIES", 1)
MTF_LLM_DEADLINE = _clean_env_float("LLM_MTF_DEADLINE", 105.0)
MTF_LLM_ENABLE_THINKING = _clean_env_bool("LLM_MTF_ENABLE_THINKING", False)

# Deterministic regime classifier over closed M15 bars. It is deliberately simple and
# explainable: Kaufman Efficiency Ratio says whether price travelled or oscillated, while
# range/ATR says whether the oscillation has enough room to fade.
REGIME_LOOKBACK = 20
REGIME_ATR_PERIOD = 14
REGIME_SWING_LOOKBACK = 8
REGIME_TREND_ER = 0.30
REGIME_RANGE_ER = 0.20
REGIME_MIN_RANGE_ATR = 2.0
MTF_RANGE_MAX_ENTRY_PIPS = _clean_env_float("MTF_RANGE_MAX_ENTRY_PIPS", 15.0)
MTF_RANGE_STOP_ATR = _clean_env_float("MTF_RANGE_STOP_ATR", 0.3)
MTF_TREND_STOP_ATR = _clean_env_float("MTF_TREND_STOP_ATR", 0.05)

_SYSTEM = """You are a discretionary multi-timeframe analyst connected to a cTrader cBot.
You do not apply a rigid indicator strategy. Default action is WAIT. Never force a trade.
Analyse D1 (context), then H4 (dominant direction and major zones), then H1 (structure and invalidation), then M15 (how to enter).
Do not analyse M15 in isolation. Do not chase an impulse. Do not sell into major support. Do not buy into major resistance.
A good direction is not a good entry. A missed trade is better than a bad trade.

The input includes a deterministic REGIME line. Obey it:
- TREND: trade pullback continuation only. The stop must sit beyond the recent swing (below swing_low for BUY, above swing_high for SELL). Target at least 2R.
- RANGE: fade the outer boundary only, with a LIMIT order. SELL only in the upper half with the stop above range_high + ~0.3 ATR and the target between mid and range_low. BUY is the mirror. If 2R is not available inside the range, WAIT. A target outside the range is a breakout thesis and is not a range trade.
- CHOP: WAIT. Do not open a new entry.
- UNKNOWN: use the general rules below.

Order types:
- BUY_LIMIT / SELL_LIMIT when direction is clear but market entry would chase, and a pullback zone exists.
- BUY_STOP / SELL_STOP when price sits on support or resistance and continuation needs a break first.
- BUY_MARKET / SELL_MARKET only when direction is confirmed, price is not extended, and nothing important sits immediately against the trade.
- WAIT when timeframes conflict, structure is unclear, the regime is CHOP, price is mid-range or extended, no logical stop exists, or reward is poor.
- A market order fills at the live price, which can move while you think: the stop must sit below the current ask for a BUY (above the current bid for a SELL) and the target beyond it, or the order is refused.
- If a pending order already exists, do not create another. Set order_management.existing_order_action to KEEP_PENDING_ORDER, MODIFY_PENDING_ORDER, or CANCEL_PENDING_ORDER. MODIFY_PENDING_ORDER re-prices that order to the entry, stop and target you return, and only on the side it already has.
- If a position is already open, do not open another. Set position_management.action to HOLD_POSITION, CLOSE_POSITION, or DO_NOT_CHANGE. Never move a stop farther away. Never remove a stop.
- You do not choose position size. Confidence is not size.
- Use only economic events supplied in the input. If a high-impact event is imminent, prefer WAIT.
- Every pending order needs an expiry or a cancel condition.
- Confidence 0-100 measures clarity, not win probability. Below 70, new entries should be WAIT.

Return ONLY this JSON, no markdown:
{
  "symbol": "",
  "market_analysis": {
    "D1": {"bias": "BULLISH|BEARISH|RANGE|UNCLEAR", "context": ""},
    "H4": {"bias": "", "structure": "", "major_support": "", "major_resistance": ""},
    "H1": {"bias": "", "structure": "", "invalidation": null},
    "M15": {"context": "", "entry_logic": ""}
  },
  "decision": {
    "action": "WAIT",
    "order_type": "NONE",
    "side": "NONE",
    "entry": null,
    "stop_loss": null,
    "take_profit": null,
    "risk_reward": null,
    "confidence": 0
  },
  "order_management": {"expiry": null, "cancel_if": "", "existing_order_action": "NONE"},
  "position_management": {"action": "NONE"},
  "reasoning": {
    "direction_reason": "",
    "entry_reason": "",
    "order_type_reason": "",
    "stop_reason": "",
    "target_reason": "",
    "main_risk": ""
  }
}
"""


class BarOHLC(BaseModel):
    t: str = ""
    o: float
    h: float
    l: float
    c: float


class OpenState(BaseModel):
    side: str = ""
    order_type: str = ""
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    bars_alive: int = 0


class MtfSnapshot(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread_pips: float = 0.0
    max_spread_pips: float = 40.0
    min_rr: float = 2.0
    max_entry_distance_pips: float = 0.0
    has_position: bool = False
    has_pending: bool = False
    is_live: bool = False
    daily_pnl: float = 0.0
    max_daily_loss: float = 0.0
    bot_id: str = "mtf"
    pip_size: float = 0.0
    equity: float = 0.0
    risk_percent: float = 0.0
    fixed_lots: float = 0.0
    min_volume_units: float = 0.0
    pip_value: float = 0.0
    running_mode: str = "RealTime"
    news: List[str] = Field(default_factory=list)
    position: Optional[OpenState] = None
    pending: Optional[OpenState] = None
    d1: List[BarOHLC] = Field(default_factory=list)
    h4: List[BarOHLC] = Field(default_factory=list)
    h1: List[BarOHLC] = Field(default_factory=list)
    m15: List[BarOHLC] = Field(default_factory=list)


class BiasBlock(BaseModel):
    bias: str = "UNCLEAR"
    context: str = ""
    structure: str = ""
    major_support: str = ""
    major_resistance: str = ""
    invalidation: Optional[float] = None
    entry_logic: str = ""


class MarketAnalysis(BaseModel):
    D1: BiasBlock = Field(default_factory=BiasBlock)
    H4: BiasBlock = Field(default_factory=BiasBlock)
    H1: BiasBlock = Field(default_factory=BiasBlock)
    M15: BiasBlock = Field(default_factory=BiasBlock)


class DecisionBlock(BaseModel):
    action: str = "WAIT"
    order_type: str = "NONE"
    side: str = "NONE"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    confidence: float = 0.0


class OrderManagement(BaseModel):
    expiry: Optional[str] = None
    cancel_if: str = ""
    existing_order_action: str = "NONE"


class PositionManagement(BaseModel):
    action: str = "NONE"


class Reasoning(BaseModel):
    direction_reason: str = ""
    entry_reason: str = ""
    order_type_reason: str = ""
    stop_reason: str = ""
    target_reason: str = ""
    main_risk: str = ""


class MtfDecision(BaseModel):
    approved: bool = False
    gate_reason: str = ""
    symbol: str = ""
    client_order_id: str = ""
    market_analysis: MarketAnalysis = Field(default_factory=MarketAnalysis)
    decision: DecisionBlock = Field(default_factory=DecisionBlock)
    order_management: OrderManagement = Field(default_factory=OrderManagement)
    position_management: PositionManagement = Field(default_factory=PositionManagement)
    reasoning: Reasoning = Field(default_factory=Reasoning)


def _wait(reason: str, symbol: str = "") -> MtfDecision:
    out = MtfDecision(approved=False, gate_reason=reason, symbol=symbol)
    out.decision.action = "WAIT"
    out.reasoning.main_risk = reason
    return out


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _block(raw: Any) -> BiasBlock:
    if not isinstance(raw, dict):
        return BiasBlock()
    return BiasBlock(
        bias=str(raw.get("bias") or "UNCLEAR").upper(),
        context=str(raw.get("context") or "")[:500],
        structure=str(raw.get("structure") or "")[:500],
        major_support=str(raw.get("major_support") or "")[:120],
        major_resistance=str(raw.get("major_resistance") or "")[:120],
        invalidation=_num(raw.get("invalidation")),
        entry_logic=str(raw.get("entry_logic") or "")[:500],
    )


def parse_llm_decision(raw: Any) -> MtfDecision:
    """Read the analyst JSON. Anything unreadable stays WAIT."""
    if isinstance(raw, str):
        try:
            raw = JSONResponseParser.parse(raw)
        except Exception:
            return _wait("LLM response was not JSON")
    if not isinstance(raw, dict):
        return _wait("LLM response was empty")

    decision_raw = raw.get("decision") if isinstance(raw.get("decision"), dict) else raw
    action = str(decision_raw.get("action") or "WAIT").upper().replace(" ", "_")
    legacy = {
        "BUY": "BUY_MARKET",
        "SELL": "SELL_MARKET",
        "SELL_LIMIT": "SELL_LIMIT",
        "BUY_LIMIT": "BUY_LIMIT",
    }
    action = legacy.get(action, action)
    if action not in ENTRY_ACTIONS and action not in MANAGE_ACTIONS:
        action = "WAIT"

    side = "BUY" if action.startswith("BUY") else ("SELL" if action.startswith("SELL") else "NONE")
    order_type = action.split("_", 1)[1] if "_" in action and action in ENTRY_ACTIONS else "NONE"
    entry = _num(decision_raw.get("entry"))
    sl = _num(decision_raw.get("stop_loss"))
    tp = _num(decision_raw.get("take_profit"))
    confidence = _num(decision_raw.get("confidence")) or 0.0

    analysis = raw.get("market_analysis") if isinstance(raw.get("market_analysis"), dict) else {}
    orders = raw.get("order_management") if isinstance(raw.get("order_management"), dict) else {}
    position = raw.get("position_management") if isinstance(raw.get("position_management"), dict) else {}
    reasons = raw.get("reasoning") if isinstance(raw.get("reasoning"), dict) else {}
    existing = str(orders.get("existing_order_action") or "NONE").upper()
    if action == "CANCEL_PENDING_ORDER":
        existing = "CANCEL_PENDING_ORDER"

    out = MtfDecision(symbol=str(raw.get("symbol") or ""), approved=False)
    out.market_analysis = MarketAnalysis(
        D1=_block(analysis.get("D1")),
        H4=_block(analysis.get("H4")),
        H1=_block(analysis.get("H1")),
        M15=_block(analysis.get("M15")),
    )
    out.decision = DecisionBlock(
        action=action,
        order_type=order_type,
        side=side,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=_num(decision_raw.get("risk_reward")),
        confidence=confidence,
    )
    out.order_management = OrderManagement(
        expiry=None if orders.get("expiry") is None else str(orders.get("expiry"))[:200],
        cancel_if=str(orders.get("cancel_if") or "")[:300],
        existing_order_action=existing,
    )
    pos_action = str(position.get("action") or "NONE").upper()
    if action in ("HOLD_POSITION", "CLOSE_POSITION"):
        pos_action = action
    out.position_management = PositionManagement(action=pos_action)
    out.reasoning = Reasoning(
        direction_reason=str(reasons.get("direction_reason") or "")[:500],
        entry_reason=str(reasons.get("entry_reason") or "")[:500],
        order_type_reason=str(reasons.get("order_type_reason") or "")[:500],
        stop_reason=str(reasons.get("stop_reason") or "")[:500],
        target_reason=str(reasons.get("target_reason") or "")[:500],
        main_risk=str(reasons.get("main_risk") or "")[:500],
    )
    return out


def compute_regime(bars: List[BarOHLC]) -> Dict[str, Any]:
    """Classify closed M15 bars as TREND, RANGE, CHOP or UNKNOWN.

    ER is Kaufman's efficiency ratio: net move / total path. A trend travels (high ER);
    a range oscillates (low ER). range/ATR separates a tradable range from a tight chop.
    """
    if len(bars) < max(REGIME_LOOKBACK + 1, REGIME_ATR_PERIOD + 1):
        return {
            "mode": "UNKNOWN", "er": 0.0, "atr": 0.0, "range_atr": 0.0,
            "range_high": None, "range_low": None, "mid": None, "width": None,
            "swing_high": None, "swing_low": None,
        }

    lookback = bars[-(REGIME_LOOKBACK + 1):]
    net = abs(lookback[-1].c - lookback[0].c)
    path = sum(abs(a.c - b.c) for a, b in zip(lookback, lookback[1:]))
    er = net / path if path > 0 else 0.0

    recent = bars[-REGIME_LOOKBACK:]
    range_high = max(b.h for b in recent)
    range_low = min(b.l for b in recent)
    trs = []
    for i in range(len(bars) - REGIME_ATR_PERIOD, len(bars)):
        prev = bars[i - 1]
        bar = bars[i]
        trs.append(max(bar.h - bar.l, abs(bar.h - prev.c), abs(bar.l - prev.c)))
    atr = sum(trs) / len(trs) if trs else 0.0
    range_atr = (range_high - range_low) / atr if atr > 0 else 0.0

    if er >= REGIME_TREND_ER and range_atr >= REGIME_MIN_RANGE_ATR:
        mode = "TREND"
    elif er <= REGIME_RANGE_ER and range_atr >= REGIME_MIN_RANGE_ATR:
        mode = "RANGE"
    else:
        mode = "CHOP"

    swing = bars[-REGIME_SWING_LOOKBACK:]
    return {
        "mode": mode,
        "er": round(er, 3),
        "atr": atr,
        "range_atr": round(range_atr, 2),
        "range_high": range_high,
        "range_low": range_low,
        "mid": (range_high + range_low) / 2.0,
        "width": range_high - range_low,
        "swing_high": max(b.h for b in swing),
        "swing_low": min(b.l for b in swing),
    }


def _regime_text(regime: Dict[str, Any]) -> str:
    if regime["mode"] == "UNKNOWN":
        return "REGIME UNKNOWN (not enough closed M15 bars)."
    return (
        f"REGIME {regime['mode']} | ER {regime['er']:.2f} | range/ATR {regime['range_atr']:.2f} | "
        f"range {regime['range_low']:.5f}-{regime['range_high']:.5f} | mid {regime['mid']:.5f} | "
        f"ATR {regime['atr']:.5f} | swing {regime['swing_low']:.5f}-{regime['swing_high']:.5f}"
    )


def _geometry(action: str, entry: float, sl: float, tp: float, bid: float, ask: float) -> Optional[str]:
    buy = action.startswith("BUY")
    if buy and not (sl < entry < tp):
        return "BUY stop must sit below entry and target above it"
    if not buy and not (tp < entry < sl):
        return "SELL stop must sit above entry and target below it"
    if action == "BUY_MARKET" and bid > entry:
        return "BUY_MARKET would chase above the planned entry"
    if action == "SELL_MARKET" and ask < entry:
        return "SELL_MARKET would chase below the planned entry"
    if action == "BUY_LIMIT" and entry >= bid:
        return "BUY_LIMIT must sit below the current bid"
    if action == "SELL_LIMIT" and entry <= ask:
        return "SELL_LIMIT must sit above the current ask"
    if action == "BUY_STOP" and entry <= ask:
        return "BUY_STOP must sit above the current ask"
    if action == "SELL_STOP" and entry >= bid:
        return "SELL_STOP must sit below the current bid"
    return None


def _side_from_bracket(entry: Optional[float], sl: Optional[float], tp: Optional[float]) -> str:
    """The side the stop and the target bracket: BUY when sl < entry < tp, SELL when it mirrors."""
    if entry is None or sl is None or tp is None:
        return "NONE"
    if sl < entry < tp:
        return "BUY"
    if tp < entry < sl:
        return "SELL"
    return "NONE"


def approve(proposal: MtfDecision, snapshot: MtfSnapshot) -> MtfDecision:
    """Authorise management always, and a new order only when prices, RR and location hold."""
    action = proposal.decision.action
    proposal.symbol = proposal.symbol or snapshot.symbol

    if snapshot.is_live and action != "WAIT":
        return _wait("Live account refused", snapshot.symbol)

    if action == "CANCEL_PENDING_ORDER" or proposal.order_management.existing_order_action == "CANCEL_PENDING_ORDER":
        if not snapshot.has_pending:
            return _wait("No pending order to cancel", snapshot.symbol)
        proposal.decision.action = "CANCEL_PENDING_ORDER"
        proposal.approved = True
        proposal.gate_reason = "Cancel authorised"
        return proposal

    if action in ("WAIT", "HOLD_POSITION"):
        proposal.approved = action == "HOLD_POSITION"
        proposal.gate_reason = proposal.reasoning.main_risk or action
        return proposal

    if action == "CLOSE_POSITION":
        if not snapshot.has_position:
            return _wait("No position to close", snapshot.symbol)
        proposal.approved = True
        proposal.gate_reason = "Close authorised"
        return proposal

    if action == "MODIFY_PENDING_ORDER":
        # The prompt asks for an entry action plus order_management, but a model that answers with
        # MODIFY_PENDING_ORDER alone still means a modify: rebuild the side from its own bracket.
        proposal.order_management.existing_order_action = "MODIFY_PENDING_ORDER"
        if proposal.decision.side == "NONE":
            derived = _side_from_bracket(proposal.decision.entry, proposal.decision.stop_loss, proposal.decision.take_profit)
            if derived == "NONE":
                return _wait("A modify needs an entry, a stop and a target that bracket it", snapshot.symbol)
            proposal.decision.side = derived
        if proposal.decision.order_type == "NONE":
            pending_kind = (snapshot.pending.order_type if snapshot.pending else "") or "MARKET"
            proposal.decision.order_type = str(pending_kind).upper()

    if action not in ENTRY_ACTIONS and action != "MODIFY_PENDING_ORDER":
        return _wait("Action is not an authorised order", snapshot.symbol)

    modify = proposal.order_management.existing_order_action == "MODIFY_PENDING_ORDER"
    if snapshot.has_position:
        return _wait("A position is already open", snapshot.symbol)
    if snapshot.has_pending:
        if not modify:
            return _wait("A pending order already exists", snapshot.symbol)
        # A modify may only re-price the order that is already there; a flip is a cancel + new entry.
        pending_side = ((snapshot.pending.side if snapshot.pending else "") or "").upper()
        if pending_side and proposal.decision.side != pending_side:
            return _wait("A pending order may only be modified on its own side", snapshot.symbol)
    elif modify:
        return _wait("No pending order to modify", snapshot.symbol)
    if snapshot.max_daily_loss > 0 and snapshot.daily_pnl <= -snapshot.max_daily_loss:
        return _wait("Daily loss limit reached", snapshot.symbol)
    if snapshot.spread_pips > snapshot.max_spread_pips:
        return _wait(f"Spread {snapshot.spread_pips:.1f} pips is above {snapshot.max_spread_pips:.1f}", snapshot.symbol)
    if proposal.decision.confidence < MIN_CONFIDENCE:
        return _wait(f"Confidence {proposal.decision.confidence:.0f} is below {MIN_CONFIDENCE:.0f}", snapshot.symbol)

    entry = proposal.decision.entry
    sl = proposal.decision.stop_loss
    tp = proposal.decision.take_profit
    if entry is None or sl is None or tp is None or entry <= 0 or sl <= 0 or tp <= 0:
        return _wait("Entry, stop and target are required", snapshot.symbol)

    # The side and the order type drive every price rule below, whichever shape the reply took.
    order_kind = proposal.decision.order_type if proposal.decision.order_type in ("MARKET", "LIMIT", "STOP") else "LIMIT"
    effective = f"{proposal.decision.side}_{order_kind}" if proposal.decision.side in ("BUY", "SELL") else action

    # A market order fills at the live price, and the fill can land up to the cBot's AI timeout
    # after this gate read the book. Judge it against that price, not the analyst's planned entry:
    # otherwise the stop and the target are measured from a level the market has already left.
    buy = effective.startswith("BUY")
    regime = compute_regime(snapshot.m15)
    if not modify and regime["mode"] == "CHOP":
        return _wait(
            f"Regime CHOP (ER {regime['er']:.2f}, range/ATR {regime['range_atr']:.2f}); "
            f"no new entry",
            snapshot.symbol,
        )
    if not modify and regime["mode"] == "RANGE":
        if not effective.endswith("LIMIT"):
            return _wait("Range regime requires a LIMIT order at the boundary", snapshot.symbol)
        atr = regime["atr"]
        buffer = MTF_RANGE_STOP_ATR * atr if atr > 0 else 0.0
        if buy:
            if entry > regime["mid"]:
                return _wait("Range BUY must sit in the lower half of the range", snapshot.symbol)
            if sl > regime["range_low"] - buffer:
                return _wait("Range BUY stop must sit below range_low minus 0.3 ATR", snapshot.symbol)
            if not (regime["mid"] <= tp <= regime["range_high"]):
                return _wait("Range BUY target must sit between mid and range_high", snapshot.symbol)
        else:
            if entry < regime["mid"]:
                return _wait("Range SELL must sit in the upper half of the range", snapshot.symbol)
            if sl < regime["range_high"] + buffer:
                return _wait("Range SELL stop must sit above range_high plus 0.3 ATR", snapshot.symbol)
            if not (regime["range_low"] <= tp <= regime["mid"]):
                return _wait("Range SELL target must sit between range_low and mid", snapshot.symbol)
        if snapshot.pip_size > 0:
            reference = snapshot.ask if buy else snapshot.bid
            distance = abs(entry - reference) / snapshot.pip_size
            if distance > MTF_RANGE_MAX_ENTRY_PIPS + 1e-9:
                return _wait(
                    f"Range entry is {distance:.0f} pips from the market, above the "
                    f"{MTF_RANGE_MAX_ENTRY_PIPS:.0f} pip cap",
                    snapshot.symbol,
                )
    if not modify and regime["mode"] == "TREND":
        atr = regime["atr"]
        buffer = MTF_TREND_STOP_ATR * atr if atr > 0 else 0.0
        if buy and sl > regime["swing_low"] - buffer:
            return _wait("Trend BUY stop is inside the recent swing_low", snapshot.symbol)
        if not buy and sl < regime["swing_high"] + buffer:
            return _wait("Trend SELL stop is inside the recent swing_high", snapshot.symbol)
    if effective.endswith("MARKET"):
        fill = snapshot.ask if buy else snapshot.bid
        if buy and not (sl < fill < tp):
            return _wait("Market price is already at or past the stop or the target", snapshot.symbol)
        if not buy and not (tp < fill < sl):
            return _wait("Market price is already at or past the stop or the target", snapshot.symbol)
        risk = abs(fill - sl)
        reward = abs(tp - fill)
    else:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
    if risk <= 0:
        return _wait("Stop distance is zero", snapshot.symbol)
    rr = reward / risk
    if rr + 1e-9 < snapshot.min_rr:
        return _wait(f"RR {rr:.2f} is below {snapshot.min_rr:.2f}", snapshot.symbol)
    problem = _geometry(effective, entry, sl, tp, snapshot.bid, snapshot.ask)
    if problem:
        return _wait(problem, snapshot.symbol)

    # The cBot cannot round size up: on a small account the broker's minimum lot can risk more
    # than the configured percentage, and the order is then refused after the gate approved it.
    # Refuse it here with the arithmetic so the journal says why instead of leaving an approved
    # order that never fills. Fields default to 0 on older cBot builds, so this is opt-in.
    # A deliberate FixedLots size (e.g. 0.01 on a $705 demo) bypasses the percentage budget.
    if not modify and snapshot.fixed_lots <= 0 and all((
        snapshot.pip_size > 0,
        snapshot.equity > 0,
        snapshot.risk_percent > 0,
        snapshot.min_volume_units > 0,
        snapshot.pip_value > 0,
    )):
        risk_pips = risk / snapshot.pip_size
        min_lot_risk = snapshot.min_volume_units * risk_pips * snapshot.pip_value
        risk_budget = snapshot.equity * snapshot.risk_percent / 100.0
        if min_lot_risk > risk_budget + 0.01:
            return _wait(
                f"Minimum lot risk ${min_lot_risk:.2f} exceeds the "
                f"${risk_budget:.2f} budget ({snapshot.risk_percent:.2f}% of "
                f"${snapshot.equity:.2f}); raise RiskPercent or fund the account",
                snapshot.symbol,
            )

    if snapshot.max_entry_distance_pips > 0 and snapshot.pip_size > 0:
        reference = snapshot.ask if buy else snapshot.bid
        distance = abs(entry - reference) / snapshot.pip_size
        if distance > snapshot.max_entry_distance_pips + 1e-9:
            return _wait(
                f"Entry is {distance:.0f} pips from the market, above the "
                f"{snapshot.max_entry_distance_pips:.0f} pip limit", snapshot.symbol)

    proposal.decision.risk_reward = round(rr, 2)
    proposal.approved = True
    if modify:
        proposal.decision.action = "MODIFY_PENDING_ORDER"
        proposal.gate_reason = "Pending order modification authorised"
        return proposal
    proposal.gate_reason = "Order authorised"
    return proposal


def _bars_text(name: str, bars: List[BarOHLC]) -> str:
    lines = [f"{name} ({len(bars)} closed bars, oldest first): t,o,h,l,c"]
    for bar in bars:
        lines.append(f"{bar.t},{bar.o},{bar.h},{bar.l},{bar.c}")
    return "\n".join(lines)


def build_messages(snapshot: MtfSnapshot) -> List[Dict[str, str]]:
    state = [
        f"Symbol {snapshot.symbol}. Bid {snapshot.bid}. Ask {snapshot.ask}.",
        f"Spread {snapshot.spread_pips} pips. Minimum RR {snapshot.min_rr}.",
        _regime_text(compute_regime(snapshot.m15)),
    ]
    if snapshot.position:
        pos = snapshot.position
        state.append(f"Open position: {pos.side} entry {pos.entry} SL {pos.stop_loss} TP {pos.take_profit} pnl {pos.pnl}.")
    if snapshot.pending:
        pend = snapshot.pending
        state.append(
            f"Existing pending: {pend.order_type} {pend.side} entry {pend.entry} "
            f"SL {pend.stop_loss} TP {pend.take_profit} bars_alive {pend.bars_alive}."
        )
    if snapshot.news:
        state.append("Supplied calendar: " + " | ".join(snapshot.news))
    else:
        state.append("No economic calendar was supplied. Do not invent events.")
    user = "\n\n".join(state + [
        _bars_text("D1", snapshot.d1),
        _bars_text("H4", snapshot.h4),
        _bars_text("H1", snapshot.h1),
        _bars_text("M15", snapshot.m15),
    ])
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _llm_kwargs(llm) -> Dict[str, Any]:
    """Per-attempt budget for the analyst call, mirroring what /trade passes."""
    kwargs: Dict[str, Any] = {
        "temperature": 0.1,
        "timeout": MTF_LLM_TIMEOUT,
        "max_retries": MTF_LLM_MAX_RETRIES,
    }
    thinking = getattr(llm, "thinking_options", None)
    if callable(thinking):
        try:
            res = thinking(MTF_LLM_ENABLE_THINKING)
            if isinstance(res, dict):
                kwargs.update(res)
        except Exception:
            pass
    if hasattr(getattr(llm, "client", None), "chat"):
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


async def decide(snapshot: MtfSnapshot, llm) -> MtfDecision:
    # 1. News Blackout Shield: while flat, a high-impact event within ±30m blocks a new entry.
    # A calendar outage is logged and the analyst still runs: blocking every entry when the feed
    # is down would be a second, unmonitored failure mode.
    if not snapshot.has_position:
        try:
            from app.news_service import is_news_blackout_active
            is_bo, bo_title, bo_mins = await is_news_blackout_active(
                snapshot.symbol, pause_before_mins=30, pause_after_mins=30
            )
            if is_bo:
                return _wait(
                    f"News blackout shield active: {bo_title} ({bo_mins}m remaining)",
                    snapshot.symbol,
                )
        except Exception as exc:
            logger.warning(
                f"[MTF] News blackout check failed for {snapshot.symbol}: {exc}"
            )

    # 2. News calendar feed: Enrich snapshot with upcoming events if none provided by cBot
    if not snapshot.news:
        try:
            from app.news_service import get_upcoming_symbol_events
            snapshot.news = await get_upcoming_symbol_events(snapshot.symbol, hours_ahead=12)
        except Exception:
            pass

    try:
        text = await asyncio.wait_for(
            llm.chat(build_messages(snapshot), **_llm_kwargs(llm)), timeout=MTF_LLM_DEADLINE)
    except asyncio.TimeoutError:
        return _wait(f"LLM deadline of {MTF_LLM_DEADLINE:.0f}s exceeded", snapshot.symbol)
    except Exception as exc:
        return _wait(f"LLM unavailable: {describe_llm_error(exc)}", snapshot.symbol)
    return approve(parse_llm_decision(text), snapshot)

JOURNAL_EVENTS = {"fill", "close", "error", "expiry", "cancel", "modify"}


def _journal_path(backtest: bool = False) -> Path:
    """`trades/mtf_journal.jsonl` is forward-test evidence; a backtest gets its own file."""
    if backtest:
        return Path(os.environ.get("MTF_BACKTEST_JOURNAL_PATH") or "trades/mtf_backtest_journal.jsonl")
    return Path(os.environ.get("MTF_JOURNAL_PATH") or "trades/mtf_journal.jsonl")


def is_backtest(running_mode: str) -> bool:
    """cTrader sends "RealTime", "Backtesting" or "Optimization": only RealTime is evidence."""
    return bool(running_mode) and str(running_mode).strip().lower() != "realtime"


def _append(record: Dict[str, Any], backtest: bool = False) -> None:
    """Append-only: past lines are never rewritten, corrections go in a new line."""
    path = _journal_path(backtest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def journal_decision(snapshot: MtfSnapshot, out: MtfDecision) -> MtfDecision:
    """Record every decision before the cBot can act on it; an approved entry gets its order id here."""
    now = _now()
    d = out.decision
    backtest = is_backtest(snapshot.running_mode)
    if out.approved and (d.action in ENTRY_ACTIONS or d.action == "MODIFY_PENDING_ORDER"):
        out.client_order_id = f"{snapshot.bot_id}-{snapshot.symbol}-{now:%Y%m%dT%H%M%SZ}"
    r = out.reasoning
    regime = compute_regime(snapshot.m15)
    _append({
        "event": "decision",
        "ts_utc": now.isoformat().replace("+00:00", "Z"),
        "bot_id": snapshot.bot_id,
        "symbol": snapshot.symbol,
        "client_order_id": out.client_order_id,
        "approved": out.approved,
        "gate_reason": out.gate_reason,
        "action": d.action,
        "intended": {"entry": d.entry, "stop": d.stop_loss, "take_profit": d.take_profit},
        "risk_reward": d.risk_reward,
        "confidence": d.confidence,
        "market": {"bid": snapshot.bid, "ask": snapshot.ask, "spread_pips": snapshot.spread_pips},
        "bias": {tf: getattr(out.market_analysis, tf).bias for tf in ("D1", "H4", "H1", "M15")},
        "regime": regime,
        "thesis": " | ".join(x for x in (r.direction_reason, r.entry_reason) if x),
        "invalidation": r.main_risk,
        "running_mode": snapshot.running_mode,
    }, backtest=backtest)
    return out


def register_mtf_routes(app, llm_provider) -> None:
    @app.post("/trade/mtf", response_model=MtfDecision)
    async def trade_mtf(snapshot: MtfSnapshot) -> MtfDecision:
        regime = compute_regime(snapshot.m15)
        logger.info(
            f"[SNAPSHOT MTF] {snapshot.bot_id} | {snapshot.symbol} | "
            f"Bid={snapshot.bid:g} Ask={snapshot.ask:g} Spread={snapshot.spread_pips:.1f}p | "
            f"Pos={'YES' if snapshot.has_position else 'NONE'} Pending={'YES' if snapshot.has_pending else 'NONE'} | "
            f"Regime={regime['mode']} ER={regime['er']:.2f}"
        )
        decision = await decide(snapshot, llm_provider())
        out = journal_decision(snapshot, decision)
        logger.info(
            f"[DECISION MTF] {snapshot.bot_id} | {snapshot.symbol} | "
            f"Approved={out.approved} Action={out.decision.action} OrderType={out.decision.order_type} "
            f"Entry={out.decision.entry} SL={out.decision.stop_loss} TP={out.decision.take_profit} "
            f"Conf={out.decision.confidence:.1f}% GateReason={out.gate_reason}"
        )
        try:
            from app.cbot_watchdog import record_bot_snapshot
            # last_snapshot_for() matches "<account>/<bot_id>"; MTF does not carry an account
            # in the snapshot, so use a stable synthetic scope instead of a bare bot id.
            record_bot_snapshot(f"mtf/{snapshot.bot_id}")
        except Exception:
            pass
        try:
            from app.dashboard import record_ai_decision, broadcast_decision
            mtf_dict = {
                "bot_id": snapshot.bot_id,
                "symbol": snapshot.symbol,
                "account_id": getattr(snapshot, "account_id", None) or "demo",
                "action": out.decision.action,
                "confidence": out.decision.confidence,
                "reason": out.gate_reason or out.reasoning.main_risk or "MTF Analysis",
                "entry_price": out.decision.entry,
                "sl_price": out.decision.stop_loss,
                "tp_price": out.decision.take_profit,
                "approved": out.approved,
                "timeframe": "m15",
                "strategy": "MTF",
                "regime": regime["mode"],
                "timestamp": _now().isoformat(),
            }
            record_ai_decision(mtf_dict)
            await broadcast_decision(mtf_dict)
        except Exception:
            pass
        return out

    @app.post("/trade/mtf/event")
    async def trade_mtf_event(event: Dict[str, Any]) -> Dict[str, str]:
        name = str(event.get("event") or "")
        if name not in JOURNAL_EVENTS:
            raise HTTPException(status_code=400, detail=f"Unknown journal event: {name or 'missing'}")
        logger.info(f"[EVENT MTF] {event.get('bot_id', 'mtf')} | Event={name} | Data={event}")
        _append({**event, "ts_utc": _now().isoformat().replace("+00:00", "Z")},
                backtest=is_backtest(str(event.get("running_mode") or "")))
        return {"status": "ok"}
