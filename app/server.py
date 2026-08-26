import os
import sys
from pathlib import Path
import uvicorn
import json
import logging
from dotenv import load_dotenv

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env
load_dotenv(PROJECT_ROOT / ".env")

import datetime
import logging.handlers

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"

def setup_agent_logging(level=logging.INFO):
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # Daily rotating file handler (14 days backup)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        logs_dir / f"agent_{today}.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    file_handler.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
    console_handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

setup_agent_logging(logging.INFO)
logger = logging.getLogger("AgentFxTrading")
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
from app.llm_client import create_llm_client, JSONResponseParser
from app.portfolio import init_portfolio, get_portfolio_manager
from app.dashboard import router as dashboard_router, broadcast_update
from app.accounts import init_account_registry, get_account_registry
app = FastAPI(title="TMS+ORB Agent Server")

# Mount static files
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")

# Mount dashboard router
app.include_router(dashboard_router)
# Initialize Account Registry
account_registry = init_account_registry("portfolio.db")
account_registry.seed_from_env()

# Initialize Portfolio Manager
portfolio_manager = init_portfolio("portfolio.db")

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

    # TDI Bounce Detection (dnse-kash)
    tdi_bounce_bull: bool = False
    tdi_bounce_bear: bool = False
class MarketRegimeInfo(BaseModel):
    regime: str = "forming"  # "forming", "trending", "choppy", "mixed"
    er_session: Optional[float] = None
    er_recent: Optional[float] = None
    or_flips: int = 0
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
    bot_id: str = "default"  # Bot identifier for portfolio tracking
    symbol: str
    timeframe: str
    tms_timeframe: Optional[str] = "Hour"
    ask: float
    bid: float
    bars: List[BarData]
    tms: TmsSignals
    chart_tms: Optional[TmsSignals] = None
    orb: Optional[OrbData] = None
    market: Optional[MarketRegimeInfo] = None
    position: Optional[PositionInfo] = None
    session: Optional[SessionInfo] = None
    loss_streak: int = 0
    day_pnl: float = 0
    trades_today: int = 0
    account_id: Optional[str] = None
    account_number: str = "0"
    account_type: str = "demo"
    account_label: Optional[str] = None
    account_balance: float = 10000.0
    account_equity: float = 10000.0

# ---- Output Format ----
class AgentDecision(BaseModel):
    action: str  # "BUY", "SELL", "CLOSE_ALL", "HOLD"
    volume_lots: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    reason: str

def build_system_prompt(snapshot: MarketSnapshot) -> str:
    """
    Dynamic System Prompt Factory (inspired by dnse-kash architecture).
    Bakes live asset characteristics, market regime guidelines, and quantitative edge-case rules directly into context.
    """
    is_gold = "XAU" in snapshot.symbol.upper() or "GOLD" in snapshot.symbol.upper()
    asset_type = "Gold (Commodity/Metals)" if is_gold else "Forex Major/Cross"
    
    # Pip scale guidelines based on asset
    sl_guideline = "20.0 - 80.0 pips" if is_gold else "6.0 - 30.0 pips"
    tp_guideline = "30.0 - 250.0 pips" if is_gold else "10.0 - 80.0 pips"
    default_sl = 30.0 if is_gold else 10.0
    default_tp = 60.0 if is_gold else 20.0

    current_regime = snapshot.market.regime if snapshot.market else "mixed"
    regime_guideline = ""
    if current_regime == "trending":
        regime_guideline = (
            "• CURRENT REGIME IS TRENDING: The execution engine (cBot) automatically DISABLES fixed TP (Trend TP Disabled). "
            "Your trade will ride the full momentum wave managed by dynamic Trailing Stop and Giveback Floor. "
            "Declare ambitious TP or open target and focus on accurate entry timing & SL boundary."
        )
    elif current_regime == "choppy":
        regime_guideline = (
            "• CURRENT REGIME IS CHOPPY (High failed breakouts / OR flips): The market is oscillating and hunting stops. "
            "The default and safest action is HOLD unless a fresh, extraordinary setup with strong momentum slope emerges. "
            "Never chase extended moves in a choppy regime."
        )
    else:
        regime_guideline = (
            "• CURRENT REGIME IS MIXED/FORMING: Maintain standard trading discipline with R:R >= 1.5."
        )

    return f"""You are an AUTONOMOUS quantitative trading agent running the TMS (Trend Momentum Signal) + ORB (Opening Range Breakout) strategy for {snapshot.symbol} ({asset_type}).

## Core Contract: "LLM proposes, Code disposes"
You analyze market structure and propose trade actions. The deterministic execution harness (cBot + Portfolio Manager) enforces hard guardrails (spread checks, correlation limits, trailing stops, and EOD force-flatten). Always output valid structured JSON.

## Strategy Logic

### 1. TMS (Trend Momentum Signal) = DIRECTIONAL BIAS
- **BULLISH**: TDI Green crossed above Red, Heikin Ashi is Green, Stochastic K > D.
- **BEARISH**: TDI Green crossed below Red, Heikin Ashi is Red, Stochastic K < D.
- **NEUTRAL**: Lines intertwined or consolidating. NEVER enter when bias is NEUTRAL.
- Bias is strictly locked until the next confirmed reverse cross. Entries MUST align with current TMS bias.

### 2. ORB (Opening Range Breakout) = ENTRY TRIGGER
- Opening Range (OR) defines the high/low of the first 15 minutes of the active session.
- Valid entry requires price closing beyond OR boundary in the direction of TMS bias.
- Breakout must be DECISIVE (breakout_distance_pips >= threshold) and within entry window (bars_since_breakout <= 5).
### 3. ENTRY MODELS (DIRECT BREAKOUT vs RETEST + TDI BOUNCE)
- **Model 1: Direct Momentum Breakout**: Price closes decisively beyond OR boundary with steep TDI slope in bias direction. Valid when in entry window (`bars_since_breakout <= 5`).
- **Model 2: Breakout Retest + TDI Bounce (High R:R Continuation)**:
  - Price broke out of OR, pulled back toward OR boundary (or consolidation zone) without breaking opposite structure.
  - **TDI Bounce Trigger**: `tdi_bounce_bull = true` (Green was near Red and bounced up continuing Bullish trend) or `tdi_bounce_bear = true` (Green was near Red and bounced down continuing Bearish trend).
  - When a TDI Bounce occurs in alignment with Macro Bias, this confirms trend continuation after pullback -> Strongly favors BUY / SELL even if `bars_since_breakout > 5`.

### 4. Market Regime (Kaufman Efficiency Ratio & Chop Detection)
- **er_session / er_recent**: Kaufman Efficiency Ratio (|net move| / total path, 1.0 = pure directional trend, ~0 = pure oscillation).
- **or_flips**: Number of times price broke outside OR and closed back inside (flips >= 5 indicates chop trap day).
{regime_guideline}

### 5. Quantitative Edge-Case Rules (Battle-Tested Discipline)
- **BIAS-FRESH Exception**: When a TMS cross JUST occurred (bars_since_cross <= 1), treat early breakout momentum as the START of a new trend leg rather than an extended move. Entering in the fresh bias direction is strongly favored.
- **TDI BOUNCE EXCEPTION TO ANTI-CHASE**: Standard Anti-Chase blocks entry when `bars_since_breakout >= 4` without a pullback. However, if a valid **TDI Bounce** is confirmed (`tdi_bounce_bull` or `tdi_bounce_bear`), the pullback has occurred and resolved in favor of the trend -> Enter on the bounce.
- **ANTI-CHASE Rule**: When bars_since_breakout >= 4 under an OLD bias (bars_since_cross >= 5) without a pullback/bounce, DO NOT chase at extremes. Declare HOLD.
- **POSITION MEMORY & GIVEBACK FLOOR**:
  - position.mfe_pips = PEAK floating profit reached.
  - position.giveback_pips = Profit given back from peak (MFE - Current PnL).
  - **Golden Rule**: Never let a large winning trade turn into a losing trade without a deliberate technical reason. If giveback is high and momentum slope turns negative, declare CLOSE_ALL to protect capital.
- **POST-TP GATE (Anti-FOMO)**: Once a trade hits Take Profit, the deterministic engine ARMS a blocker (`post_tp_gate_active = true`) preventing immediate re-entry in the same direction (`post_tp_gate_side`). It unlocks automatically only when a Pullback, OR Touch, TDI Bounce, or Bias Flip occurs.
  - **Golden Rule**: If `post_tp_gate_active` is true and you want to enter in the `post_tp_gate_side` direction, you MUST declare HOLD and wait for the pullback/bounce to unlock the gate.
- **MOMENTUM SLOPE AS EARLIEST EXIT WARNING**:
  - green_tf_slope turning negative for a BUY position (or positive for a SELL position) is the earliest warning that momentum is exhausting before full TDI cross confirms.

### 6. Parameter Guidelines for {snapshot.symbol}
- Realistic TP Distance: {tp_guideline} (Suggested: ~{default_tp} pips)
- Minimum Risk-to-Reward: R:R >= 1.5

## Decision Rules Summary

### Entry Criteria (ALL must be satisfied):
1. TMS Bias is clearly BULLISH (for BUY) or BEARISH (for SELL).
2. Valid Entry Trigger:
   - EITHER Direct ORB Breakout (is_decisive = true, in_entry_window = true) aligned with bias
   - OR Retest / Continuation with confirmed TDI Bounce (`tdi_bounce_bull = true` for BUY, `tdi_bounce_bear = true` for SELL).
3. Session is active (not ending / not closed).
4. Loss streak < 3.
-> Any mismatch or conflicting signal -> HOLD.
### Exit Criteria:
1. exit_long = true (for BUY) or exit_short = true (for SELL) -> CLOSE_ALL.
2. green_tf_slope turning sharply against position -> CLOSE_ALL (early momentum exit).
3. session.phase = "ending" -> CLOSE_ALL (EOD safety).

## Output Format (JSON only)

{{
  "action": "BUY" | "SELL" | "CLOSE_ALL" | "HOLD",
  "volume_lots": 0.01,
  "sl_pips": {default_sl},
  "tp_pips": {default_tp},
  "reason": "Clear, concise technical justification (TMS bias, ORB breakout, Regime ER, Momentum slope)"
}}
"""


def _resolve_account(snapshot: MarketSnapshot) -> str:
    registry = get_account_registry()
    return registry.upsert_from_bot(
        account_number=snapshot.account_number,
        account_type=snapshot.account_type,
        label=snapshot.account_label,
        balance=snapshot.account_balance,
        equity=snapshot.account_equity,
    )

def evaluate_cycle_gate(snapshot: MarketSnapshot) -> Optional[AgentDecision]:
    """
    Deterministic Cycle Gate (Cost Gate).
    Evaluates whether an expensive LLM call can be safely bypassed with an immediate deterministic action.
    Returns AgentDecision if gated, or None if LLM call is required.
    """
    # 1. When we HAVE an open position:
    if snapshot.position is not None:
        # Check if session is ending -> Deterministic CLOSE_ALL
        if snapshot.session and snapshot.session.phase in ("ending", "closed"):
            return AgentDecision(
                action="CLOSE_ALL",
                volume_lots=0.0,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Session {snapshot.session.phase} (EOD close)"
            )
        # Check if explicit TMS exit signal fired for current position side
        pos_side = snapshot.position.side.upper()
        if (pos_side == "BUY" and snapshot.tms.exit_long) or (pos_side == "SELL" and snapshot.tms.exit_short):
            return AgentDecision(
                action="CLOSE_ALL",
                volume_lots=0.0,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: TMS exit signal triggered ({snapshot.tms.exit_reason})"
            )
        # Position is open and needs active LLM monitoring (momentum slope, MFE giveback, etc.)
        return None

    # 2. When we DO NOT have an open position (Flat):
    # Only candidate setups with aligned TMS + ORB should reach LLM.

    # Gate 2.1: Session Gate
    if snapshot.session is not None:
        if not snapshot.session.is_trading_time or snapshot.session.phase in ("pre", "closed"):
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Outside trading session (phase={snapshot.session.phase})"
            )
        if snapshot.session.phase == "ending":
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Session ending ({snapshot.session.minutes_to_end}m remaining, no new entries)"
            )

    # Gate 2.2: Loss Streak Gate
    if snapshot.loss_streak >= 3:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Loss streak protection active ({snapshot.loss_streak} consecutive losses)"
        )
    bias = (snapshot.tms.bias or "NEUTRAL").upper()

    # Gate 2.3: TMS Bias Gate

    # Gate 2.3.1: Post-TP Gate (Anti-FOMO)
    if snapshot.tms.post_tp_gate_active:
        gate_side = (snapshot.tms.post_tp_gate_side or "").upper()
        if gate_side == bias: # Block entry if gate is active and bias matches
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Post-TP Gate is ACTIVE blocking {gate_side} (waiting for Pullback/Bounce)"
            )

    if bias == "NEUTRAL":
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason="Cycle gate: TMS bias is NEUTRAL"
        )

    # Gate 2.4: ORB Breakout Gate
    orb = snapshot.orb
    if orb is None or not orb.breakout_direction or orb.breakout_direction.lower() == "none":
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason="Cycle gate: Price inside Opening Range (no breakout)"
        )

    if not orb.is_decisive:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Breakout not decisive ({orb.breakout_distance_pips:.1f}p < threshold)"
        )
    # Post-TP Gate and Anti-Chase Bypass rule:
    # If there is a TDI Bounce, we ignore the Entry Window constraint!
    has_bounce = (bias == "BULLISH" and snapshot.tms.tdi_bounce_bull) or (bias == "BEARISH" and snapshot.tms.tdi_bounce_bear)
    if snapshot.chart_tms:
        has_bounce = has_bounce or (bias == "BULLISH" and snapshot.chart_tms.tdi_bounce_bull) or (bias == "BEARISH" and snapshot.chart_tms.tdi_bounce_bear)

    if not orb.in_entry_window and not has_bounce:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Outside entry window (bars_since_breakout={orb.bars_since_breakout}) with no Bounce"
        )


    # Gate 2.5: Directional Alignment Gate (TMS vs ORB)
    orb_dir = orb.breakout_direction.lower()
    if bias == "BULLISH" and orb_dir != "up":
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Conflict - TMS {bias} vs ORB breakout {orb_dir}"
        )
    if bias == "BEARISH" and orb_dir != "down":
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Conflict - TMS {bias} vs ORB breakout {orb_dir}"
        )

    # Gate 2.6: Choppy Market Gate (Chop trap brake)
    if snapshot.market and snapshot.market.regime == "choppy" and snapshot.market.or_flips >= 5:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Market is CHOPPY ({snapshot.market.or_flips} failed OR breakouts)"
        )

    # All entry criteria met! Valid candidate setup -> Invoke LLM for entry sizing & SL/TP validation
    return None


@app.post("/trade", response_model=AgentDecision)
async def trade_decision(snapshot: MarketSnapshot):
    account_id = _resolve_account(snapshot)
    regime_str = snapshot.market.regime if snapshot.market else "N/A"
    er_str = f"{snapshot.market.er_session:.2f}" if snapshot.market and snapshot.market.er_session is not None else "N/A"
    pos_str = f"{snapshot.position.side} pnl={snapshot.position.unrealized_pnl_pips:.1f}p" if snapshot.position else "FLAT"
    sess_str = f"{snapshot.session.phase} ({snapshot.session.minutes_to_end}m)" if snapshot.session else "N/A"
    
    logger.info(
        f"[SNAPSHOT] {account_id}/{snapshot.bot_id} | {snapshot.symbol} {snapshot.timeframe} | "
        f"Bid/Ask={snapshot.bid:.5f}/{snapshot.ask:.5f} | TMS={snapshot.tms.bias} (age={snapshot.tms.bars_since_cross}) | "
        f"Regime={regime_str} (ER={er_str}) | Pos={pos_str} | Session={sess_str}"
    )

    # Deterministic Cycle Gate (Cost Gate - skip LLM when decision is deterministic)
    gated_decision = evaluate_cycle_gate(snapshot)
    if gated_decision is not None:
        logger.info(f"[CYCLE GATE] GATED: {gated_decision.action} | Reason: {gated_decision.reason}")
        return gated_decision

    # Check portfolio risk before allowing new trades
    if snapshot.position is None:  # No open position, might want to open new one
        portfolio_status = portfolio_manager.get_portfolio_status(account_id=account_id)
        open_positions = portfolio_status['total_positions']
        
        # Check if we can open a new position
        can_trade, reason = portfolio_manager.check_risk(
            symbol=snapshot.symbol,
            side="BUY",  # Will be determined by LLM, just checking capacity
            volume=0.01,  # Minimum volume
            sl_pips=10.0,  # Reasonable SL
            account_balance=snapshot.account_balance,
            account_id=account_id
        )
        
        if not can_trade:
            logger.warning(f"[{account_id}/{snapshot.bot_id}] Portfolio risk check failed: {reason}")
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0,
                tp_pips=0,
                reason=f"Portfolio constraint: {reason}"
            )

    user_prompt = build_user_prompt(snapshot)

    try:
        # Use abstracted LLM client (supports Qwen, OpenAI, Claude, Gemini, DeepSeek)
        system_prompt = build_system_prompt(snapshot)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # Qwen/OpenAI support response_format, Claude/Gemini don't - handle both
        kwargs = {"temperature": 0.1}
        if hasattr(llm_client, 'client') and hasattr(llm_client.client, 'chat'):
            kwargs["response_format"] = {"type": "json_object"}
        
        result_str = await llm_client.chat(messages, **kwargs)
        
        # Parse JSON (handles markdown code blocks, etc.)
        decision_dict = JSONResponseParser.parse(result_str)
        logger.info(
            f"[LLM DECISION] {account_id}/{snapshot.bot_id} -> Action: {decision_dict['action']} | "
            f"Vol: {decision_dict.get('volume_lots', 0.01)} lots | SL: {decision_dict.get('sl_pips', 0)}p | "
            f"TP: {decision_dict.get('tp_pips', 0)}p | Reason: {decision_dict.get('reason', '')}"
        )

        return AgentDecision(**decision_dict)
    except Exception as e:
        logger.error(f"[{account_id}/{snapshot.bot_id}] LLM Error: {e}")
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0,
            tp_pips=0,
            reason=f"Error: {e}"
        )


@app.post("/portfolio/report")
async def report_position(request: dict):
    """
    Report position changes from cBot.
    Expected format: {"bot_id": "...", "action": "open|close", "symbol": "...", ...}
    """
    try:
        bot_id = request.get("bot_id", "default")
        action = request.get("action")
        symbol = request.get("symbol")
        
        account_number = request.get("account_number", "0")
        account_type = request.get("account_type", "demo")
        account_label = request.get("account_label")
        account_balance = request.get("account_balance", 0)
        account_equity = request.get("account_equity", 0)
        
        registry = get_account_registry()
        account_id = registry.upsert_from_bot(
            account_number=account_number,
            account_type=account_type,
            label=account_label,
            balance=account_balance,
            equity=account_equity
        )
        
        if action == "open":
            side = request.get("side")
            volume = request.get("volume", 0.01)
            entry_price = request.get("entry_price")
            sl_pips = request.get("sl_pips")
            tp_pips = request.get("tp_pips")
            
            success = portfolio_manager.register_position(
                bot_id=bot_id,
                symbol=symbol,
                side=side,
                volume=volume,
                entry_price=entry_price,
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                account_id=account_id
            )
            
            if success:
                logger.info(f"[PORTFOLIO EVENT] OPEN | {account_id}/{bot_id} | {symbol} {side} {volume} lots @ {entry_price} | SL={sl_pips}p TP={tp_pips}p")
                try:
                    await broadcast_update()
                except Exception:
                    pass
                return {"status": "success", "message": "Position registered"}
            else:
                return {"status": "error", "message": "Failed to register position"}
        
        elif action in ("ping", "sync"):
            logger.info(f"[PORTFOLIO EVENT] SYNC | {account_id}/{bot_id} | Balance: ${account_balance:.2f} | Equity: ${account_equity:.2f}")
            try:
                await broadcast_update()
            except Exception:
                pass
            return {"status": "success", "message": f"Account {account_id} synced", "account_id": account_id}
        
        elif action == "close":
            exit_price = request.get("exit_price")
            pnl = request.get("pnl", 0)
            
            success = portfolio_manager.close_position(
                bot_id=bot_id,
                symbol=symbol,
                exit_price=exit_price,
                pnl=pnl,
                account_id=account_id
            )
            
            if success:
                try:
                    await broadcast_update()
                except Exception:
                    pass
                logger.info(f"[PORTFOLIO EVENT] CLOSE | {account_id}/{bot_id} | {symbol} | PnL: ${pnl:.2f}")
                return {"status": "success", "message": "Position closed"}
            else:
                return {"status": "error", "message": "Failed to close position"}
            return {"status": "error", "message": f"Unknown action: {action}"}
    
    except Exception as e:
        logger.error(f"Portfolio report error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/portfolio/status")
async def get_portfolio_status(account_id: Optional[str] = None):
    """Get current portfolio status."""
    try:
        status = portfolio_manager.get_portfolio_status(account_id=account_id)
        return status
    except Exception as e:
        logger.error(f"Portfolio status error: {e}")
        return {"error": str(e)}

@app.get("/api/dashboard/accounts")
async def api_dashboard_accounts():
    """List all accounts for dashboard selector."""
    try:
        registry = get_account_registry()
        return {"accounts": registry.list_accounts()}
    except Exception as e:
        logger.error(f"Dashboard accounts error: {e}")
        return {"error": str(e)}


def build_user_prompt(snapshot: MarketSnapshot) -> str:
    """Build structured prompt from pre-computed signals."""
    tms = snapshot.tms
    macro_tf = snapshot.tms_timeframe or "Macro"

    lines = [
        f"## Market: {snapshot.symbol} | Chart: {snapshot.timeframe} | Macro TMS: {macro_tf}",
        f"**Price**: Ask={snapshot.ask:.5f}, Bid={snapshot.bid:.5f}",
        "",
        f"### Macro TMS Signals ({macro_tf} - Directional Bias)",
        f"- **Macro Bias**: {tms.bias}",
        f"- Bars since cross ({macro_tf}): {tms.bars_since_cross}",
        f"- Cross direction: {tms.cross_direction or 'none'}",
        f"- TDI level: {tms.tdi_level}",
        f"- Macro Green Slope: {tms.green_tf_slope:.3f}",
        f"- HA Bullish: {tms.long_entry}, Stoch Bullish: {tms.stoch_bull}",
        f"- Macro TDI Bounce: Bull={tms.tdi_bounce_bull}, Bear={tms.tdi_bounce_bear}",
        f"- Post-TP Gate Active: {tms.post_tp_gate_active} (Blocking {tms.post_tp_gate_side or 'None'})",
    ]

    # Chart execution TMS signals (e.g. M15/M5)
    if snapshot.chart_tms:
        ctms = snapshot.chart_tms
        lines.extend([
            "",
            f"### Chart Execution Signals ({snapshot.timeframe} - Timing & Momentum)",
            f"- Chart HA Turned Green: {ctms.ha_turned_green}, Turned Red: {ctms.ha_turned_red}",
            f"- Chart Stoch Bull: {ctms.stoch_bull}, Bear: {ctms.stoch_bear}",
            f"- Chart Green Momentum Value: {ctms.green_tf_value:.2f}",
            f"- Chart Green Momentum Slope: {ctms.green_tf_slope:.3f} (positive=rising, negative=falling)",
            f"- Chart TDI Bounce: Bull={ctms.tdi_bounce_bull}, Bear={ctms.tdi_bounce_bear}",
            f"- Chart Exit Signals: exit_long={ctms.exit_long}, exit_short={ctms.exit_short} ({ctms.exit_reason or 'none'})",
        ])
    else:
        lines.extend([
            "",
            f"**Exit signals:**",
            f"- exit_long: {tms.exit_long}",
            f"- exit_short: {tms.exit_short}",
            f"- exit_reason: {tms.exit_reason or 'none'}",
        ])
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

    # Market regime info
    if snapshot.market:
        mkt = snapshot.market
        er_sess_str = f"{mkt.er_session:.2f}" if mkt.er_session is not None else "N/A"
        er_rec_str = f"{mkt.er_recent:.2f}" if mkt.er_recent is not None else "N/A"
        lines.extend([
            "",
            "### Market Regime",
            f"- Regime: {mkt.regime}",
            f"- ER Session: {er_sess_str}",
            f"- ER Recent (1h): {er_rec_str}",
            f"- OR Flips (Failed Breakouts): {mkt.or_flips}",
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
    host = os.getenv("SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run("app.server:app", host=host, port=port, reload=True)
