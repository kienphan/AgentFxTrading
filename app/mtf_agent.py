"""D1 → H4 → H1 → M15 analyst. The model proposes an order type; this module authorises it."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.llm_client import JSONResponseParser, describe_llm_error

ENTRY_ACTIONS = {
    "BUY_MARKET",
    "SELL_MARKET",
    "BUY_LIMIT",
    "SELL_LIMIT",
    "BUY_STOP",
    "SELL_STOP",
}
MANAGE_ACTIONS = {"WAIT", "CANCEL_PENDING_ORDER", "HOLD_POSITION", "CLOSE_POSITION"}
MIN_CONFIDENCE = 65.0

_SYSTEM = """You are a discretionary multi-timeframe analyst connected to a cTrader cBot.
You do not apply a rigid indicator strategy. Default action is WAIT. Never force a trade.
Analyse D1 (context), then H4 (dominant direction and major zones), then H1 (structure and invalidation), then M15 (how to enter).
Do not analyse M15 in isolation. Do not chase an impulse. Do not sell into major support. Do not buy into major resistance.
A good direction is not a good entry. A missed trade is better than a bad trade.

Order types:
- BUY_LIMIT / SELL_LIMIT when direction is clear but market entry would chase, and a pullback zone exists.
- BUY_STOP / SELL_STOP when price sits on support or resistance and continuation needs a break first.
- BUY_MARKET / SELL_MARKET only when direction is confirmed, price is not extended, and nothing important sits immediately against the trade.
- WAIT when timeframes conflict, structure is unclear, price is mid-range or extended, no logical stop exists, or reward is poor.
- If a pending order already exists, do not create another. Set order_management.existing_order_action to KEEP_PENDING_ORDER, MODIFY_PENDING_ORDER, or CANCEL_PENDING_ORDER.
- If a position is already open, do not open another. Set position_management.action to HOLD_POSITION, CLOSE_POSITION, or DO_NOT_CHANGE. Never move a stop farther away. Never remove a stop.
- You do not choose position size. Confidence is not size.
- Use only economic events supplied in the input. If a high-impact event is imminent, prefer WAIT.
- Every pending order needs an expiry or a cancel condition.
- Confidence 0-100 measures clarity, not win probability. Below 65, new entries should be WAIT.

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
    has_position: bool = False
    has_pending: bool = False
    is_live: bool = False
    daily_pnl: float = 0.0
    max_daily_loss: float = 0.0
    bot_id: str = "mtf"
    pip_size: float = 0.0
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

    if action not in ENTRY_ACTIONS and proposal.order_management.existing_order_action != "MODIFY_PENDING_ORDER":
        return _wait("Action is not an authorised order", snapshot.symbol)

    if snapshot.has_position:
        return _wait("A position is already open", snapshot.symbol)
    if snapshot.has_pending and proposal.order_management.existing_order_action != "MODIFY_PENDING_ORDER":
        return _wait("A pending order already exists", snapshot.symbol)
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
    risk = abs(entry - sl)
    if risk <= 0:
        return _wait("Stop distance is zero", snapshot.symbol)
    rr = abs(tp - entry) / risk
    if rr + 1e-9 < snapshot.min_rr:
        return _wait(f"RR {rr:.2f} is below {snapshot.min_rr:.2f}", snapshot.symbol)
    problem = _geometry(action, entry, sl, tp, snapshot.bid, snapshot.ask)
    if problem:
        return _wait(problem, snapshot.symbol)

    proposal.decision.risk_reward = round(rr, 2)
    proposal.approved = True
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


async def decide(snapshot: MtfSnapshot, llm) -> MtfDecision:
    try:
        text = await llm.chat(build_messages(snapshot))
    except Exception as exc:
        return _wait(f"LLM unavailable: {describe_llm_error(exc)}", snapshot.symbol)
    return approve(parse_llm_decision(text), snapshot)


JOURNAL_EVENTS = {"fill", "close", "error", "expiry", "cancel"}


def _journal_path() -> Path:
    return Path(os.environ.get("MTF_JOURNAL_PATH") or "trades/mtf_journal.jsonl")


def _append(record: Dict[str, Any]) -> None:
    """Append-only: past lines are never rewritten, corrections go in a new line."""
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def journal_decision(snapshot: MtfSnapshot, out: MtfDecision) -> MtfDecision:
    """Record every decision before the cBot can act on it; an approved entry gets its order id here."""
    now = _now()
    d = out.decision
    if out.approved and d.action in ENTRY_ACTIONS:
        out.client_order_id = f"{snapshot.bot_id}-{snapshot.symbol}-{now:%Y%m%dT%H%M%SZ}"
    r = out.reasoning
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
        "thesis": " | ".join(x for x in (r.direction_reason, r.entry_reason) if x),
        "invalidation": r.main_risk,
    })
    return out


def register_mtf_routes(app, llm_provider) -> None:
    @app.post("/trade/mtf", response_model=MtfDecision)
    async def trade_mtf(snapshot: MtfSnapshot) -> MtfDecision:
        return journal_decision(snapshot, await decide(snapshot, llm_provider()))

    @app.post("/trade/mtf/event")
    async def trade_mtf_event(event: Dict[str, Any]) -> Dict[str, str]:
        name = str(event.get("event") or "")
        if name not in JOURNAL_EVENTS:
            raise HTTPException(status_code=400, detail=f"Unknown journal event: {name or 'missing'}")
        _append({**event, "ts_utc": _now().isoformat().replace("+00:00", "Z")})
        return {"status": "ok"}
