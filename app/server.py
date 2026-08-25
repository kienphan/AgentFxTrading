import os
import uvicorn
import json
import logging
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv
from app.llm_client import create_llm_client, JSONResponseParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AgentServer")

app = FastAPI(title="TMS+ORB Agent Server")

# Create LLM client based on LLM_PROVIDER env variable
# Supports: "qwen", "openai", "anthropic", "deepseek", "openai_compatible"
llm_client = create_llm_client()

# ---- Data Models (from cBot) ----
class BarData(BaseModel):
    ha_color: str
    tdi_green: float
    tdi_red: float
    stoch_k: float
    stoch_d: float

class TmsSignals(BaseModel):
    # Bias
    bias: str  # "BULLISH", "BEARISH", "NEUTRAL"
    bars_since_cross: int
    cross_direction: Optional[str] = None

    # Current signals
    cross_up: bool = False
    cross_down: bool = False
    ha_turned_green: bool = False
    ha_turned_red: bool = False
    stoch_bull: bool = False
    stoch_bear: bool = False
    angle_ok_long: bool = False
    angle_ok_short: bool = False
    within_window: bool = False

    # Entry signals (all conditions met)
    long_entry: bool = False
    short_entry: bool = False

    # Exit signals
    exit_long: bool = False
    exit_short: bool = False
    exit_reason: str = ""

    # TDI level
    tdi_level: str = "neutral"

    # TF Green State (current chart timeframe momentum)
    green_tf_value: float = 50.0
    green_tf_slope: float = 0.0  # positive = rising, negative = falling

class OrbData(BaseModel):
    or_high: float
    or_low: float
    or_mid: float
    or_width: float
    or_complete: bool
    breakout_direction: Optional[str] = None  # "up", "down", null
    breakout_price: float = 0
    breakout_distance_pips: float = 0  # how far price is beyond OR boundary
    bars_since_breakout: int = 0
    in_entry_window: bool = False
    is_decisive: bool = False  # breakout_distance >= MinDecisiveBreakoutPips
    price_position: str = "inside"

class PositionInfo(BaseModel):
    side: str  # "BUY", "SELL"
    entry_price: float
    unrealized_pnl: float
    unrealized_pnl_pips: float
    mfe_pips: float  # Maximum Favorable Excursion
    giveback_pips: float  # MFE - current profit
    sl_price: float
    tp_price: float
    bars_held: int

class SessionInfo(BaseModel):
    session_name: str  # "london", "newyork", "tokyo", etc.
    phase: str  # "pre", "active", "ending", "closed"
    minutes_to_end: int
    is_trading_time: bool

class MarketSnapshot(BaseModel):
    symbol: str
    timeframe: str
    ask: float
    bid: float
    bars: List[BarData]
    tms: TmsSignals
    orb: Optional[OrbData] = None
    position: Optional[PositionInfo] = None
    session: Optional[SessionInfo] = None
    loss_streak: int = 0
    day_pnl: float = 0
    trades_today: int = 0

# ---- Output Format ----
class AgentDecision(BaseModel):
    action: str  # "BUY", "SELL", "CLOSE_ALL", "HOLD"
    volume_lots: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    reason: str

# ---- System Prompt ----
SYSTEM_PROMPT = """You are an AUTONOMOUS trading agent using TMS for BIAS and ORB for ENTRY.

## Strategy Logic

**TMS = DIRECTIONAL BIAS**
- BULLISH: Green crossed above Red, HA green, Stoch K > D
- BEARISH: Green crossed below Red, HA red, Stoch K < D
- Bias is locked until next cross

**ORB = ENTRY TRIGGER**
- Only enter when ORB breaks in direction of TMS bias
- Breakout must be DECISIVE (breakout_distance_pips >= threshold)
- Entry window: bars_since_breakout <= max_bars_after_breakout

## Decision Rules

### Entry (TMS + ORB alignment)
- TMS BULLISH + ORB breakout UP + is_decisive=true + in_entry_window → BUY
- TMS BEARISH + ORB breakout DOWN + is_decisive=true + in_entry_window → SELL
- Any mismatch → HOLD

### Exit (from TMS signals)
- exit_long = true (TDI flat/hook/checkmark) → CLOSE_ALL
- exit_short = true → CLOSE_ALL
- green_tf_slope turning against position → consider CLOSE_ALL (early warning)

### Position Management
- If position exists and giveback_pips is high → consider tightening SL
- If session.phase = "ending" → CLOSE_ALL (session end)

## Green TF State (Momentum)
- green_tf_value: current TDI Green value (0-100)
- green_tf_slope: positive = rising momentum, negative = falling momentum
- Use slope as early warning: if slope turns against position, momentum is fading

## Output Format (JSON)

{
  "action": "BUY" | "SELL" | "CLOSE_ALL" | "HOLD",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "Brief explanation"
}

## Critical Rules

1. NEVER trade against TMS bias
2. NEVER trade without SL
3. NEVER trade if TMS bias is NEUTRAL
4. ORB breakout must be decisive (is_decisive=true)
5. R:R must be >= 1.5
6. If exit_long/exit_short is true and position open → CLOSE_ALL
7. If session.phase = "ending" → CLOSE_ALL
8. If loss_streak >= 3 → HOLD (no new entries)
"""


@app.post("/trade", response_model=AgentDecision)
async def trade_decision(snapshot: MarketSnapshot):
    logger.info(f"{snapshot.symbol} {snapshot.timeframe} | TMS: {snapshot.tms.bias} | Pos: {snapshot.position is not None} | Session: {snapshot.session.phase if snapshot.session else 'N/A'}")

    user_prompt = build_user_prompt(snapshot)

    try:
        # Use abstracted LLM client (supports Qwen, OpenAI, Claude, DeepSeek)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
        
        # Qwen/OpenAI support response_format, Claude doesn't - handle both
        kwargs = {"temperature": 0.1}
        if hasattr(llm_client, 'client') and hasattr(llm_client.client, 'chat'):
            kwargs["response_format"] = {"type": "json_object"}
        
        result_str = await llm_client.chat(messages, **kwargs)
        
        # Parse JSON (handles markdown code blocks, etc.)
        decision_dict = JSONResponseParser.parse(result_str)

        logger.info(f"Decision: {decision_dict['action']} | {decision_dict.get('reason', '')[:50]}")

        return AgentDecision(**decision_dict)
    except Exception as e:
        logger.error(f"LLM Error: {e}")
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0,
            tp_pips=0,
            reason=f"Error: {e}"
        )


def build_user_prompt(snapshot: MarketSnapshot) -> str:
    """Build structured prompt from pre-computed signals."""
    tms = snapshot.tms

    lines = [
        f"## Market: {snapshot.symbol} on {snapshot.timeframe}",
        f"**Price**: Ask={snapshot.ask:.5f}, Bid={snapshot.bid:.5f}",
        "",
        "### TMS Signals (pre-computed)",
        f"- **Bias**: {tms.bias}",
        f"- Bars since cross: {tms.bars_since_cross}",
        f"- Cross direction: {tms.cross_direction or 'none'}",
        f"- TDI level: {tms.tdi_level}",
        "",
        "**Current bar signals:**",
        f"- cross_up: {tms.cross_up}, cross_down: {tms.cross_down}",
        f"- ha_turned_green: {tms.ha_turned_green}, ha_turned_red: {tms.ha_turned_red}",
        f"- stoch_bull: {tms.stoch_bull}, stoch_bear: {tms.stoch_bear}",
        f"- angle_ok_long: {tms.angle_ok_long}, angle_ok_short: {tms.angle_ok_short}",
        f"- within_window: {tms.within_window}",
        "",
        "**Entry signals:**",
        f"- long_entry: {tms.long_entry}",
        f"- short_entry: {tms.short_entry}",
        "",
        "**Exit signals:**",
        f"- exit_long: {tms.exit_long}",
        f"- exit_short: {tms.exit_short}",
        f"- exit_reason: {tms.exit_reason or 'none'}",
        "",
        "**TF Green State (momentum):**",
        f"- green_tf_value: {tms.green_tf_value:.2f}",
        f"- green_tf_slope: {tms.green_tf_slope:.3f} (positive=rising, negative=falling)",
    ]

    # ORB data
    if snapshot.orb:
        orb = snapshot.orb
        lines.extend([
            "",
            "### ORB (Opening Range Breakout)",
            f"- OR High: {orb.or_high:.5f}",
            f"- OR Low: {orb.or_low:.5f}",
            f"- OR Complete: {orb.or_complete}",
            f"- Breakout: {orb.breakout_direction or 'none'}",
            f"- Breakout distance: {orb.breakout_distance_pips:.1f} pips",
            f"- Is decisive: {orb.is_decisive}",
            f"- Bars since breakout: {orb.bars_since_breakout}",
            f"- In entry window: {orb.in_entry_window}",
            f"- Price position: {orb.price_position}",
        ])

    # Position info
    if snapshot.position:
        pos = snapshot.position
        lines.extend([
            "",
            "### Position",
            f"- Side: {pos.side}",
            f"- Entry: {pos.entry_price:.5f}",
            f"- PnL: {pos.unrealized_pnl:.2f} ({pos.unrealized_pnl_pips:.1f} pips)",
            f"- MFE: {pos.mfe_pips:.1f} pips",
            f"- Giveback: {pos.giveback_pips:.1f} pips",
            f"- SL: {pos.sl_price:.5f}",
            f"- TP: {pos.tp_price:.5f}",
            f"- Bars held: {pos.bars_held}",
        ])
    else:
        lines.extend(["", "### Position: None"])

    # Session info
    if snapshot.session:
        sess = snapshot.session
        lines.extend([
            "",
            "### Session",
            f"- Name: {sess.session_name}",
            f"- Phase: {sess.phase}",
            f"- Minutes to end: {sess.minutes_to_end}",
            f"- Is trading time: {sess.is_trading_time}",
        ])

    # Day stats
    lines.extend([
        "",
        "### Day Stats",
        f"- Loss streak: {snapshot.loss_streak}",
        f"- Day PnL: {snapshot.day_pnl:.2f}",
        f"- Trades today: {snapshot.trades_today}",
    ])

    lines.extend([
        "",
        "---",
        "",
        "## YOUR TASK",
        "",
        "Based on the pre-computed signals:",
        "1. If exit_long/exit_short is true and position open → CLOSE_ALL",
        "2. If session.phase = 'ending' and position open → CLOSE_ALL",
        "3. If long_entry=true and TMS BULLISH and ORB breakout UP and is_decisive → BUY",
        "4. If short_entry=true and TMS BEARISH and ORB breakout DOWN and is_decisive → SELL",
        "5. If loss_streak >= 3 → HOLD",
        "6. Otherwise → HOLD",
        "",
        "Output JSON decision.",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
