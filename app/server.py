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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from typing import Optional, List

def sanitize_bot_id(bot_id: Optional[str]) -> str:
    if not bot_id:
        return "default"
    cleaned = str(bot_id).strip().strip("\"'“”‘’`")
    if " --" in cleaned:
        cleaned = cleaned.split(" --")[0].strip()
    return cleaned.strip("\"'“”‘’`") or "default"
from app.llm_client import create_llm_client, JSONResponseParser
from app.portfolio import init_portfolio, get_portfolio_manager
from app.dashboard import router as dashboard_router, broadcast_update
from app.accounts import init_account_registry, get_account_registry
app = FastAPI(title="TMS+ORB Agent Server")

# Mount static files
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")

# Mount dashboard router
app.include_router(dashboard_router)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = PROJECT_ROOT / "static" / "favicon.png"
    if favicon_path.exists():
        return FileResponse(favicon_path)
    return ""
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
    # TMS Heikin-Ashi / Stoch / TDI fields
    ha_color: Optional[str] = None
    tdi_green: Optional[float] = None
    tdi_red: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None

    # Standard OHLCV fields (used in Judas Sweep / SMC)
    time: Optional[str] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None

class TmsSignals(BaseModel):
    # Bias
    bias: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    bars_since_cross: int = 0
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
    price_above_ema: bool = False
    price_below_ema: bool = False
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

    # Post-TP Gate State
    post_tp_gate_active: bool = False
    post_tp_gate_side: Optional[str] = None

class MarketRegimeInfo(BaseModel):
    regime: str = "forming"  # "forming", "trending", "choppy", "mixed"
    er_session: Optional[float] = None
    er_recent: Optional[float] = None
    or_flips: int = 0

class OrbData(BaseModel):
    or_high: float = 0.0
    or_low: float = 0.0
    or_mid: float = 0.0
    or_width: float = 0.0
    or_complete: bool = False
    breakout_direction: Optional[str] = None  # "up", "down", null
    breakout_price: float = 0.0
    breakout_distance_pips: float = 0.0  # how far price is beyond OR boundary
    bars_since_breakout: int = 0
    in_entry_window: bool = False
    is_decisive: bool = False  # breakout_distance >= MinDecisiveBreakoutPips
    price_position: str = "inside"

class PositionInfo(BaseModel):
    side: Optional[str] = None  # "BUY", "SELL"
    type: Optional[str] = None  # Alias for side used in some cBots
    id: Optional[int] = None
    entry_price: float = 0.0
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    unrealized_pnl_pips: float = 0.0
    pnl: Optional[float] = None  # Alias for unrealized_pnl
    mfe_pips: float = 0.0  # Maximum Favorable Excursion
    giveback_pips: float = 0.0  # MFE - current profit
    sl_price: float = 0.0
    tp_price: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    bars_held: int = 0
    duration_minutes: float = 0.0
    volume: Optional[float] = None

    @property
    def resolved_side(self) -> str:
        return (self.side or self.type or "BUY").upper()

    @property
    def resolved_pnl(self) -> float:
        return self.unrealized_pnl if self.unrealized_pnl != 0.0 else (self.pnl or 0.0)

class SessionInfo(BaseModel):
    session_name: str = "london"  # "london", "newyork", "tokyo", etc.
    phase: str = "active"  # "pre", "active", "ending", "closed"
    minutes_to_end: int = 0
    is_trading_time: bool = True

# ---- Judas Sweep / Smart Money Concepts (SMC) Data Models ----
class StrategyData(BaseModel):
    tema1: float = 0.0
    tema2: float = 0.0
    rsi: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    recent_high: float = 0.0
    recent_low: float = 0.0
    asian_high: float = 0.0
    asian_low: float = 0.0
    asian_range_pips: float = 0.0
    killzone_session: str = "NONE"
    bias_direction: str = "NONE"
    traditional_signal: str = "NONE"
    signal_window_bars: int = 0

class SwingStructure(BaseModel):
    last_swing_high: float = 0.0
    swing_high_type: Optional[str] = None
    last_swing_low: float = 0.0
    swing_low_type: Optional[str] = None
    prev_swing_high: float = 0.0
    prev_swing_low: float = 0.0
    market_structure: Optional[str] = None

class TimeframeContext(BaseModel):
    timeframe: Optional[str] = None
    fast_tema: float = 0.0
    slow_tema: float = 0.0
    rsi: float = 0.0
    trend_bias: Optional[str] = None
    high_35: float = 0.0
    low_35: float = 0.0
    close: float = 0.0
    swing_structure: Optional[SwingStructure] = None

class MultiTimeframeData(BaseModel):
    current_tf: Optional[TimeframeContext] = None
    h1_tf: Optional[TimeframeContext] = None
    h4_tf: Optional[TimeframeContext] = None

class ActivePosition(BaseModel):
    id: Optional[int] = None
    symbol: Optional[str] = None
    trade_type: Optional[str] = None
    volume: float = 0.0
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    entry_time: Optional[str] = None

class HistoricalTrade(BaseModel):
    position_id: Optional[int] = None
    symbol: Optional[str] = None
    trade_type: Optional[str] = None
    volume: float = 0.0
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None

class MarketSnapshot(BaseModel):
    request_id: Optional[str] = None
    bot_id: str = "default"  # Bot identifier for portfolio tracking
    symbol: str
    timeframe: str
    tms_timeframe: Optional[str] = "Hour"
    ask: float
    bid: float
    atr_pips: Optional[float] = None
    bars: List[BarData] = []
    tms: Optional[TmsSignals] = None
    chart_tms: Optional[TmsSignals] = None
    orb: Optional[OrbData] = None
    market: Optional[MarketRegimeInfo] = None
    position: Optional[PositionInfo] = None
    session: Optional[SessionInfo] = None
    strategy: Optional[StrategyData] = None
    multi_timeframe: Optional[MultiTimeframeData] = None
    active_positions: Optional[List[ActivePosition]] = None
    recent_history: Optional[List[HistoricalTrade]] = None
    loss_streak: int = 0
    day_pnl: float = 0.0
    trades_today: int = 0
    account_id: Optional[str] = None
    account_number: str = "0"
    account_type: str = "demo"
    account_label: Optional[str] = None
    account_balance: float = 10000.0
    account_equity: float = 10000.0

    @field_validator("bot_id", mode="before")
    @classmethod
    def clean_bot_id(cls, v):
        return sanitize_bot_id(v)

    @field_validator("account_label", mode="before")
    @classmethod
    def clean_account_label(cls, v):
        if not v:
            return None
        cleaned = str(v).strip().strip("\"'“”`")
        return cleaned or None

# ---- Output Format ----
class AgentDecision(BaseModel):
    action: str  # "BUY", "SELL", "CLOSE_ALL", "HOLD", "ADJUST"
    volume_lots: float = 0.01
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    new_sl_price: float = 0.0
    new_tp_price: float = 0.0
    confidence: float = 80.0
    reason: str
    request_id: Optional[str] = None
    bot_id: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None

def is_judas_sweep_bot(snapshot: MarketSnapshot) -> bool:
    """Detect whether snapshot belongs to an Asian Range Judas Sweep / SMC bot."""
    if snapshot.strategy is not None and (
        snapshot.strategy.asian_high > 0 or 
        snapshot.strategy.killzone_session not in ("NONE", "Outside Killzones", "")
    ):
        return True
    bot_name = (snapshot.bot_id or "").lower()
    return "judas" in bot_name or "asian" in bot_name or "sweep" in bot_name
def format_price(price: Optional[float], symbol: str) -> str:
    """Format price dynamically according to symbol asset class and decimal convention."""
    if price is None:
        return "0.0"
    sym = (symbol or "").upper()
    if "JPY" in sym:
        return f"{price:.3f}"
    elif any(k in sym for k in ["XAU", "GOLD", "US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40", "BTC", "ETH", "SOL", "XRP"]):
        return f"{price:.2f}"
    else:
        return f"{price:.5f}"

def build_system_prompt(snapshot: MarketSnapshot) -> str:
    """
    Dynamic System Prompt Factory (inspired by dnse-kash architecture).
    Bakes live asset characteristics, market regime guidelines, and quantitative edge-case rules directly into context.
    """
    sym_up = snapshot.symbol.upper()
    is_gold = "XAU" in sym_up or "GOLD" in sym_up
    is_crypto = any(cr in sym_up for cr in ["BTC", "ETH", "SOL", "XRP", "CRYPTO"])
    is_index = any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40"])

    if is_crypto:
        asset_type = "Cryptocurrency (High Volatility Momentum)"
    elif is_gold:
        asset_type = "Gold (Commodity/Metals)"
    elif is_index:
        asset_type = "Stock Index (High Beta Momentum)"
    else:
        asset_type = "Forex Major/Cross"

    current_regime = snapshot.market.regime if snapshot.market else "mixed"
    regime_guideline = ""
    if current_regime == "trending":
        regime_guideline = (
            "• CURRENT REGIME IS TRENDING: The execution engine (cBot) automatically DISABLES fixed TP (Trend TP Disabled). "
            "Your trade will ride the full momentum wave managed by dynamic Trailing Stop and Giveback Floor. "
            "Focus on accurate entry timing & direction — SL/TP are sized by the ATR engine."
        )
    elif current_regime == "choppy":
        regime_guideline = (
            "• CURRENT REGIME IS CHOPPY (High failed breakouts / OR flips): The market is oscillating and hunting stops. "
            "The default and safest action is HOLD unless a fresh, extraordinary setup with strong momentum slope emerges. "
            "Never chase extended moves in a choppy regime."
        )
    else:
        regime_guideline = (
            "• CURRENT REGIME IS MIXED/FORMING: Maintain standard trading discipline; entry only on confirmed setups."
        )

    return f"""You are an AUTONOMOUS quantitative trading agent running the TMS (Trend Momentum Signal) + ORB (Opening Range Breakout) strategy for {snapshot.symbol} ({asset_type}).

## Core Contract: "LLM proposes, Code disposes"
You analyze market structure and propose trade actions. The deterministic execution harness (cBot + Portfolio Manager) enforces hard guardrails (spread checks, correlation limits, trailing stops, and EOD force-flatten). Always output valid structured JSON.

## SL/TP ARE COMPUTED BY THE ATR ENGINE — DO NOT GUESS PIPS
- The cBot overrides any sl_pips / tp_pips you return with ATR-based distances
  (SL = ATR SL Multiplier x ATR, TP = ATR TP Multiplier x ATR, clamped by Min/Max ATR guardrails).
- Return sl_pips = 0 and tp_pips = 0. Your job is DIRECTION (action), SIZING (volume_lots), and TIMING — never pip targets.

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
- A breakout that has re-entered the range is reported as NO breakout (direction = none) — never trade a failed breakout.

### 3. ENTRY MODELS (DIRECT BREAKOUT vs RETEST + TDI BOUNCE)
- **Model 1: Direct Momentum Breakout**: Price closes decisively beyond OR boundary with steep TDI slope in bias direction. Valid when in entry window (`bars_since_breakout <= 5`) and distance is NOT overextended (`breakout_distance <= 2.5x ATR`).
- **Model 2: Breakout Retest + TDI Bounce (High R:R Continuation)**:
  - Price broke out of OR, pulled back toward OR boundary / EMA5 without breaking opposite structure.
  - **TDI Bounce Trigger**: `tdi_bounce_bull = true` (Bullish continuation) or `tdi_bounce_bear = true` (Bearish continuation).
  - **Strict Price Action Verification**: A TDI Bounce is ONLY valid when price is properly positioned relative to the 5 EMA (`price_above_ema = true` for BUY, `price_below_ema = true` for SELL), `bars_since_breakout <= 10`, and distance is NOT overextended. NEVER enter a bounce trade when price is overextended far from EMA5 or floating at extreme exhaustion levels.
### 4. Market Regime (Kaufman Efficiency Ratio & Chop Detection)
- **er_session / er_recent**: Kaufman Efficiency Ratio (|net move| / total path, 1.0 = pure directional trend, ~0 = pure oscillation).
- **or_flips**: Number of times price broke outside OR and closed back inside (flips >= 5 indicates chop trap day).
{regime_guideline}

### 5. Quantitative Edge-Case Rules (Battle-Tested Discipline)
- **ANTI-OVEREXTENSION RULE**: NEVER buy or sell when price is already overextended (> 2.5x ATR, or > 1500 pips on Gold / $15.00, > 30,000 pips on BTC, > 1500 pips on Indices, > 50 pips on Forex) from the OR boundary. Chasing extreme exhaustion moves is strictly prohibited -> Declare HOLD.
- **BIAS-FRESH Exception**: When a TMS cross JUST occurred (bars_since_cross <= 1), treat early breakout momentum as the START of a new trend leg rather than an extended move. Entering in the fresh bias direction is strongly favored.
- **TDI BOUNCE EXCEPTION TO ANTI-CHASE**: Standard Anti-Chase blocks entry when `bars_since_breakout >= 4` without a pullback. However, if a valid **TDI Bounce** is confirmed (`tdi_bounce_bull` or `tdi_bounce_bear`) AND `bars_since_breakout <= 10` AND price is near EMA5, the pullback has occurred and resolved in favor of the trend -> Enter on the bounce.
- **ANTI-CHASE Rule**: When bars_since_breakout >= 4 under an OLD bias (bars_since_cross >= 5) without a pullback/bounce, DO NOT chase at extremes. Declare HOLD.
- **POST-TP GATE (Anti-FOMO)**: Once a trade hits Take Profit or closes after a major win, the deterministic engine ARMS a blocker (`post_tp_gate_active = true`) preventing immediate re-entry in the same direction (`post_tp_gate_side`). It unlocks automatically only when a real Pullback (>= 0.5x ATR), OR Touch, or Bias Flip occurs. Never re-enter immediately at the peak of a move without a structural pullback.
- **POSITION BREATHING ROOM & PATIENCE**:
  - Trading requires room for normal market fluctuations. Never prematurely cut an open position on minor pullbacks or single-candle noise if price is still structurally valid and aligned with the macro trend. Stop Loss and Trailing Stop are dynamically managed by the engine via ATR.
- **POSITION MEMORY & GIVEBACK FLOOR (PROFIT LOCK-IN)**:
  - position.mfe_pips = PEAK floating profit reached.
  - position.giveback_pips = Profit given back from peak (MFE - Current PnL).
  - **Golden Rule**: Giveback protection activates on winning trades. For Forex/Metals, activation starts when MFE >= 0.8x ATR, triggering CLOSE_ALL if giveback >= 40% of peak MFE with momentum stall/reversal. For Indices (US30, USTEC, DE40, etc.), activation requires MFE >= 1.5x ATR (min 1000 pips / 100 points for US30) and giveback >= 55% of peak MFE with momentum stall/reversal, allowing natural intraday index swings (50–300 points). NEVER let a winning trade turn into a full loss.
- **ASSET SCALE & RISK DISCIPLINE (CRYPTO / INDICES / METALS / FOREX)**:
  - Stop Loss is hard-capped by ATR guardrails and max dollar risk ($10–$15 max per trade on a $700 account).
  - On Gold (XAUUSD), 1 pip = $0.01. Do NOT trade with massive SLs > 1200 pips ($12).
  - On Crypto and Indices, several hundred pips is minimal noise (a small fraction of 1 ATR). Evaluate the actual chart trend structure.
### 6. Risk & Sizing (handled by the engine — context only)
- SL/TP distances are computed by the cBot ATR engine (ATR on the chart timeframe); you do NOT provide them.
- Position volume is computed by the engine from risk-per-trade % and ATR-based SL. `volume_lots` you return is a *relative* suggestion only and may be overridden.

## Decision Rules Summary

### Entry Criteria (ALL must be satisfied):
1. TMS Bias is clearly BULLISH (for BUY) or BEARISH (for SELL).
2. Valid Entry Trigger (Any of the following models):
   - **Model 1 (Direct Breakout)**: ORB Breakout (is_decisive = true, in_entry_window = true) AND price agrees with 5 EMA (price_above_ema = true for BUY, price_below_ema = true for SELL).
   - **Model 2 (Retest + TDI Bounce)**: Breakout Retest/Continuation with confirmed TDI Bounce (`tdi_bounce_bull` for BUY, `tdi_bounce_bear` for SELL) AND price alignment with 5 EMA (`price_above_ema` for BUY, `price_below_ema` for SELL).
   - **Model 3 (Fakeout Trap / Liquidity Sweep)**: Market is choppy (`or_flips > 0`), price recently broke opposite to Macro Bias (hunting liquidity), but immediately recovered back over 50% OR to trigger a Breakout aligned with Macro Bias.
3. Session is active (not ending / not closed).
4. Loss streak < 3.
-> Any mismatch or conflicting signal -> HOLD.
### Exit Criteria:
1. session.phase = "ending" -> CLOSE_ALL (EOD safety).
2. Confirmed Reversal Signal: exit_long = true (for BUY) or exit_short = true (for SELL) indicating a true TDI cross / momentum reversal -> CLOSE_ALL.
3. Significant Giveback on Winning Trade: Trade achieved profit (MFE >= 0.8x ATR) and gives back >= 40% of peak with momentum stall or reversal -> CLOSE_ALL (Lock-in profit).
4. Otherwise (trade in normal consolidation or healthy pullback within trend) -> HOLD (let ATR SL/TP and Trailing Stop manage the trade).

## Output Format (JSON only)

{{
  "action": "BUY" | "SELL" | "CLOSE_ALL" | "HOLD",
  "volume_lots": 0.01,
  "sl_pips": 0,
  "tp_pips": 0,
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

def evaluate_judas_sweep_gate(snapshot: MarketSnapshot) -> Optional[AgentDecision]:
    """
    Deterministic Gate for SMC / Asian Range Judas Sweep Bot.
    Filters out invalid setups before querying LLM.
    """
    strat = snapshot.strategy
    if strat is None:
        return None

    has_open_pos = (
        snapshot.position is not None
        or (snapshot.active_positions is not None and len(snapshot.active_positions) > 0)
    )

    # When NO open positions exist (New Entry Discovery Mode):
    if not has_open_pos:
        # Gate 1: No Sweep detected or outside Killzone
        if strat.bias_direction == "NONE":
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                confidence=90.0,
                reason="Judas Sweep Gate: Outside Killzone or no liquidity sweep detected",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

        # Gate 2: Pre-filter mode is MANAGE_ONLY
        if strat.bias_direction == "MANAGE_ONLY":
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                confidence=90.0,
                reason="Judas Sweep Gate: Pre-filter is MANAGE_ONLY with no open positions to manage",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

        # Gate 3: Stale Sweep Signal (> 3 bars since Judas Sweep occurred)
        if strat.signal_window_bars > 3:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                confidence=85.0,
                reason=f"Judas Sweep Gate: Signal is stale ({strat.signal_window_bars} bars elapsed since sweep > 3 max)",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

        # Gate 4: Invalid Asian Range width (too narrow < 20 pips or too wide > 600 pips on Gold)
        # Gate 4: Invalid Asian Range width (symbol-specific bounds)
        if strat.asian_range_pips > 0:
            sym_up = (snapshot.symbol or "").upper()
            if "XAU" in sym_up or "GOLD" in sym_up:
                min_asian_pips, max_asian_pips = 100.0, 10000.0  # $10.00 to $100.00 (or $1.00-$100.00 depending on broker digits)
            elif any(jpy in sym_up for jpy in ["JPY"]):
                min_asian_pips, max_asian_pips = 15.0, 200.0
            elif any(cr in sym_up for cr in ["BTC", "ETH", "CRYPTO"]):
                min_asian_pips, max_asian_pips = 500.0, 30000.0
            elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40"]):
                min_asian_pips, max_asian_pips = 50.0, 2000.0
            else:
                min_asian_pips, max_asian_pips = 12.0, 100.0  # Allow tight 12-20p Asian sessions for EURUSD/GBPUSD
            
            if strat.asian_range_pips < min_asian_pips or strat.asian_range_pips > max_asian_pips:
                return AgentDecision(
                    action="HOLD",
                    volume_lots=0.01,
                    sl_pips=0.0,
                    tp_pips=0.0,
                    confidence=85.0,
                    reason=f"Judas Sweep Gate: Asian Range width abnormal ({strat.asian_range_pips:.0f} pips not in {min_asian_pips:.0f}-{max_asian_pips:.0f}p valid range)",
                    request_id=snapshot.request_id,
                    bot_id=snapshot.bot_id,
                    symbol=snapshot.symbol,
                    timeframe=snapshot.timeframe
                )

    return None

def evaluate_cycle_gate(snapshot: MarketSnapshot) -> Optional[AgentDecision]:
    """
    Deterministic Cycle Gate (Cost Gate) for TMS + ORB Strategy.
    Evaluates whether an expensive LLM call can be safely bypassed with an immediate deterministic action.
    Returns AgentDecision if gated, or None if LLM call is required.
    """
    if not snapshot.tms:
        return None

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
        pos_side = snapshot.position.resolved_side
        if (pos_side == "BUY" and snapshot.tms.exit_long) or (pos_side == "SELL" and snapshot.tms.exit_short):
            return AgentDecision(
                action="CLOSE_ALL",
                volume_lots=0.0,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: TMS exit signal triggered ({snapshot.tms.exit_reason})"
            )
        # Check Profit Lock-in Giveback Guard
        # Differentiate Asset Class:
        # - Indices (US30, USTEC, DE40, NAS100, GER40, DJ30):
        #   Activation MFE >= 1.5x ATR (min 1000.0 pips / 100 points for US30, 300.0 pips for others)
        #   Giveback ratio threshold = 0.55 (55%)
        # - Forex / Metals:
        #   Activation MFE >= 0.8x ATR
        #   Giveback ratio threshold = 0.40 (40%)
        sym_upper = snapshot.symbol.upper()
        is_index = any(idx in sym_upper for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40"])

        if is_index:
            default_atr = 1000.0 if "US30" in sym_upper else 300.0
            atr_ref = snapshot.atr_pips if snapshot.atr_pips and snapshot.atr_pips > 0 else default_atr
            activation_mfe = max(1.5 * atr_ref, 1000.0 if "US30" in sym_upper else 300.0)
            giveback_ratio_threshold = 0.55
        else:
            atr_ref = snapshot.atr_pips if snapshot.atr_pips and snapshot.atr_pips > 0 else 30.0
            activation_mfe = 0.8 * atr_ref
            giveback_ratio_threshold = 0.40

        pos = snapshot.position
        if pos.mfe_pips >= activation_mfe and pos.giveback_pips >= pos.mfe_pips * giveback_ratio_threshold:
            chart_tms = snapshot.chart_tms or snapshot.tms
            momentum_stall = False
            if pos_side == "BUY" and (chart_tms.ha_turned_red or chart_tms.exit_long or chart_tms.green_tf_slope < 0):
                momentum_stall = True
            elif pos_side == "SELL" and (chart_tms.ha_turned_green or chart_tms.exit_short or chart_tms.green_tf_slope > 0):
                momentum_stall = True
            if momentum_stall:
                return AgentDecision(
                    action="CLOSE_ALL",
                    volume_lots=0.0,
                    sl_pips=0.0,
                    tp_pips=0.0,
                    reason=f"Cycle gate: Profit lock-in triggered (MFE={pos.mfe_pips:.1f}p >= {activation_mfe:.1f}p, gave back {pos.giveback_pips:.1f}p >= {giveback_ratio_threshold*100:.0f}% with momentum stall)"
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
                reason=f"Cycle gate: Market outside trading session (phase={snapshot.session.phase}, is_trading_time={snapshot.session.is_trading_time})"
            )

    # Gate 2.2: Loss Streak Gate (Circuit breaker)
    if snapshot.loss_streak >= 3:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Loss streak active ({snapshot.loss_streak} consecutive losses)"
        )

    # Gate 2.3: TMS Bias Gate
    bias = snapshot.tms.bias.upper()
    if bias == "NEUTRAL":
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason="Cycle gate: TMS bias is NEUTRAL"
        )

    # Gate 2.3.1: Post-TP Gate (Anti-FOMO / Structural Pullback Re-entry Check)
    if snapshot.tms.post_tp_gate_active:
        blocked_side = (snapshot.tms.post_tp_gate_side or "").upper()
        if (blocked_side == "BUY" and bias == "BULLISH") or (blocked_side == "SELL" and bias == "BEARISH") or not blocked_side:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Post-TP Protection active (blocking {blocked_side or 're-entry'} until valid structural pullback >= 0.5x ATR occurs)"
            )

    # Gate 2.4: ORB State Gate
    orb = snapshot.orb
    if orb is None or not orb.or_complete or orb.breakout_direction is None:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason="Cycle gate: No active decisive ORB breakout"
        )

    # Check if a qualified TDI Bounce is active
    chart_tms = snapshot.chart_tms or snapshot.tms
    has_bounce = (
        (bias == "BULLISH" and chart_tms.tdi_bounce_bull and chart_tms.price_above_ema) or
        (bias == "BEARISH" and chart_tms.tdi_bounce_bear and chart_tms.price_below_ema)
    )

    # Gate 2.4.1: Anti-Overextension / Max Breakout Distance Filter
    atr_ref = snapshot.atr_pips if snapshot.atr_pips and snapshot.atr_pips > 0 else None
    sym_upper = snapshot.symbol.upper()
    if "XAU" in sym_upper or "GOLD" in sym_upper:
        max_breakout_dist = min(atr_ref * 2.5, 1500.0) if atr_ref else 1500.0
    elif any(cr in sym_upper for cr in ["BTC", "CRYPTO"]):
        max_breakout_dist = min(atr_ref * 2.5, 60000.0) if atr_ref else 60000.0
    elif any(cr in sym_upper for cr in ["ETH", "SOL", "XRP"]):
        max_breakout_dist = min(atr_ref * 2.5, 15000.0) if atr_ref else 15000.0
    elif any(idx in sym_upper for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40"]):
        max_breakout_dist = min(atr_ref * 2.5, 1500.0) if atr_ref else 1500.0
    else:
        max_breakout_dist = min(atr_ref * 2.5, 60.0) if atr_ref else 60.0

    if orb.breakout_distance_pips > max_breakout_dist:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Breakout overextended ({orb.breakout_distance_pips:.1f}p > max {max_breakout_dist:.1f}p threshold). Avoid chasing at extremes."
        )

    if not orb.is_decisive and not has_bounce:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Breakout not decisive ({orb.breakout_distance_pips:.1f}p < threshold)"
        )

    # Model 2 Bounce is strictly capped at bars_since_breakout <= 10
    if not orb.in_entry_window:
        if orb.bars_since_breakout > 10:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Breakout is stale (bars_since_breakout={orb.bars_since_breakout} > 10). Re-entry prohibited."
            )
        if not has_bounce:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Outside entry window (bars_since_breakout={orb.bars_since_breakout}) with no qualified Bounce"
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

def build_judas_sweep_system_prompt(snapshot: MarketSnapshot) -> str:
    return "You are an elite Algorithmic Trading AI Co-Pilot for cTrader. Analyze the real-time market snapshot and output strictly valid JSON format with keys: \"action\" (\"BUY\"|\"SELL\"|\"HOLD\"|\"ADJUST\"|\"CLOSE_ALL\"), \"volume_lots\" (number), \"sl_pips\" (number), \"tp_pips\" (number), \"new_sl_price\" (number), \"new_tp_price\" (number), \"confidence\" (number between 0 and 100), \"reason\" (concise technical rationale). Output NO markdown explanations outside the JSON object."

def build_judas_sweep_user_prompt(snapshot: MarketSnapshot) -> str:
    strat = snapshot.strategy or StrategyData()
    sym_up = snapshot.symbol.upper()
    if "XAU" in sym_up or "GOLD" in sym_up:
        spread_pips = round(abs(snapshot.ask - snapshot.bid) / 0.01, 1)
    elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100"]):
        spread_pips = round(abs(snapshot.ask - snapshot.bid), 1)
    elif any(cr in sym_up for cr in ["BTC", "ETH"]):
        spread_pips = round(abs(snapshot.ask - snapshot.bid), 1)
    elif "JPY" in sym_up:
        spread_pips = round(abs(snapshot.ask - snapshot.bid) / 0.01, 1)
    else:
        spread_pips = round(abs(snapshot.ask - snapshot.bid) / 0.0001, 1)

    if strat.atr > 0:
        if "JPY" in sym_up:
            atr_pips = strat.atr / 0.01 if strat.atr < 5.0 else strat.atr
        elif "XAU" in sym_up or "GOLD" in sym_up:
            atr_pips = strat.atr / 0.01 if strat.atr < 100.0 else strat.atr
        elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100"]):
            atr_pips = strat.atr
        elif any(cr in sym_up for cr in ["BTC", "ETH", "SOL", "XRP"]):
            atr_pips = strat.atr / 0.01 if "ETH" in sym_up and strat.atr < 100.0 else strat.atr
        else:
            atr_pips = strat.atr / 0.0001 if strat.atr < 1.0 else strat.atr
    else:
        atr_pips = snapshot.atr_pips or 0.0
    open_pos_count = len(snapshot.active_positions) if snapshot.active_positions else (1 if snapshot.position else 0)
    has_open_pos = open_pos_count > 0 or snapshot.position is not None

    # 1. Format 50 chronological bars
    bar_lines = []
    if snapshot.bars:
        max_bars = min(50, len(snapshot.bars))
        chronological_bars = snapshot.bars[-max_bars:]
        for i, b in enumerate(chronological_bars):
            bar_idx = -(max_bars - 1 - i)
            o_val = b.open if b.open is not None else 0.0
            h_val = b.high if b.high is not None else 0.0
            l_val = b.low if b.low is not None else 0.0
            c_val = b.close if b.close is not None else 0.0
            v_val = b.volume if b.volume is not None else 0.0
            bar_lines.append(f"Bar[{bar_idx}]: O={format_price(o_val, snapshot.symbol)}, H={format_price(h_val, snapshot.symbol)}, L={format_price(l_val, snapshot.symbol)}, C={format_price(c_val, snapshot.symbol)}, V={v_val:.0f}")
    bars_formatted = "\n".join(bar_lines) if bar_lines else "No OHLCV bars available."

    # 2. Format recent trade history
    history_formatted = "No recent trades in the last 24h."
    if snapshot.recent_history:
        total_pnl = sum(h.pnl for h in snapshot.recent_history)
        win_count = sum(1 for h in snapshot.recent_history if h.pnl > 0)
        loss_count = sum(1 for h in snapshot.recent_history if h.pnl < 0)
        summary_header = f"[Session Performance: 24h PnL = {'+' if total_pnl >= 0 else ''}${total_pnl:.2f} | Wins: {win_count}, Losses: {loss_count}]"
        hist_lines = [
            f"  - {h.trade_type} {h.volume:.2f} lots @ {format_price(h.entry_price, snapshot.symbol)} -> Exit {format_price(h.exit_price, snapshot.symbol)} | PnL: {'+' if h.pnl >= 0 else ''}${h.pnl:.2f} | Closed: {h.exit_time}"
            for h in snapshot.recent_history
        ]
        history_formatted = summary_header + "\n" + "\n".join(hist_lines)

    # 3. Multi-timeframe summary
    mtf_summary = "Current Timeframe Only"
    if snapshot.multi_timeframe:
        cur = snapshot.multi_timeframe.current_tf
        h1 = snapshot.multi_timeframe.h1_tf
        h4 = snapshot.multi_timeframe.h4_tf
        lines = []
        for tf_ctx, label in [(cur, f"Current ({cur.timeframe if cur and cur.timeframe else 'M15'})"), (h1, "Higher TF (H1)"), (h4, "Major TF (H4)")]:
            if tf_ctx:
                sw_str = ""
                if tf_ctx.swing_structure:
                    sw = tf_ctx.swing_structure
                    sw_str = f" | Swings: High={format_price(sw.last_swing_high, snapshot.symbol)} ({sw.swing_high_type}), Low={format_price(sw.last_swing_low, snapshot.symbol)} ({sw.swing_low_type}), PrevH={format_price(sw.prev_swing_high, snapshot.symbol)}, PrevL={format_price(sw.prev_swing_low, snapshot.symbol)} [Struct: {sw.market_structure}]"
                lines.append(f"- {label}: Bias={tf_ctx.trend_bias} | FastMA={format_price(tf_ctx.fast_tema, snapshot.symbol)} | SlowMA={format_price(tf_ctx.slow_tema, snapshot.symbol)} | RSI={tf_ctx.rsi:.1f}{sw_str}")
        if lines:
            mtf_summary = "\n".join(lines)

    if not has_open_pos:
        return f"""You are a World-Class Institutional Forex Specialist & Quantitative Trader using SMART MONEY CONCEPTS (SMC) & Asian Range Judas Sweep.

=== NEW ENTRY DISCOVERY MODE ===
The cBot currently HAS NO OPEN POSITIONS. Your mission is to analyze the Asian Range Liquidity Sweep and identify high-probability Sniper entries.

=== 1. MARKET SNAPSHOT ===
- Symbol: {snapshot.symbol} | Timeframe: {snapshot.timeframe}
- Current Market Prices: Ask={format_price(snapshot.ask, snapshot.symbol)}, Bid={format_price(snapshot.bid, snapshot.symbol)} | Spread: {spread_pips:.1f} pips
- Account: Balance=${snapshot.account_balance:.2f} | Equity=${snapshot.account_equity:.2f}

=== 2. ASIAN RANGE & JUDAS SWEEP GATE CONTEXT ===
- Asian Session Range (00:00 - 06:00 UTC): High={format_price(strat.asian_high, snapshot.symbol)} | Low={format_price(strat.asian_low, snapshot.symbol)} | Range={strat.asian_range_pips:.0f} pips
- Active Killzone Window: {strat.killzone_session}
- Gate Signal Trigger: {strat.traditional_signal} (Bias: {strat.bias_direction})
- Bars Since Sweep: {strat.signal_window_bars} bar(s)
⚠️ CONSTRAINT:
  - Gate=BUY -> Price swept Asian Low & rejected back up. You MAY ONLY suggest 'BUY' or 'HOLD'. NEVER 'SELL'.
  - Gate=SELL -> Price swept Asian High & rejected back down. You MAY ONLY suggest 'SELL' or 'HOLD'. NEVER 'BUY'.
  - Gate=MANAGE_ONLY -> Do NOT open new positions. Only 'ADJUST', 'HOLD', or 'CLOSE_ALL'.
  - Bars Since Sweep > 3 -> Signal is STALE. Strongly prefer 'HOLD'.
  - volume_lots -> Always output 0. Volume is controlled by the cBot risk engine.

=== 3. MULTI-TIMEFRAME TREND BIAS (M15 + H1 + H4) ===
{mtf_summary}

=== 4. TECHNICAL INDICATORS & SWINGS ===
- Fast EMA: {format_price(strat.tema1, snapshot.symbol)} | Slow EMA: {format_price(strat.tema2, snapshot.symbol)}
- RSI (14): {strat.rsi:.1f} | ATR (14 Volatility): {atr_pips:.1f} pips
- Major Swing High (BSL / Resistance): {format_price(strat.recent_high, snapshot.symbol)}
- Major Swing Low (SSL / Support): {format_price(strat.recent_low, snapshot.symbol)}

=== 5. RECENT OHLCV CANDLE SEQUENCE (Last {len(bar_lines)} bars, chronological) ===
{bars_formatted}

=== 6. RECENT TRADE HISTORY (Last 24h, Max 5 trades) ===
{history_formatted}

=== 7. SMART MONEY CONCEPTS (SMC) & JUDAS SWEEP RULES ===
1. Judas Swing Reversal: Price fakeouts above Asian High or below Asian Low during London/NY Killzones, sweeps liquidity (BSL/SSL), and rejects back inside range.
2. Entry Confirmation: Validated Order Block, Fair Value Gap (FVG), or pinbar rejection on M15.
3. Technical SL & TP: Place SL safely beyond the sweep extreme spike (min floor 200 pips); TP targeted at opposing Asian Range boundary (Asian Low for SELL, Asian High for BUY) or target liquidity pool. For XAUUSD, $1.00 move = 100 pips.

=== 8. VALID ACTIONS ===
- BUY: Validated Bullish Judas Sweep (Asian Low fakeout) + Order Block bounce.
- SELL: Validated Bearish Judas Sweep (Asian High fakeout) + Order Block rejection.
- HOLD: Choppy consolidation inside Asian Range, no sweep, or conflicting HTF bias.

Reply strictly with JSON object."""
    else:
        pos_lines = []
        if snapshot.position:
            pos = snapshot.position
            cur_p = pos.current_price or snapshot.bid
            pos_lines.append(f"- Primary Position: {pos.resolved_side} {pos.volume or 0.01:.2f} lots @ Entry={format_price(pos.entry_price, snapshot.symbol)} | CurrentPrice={format_price(cur_p, snapshot.symbol)} | PnL=${pos.resolved_pnl:.2f} | SL={format_price(pos.sl or pos.sl_price, snapshot.symbol)} | TP={format_price(pos.tp or pos.tp_price, snapshot.symbol)} | Duration={pos.duration_minutes:.1f} mins")
        if snapshot.active_positions:
            for p in snapshot.active_positions:
                pos_lines.append(f"- Position ID {p.id}: {p.trade_type} {p.volume:.2f} lots @ Entry={format_price(p.entry_price, snapshot.symbol)} | SL={format_price(p.sl, snapshot.symbol)} | TP={format_price(p.tp, snapshot.symbol)} | Opened={p.entry_time}")
        running_pos_str = "\n".join(pos_lines) if pos_lines else "No position details."

        return f"""You are a World-Class Institutional Forex Specialist & Quantitative Risk Manager using SMART MONEY CONCEPTS (SMC) & Price Action.

=== ACTIVE POSITION MANAGEMENT MODE ===
The cBot currently HAS OPEN POSITIONS in the order book. Your PRIMARY MISSION is to EVALUATE AND MANAGE THESE EXISTING POSITIONS (Protect capital, lock in profits, adjust SL/TP, or exit safely).

=== 1. ACTIVE ORDER BOOK SNAPSHOT ===
- Symbol: {snapshot.symbol} | Timeframe: {snapshot.timeframe}
- Current Market Prices: Ask={format_price(snapshot.ask, snapshot.symbol)}, Bid={format_price(snapshot.bid, snapshot.symbol)} | Spread: {spread_pips:.1f} pips
- Account: Balance=${snapshot.account_balance:.2f} | Equity=${snapshot.account_equity:.2f}
- Running Positions:
{running_pos_str}

=== 2. TRADITIONAL STRATEGY GATE — MANDATORY CONSTRAINT ===
- Gate Direction: {strat.bias_direction}
- Signal Type: {strat.traditional_signal}
- Bars Since Cross: {strat.signal_window_bars} bar(s)
⚠️ CONSTRAINT:
  - Gate=MANAGE_ONLY → Focus on managing existing positions. Do NOT open new ones.
  - volume_lots → Always output 0. Volume is controlled by the cBot risk engine.

=== 3. MULTI-TIMEFRAME TREND BIAS (M15 + H1 + H4) ===
{mtf_summary}

=== 4. TECHNICAL INDICATORS & SWINGS ===
- Fast EMA: {format_price(strat.tema1, snapshot.symbol)} | Slow EMA: {format_price(strat.tema2, snapshot.symbol)}
- RSI (14): {strat.rsi:.1f} | ATR (14 Volatility): {atr_pips:.1f} pips
- Major Swing High (Resistance): {format_price(strat.recent_high, snapshot.symbol)}
- Major Swing Low (Support): {format_price(strat.recent_low, snapshot.symbol)}

=== 5. RECENT OHLCV CANDLE SEQUENCE (Last {len(bar_lines)} bars, chronological) ===
{bars_formatted}

=== 6. POSITION MANAGEMENT EVALUATION RULES ===
1. Trend & Structure Health: Check if current structure still favors the open position.
2. Action Decisions:
   - HOLD: Position healthy and progressing towards TP.
   - ADJUST: Move SL to Break-Even (when in >= 1:1 RR profit) or Trailing Stop behind new Order Block. Specify new_sl_price and/or new_tp_price (or sl_pips/tp_pips).
   - CLOSE_ALL: Emergency exit if major opposing CHoCH reversal occurs against the position.
   - BUY / SELL: Scale-in ONLY if trend is extremely strong with fresh unmitigated Order Block.

Reply strictly with JSON object."""

@app.post("/trade", response_model=AgentDecision)
async def trade_decision(snapshot: MarketSnapshot):
    account_id = _resolve_account(snapshot)
    is_judas = is_judas_sweep_bot(snapshot)
    
    pos_data = None
    if snapshot.position:
        pos_data = {
            "side": snapshot.position.resolved_side,
            "entry_price": snapshot.position.entry_price,
            "unrealized_pnl": snapshot.position.resolved_pnl,
            "unrealized_pnl_pips": snapshot.position.unrealized_pnl_pips,
            "mfe_pips": snapshot.position.mfe_pips,
            "giveback_pips": snapshot.position.giveback_pips,
            "sl_price": snapshot.position.sl or snapshot.position.sl_price,
            "tp_price": snapshot.position.tp or snapshot.position.tp_price,
        }
    portfolio_manager.update_market_price(snapshot.symbol, snapshot.bid, snapshot.ask, bot_id=snapshot.bot_id, position_data=pos_data, account_id=account_id)

    if is_judas:
        strat = snapshot.strategy
        asian_str = f"Asian=[{strat.asian_low:g}...{strat.asian_high:g}] ({strat.asian_range_pips:.0f}p)" if strat else "Asian=N/A"
        kz_str = strat.killzone_session if strat else "N/A"
        bias_str = f"{strat.bias_direction} ({strat.traditional_signal})" if strat else "N/A"
        pos_str = f"{snapshot.position.resolved_side} pnl=${snapshot.position.resolved_pnl:.2f}" if snapshot.position else "FLAT"
        
        logger.info(
            f"[SNAPSHOT SMC] {account_id}/{snapshot.bot_id} | {snapshot.symbol} {snapshot.timeframe} | "
            f"Bid={snapshot.bid:g} Ask={snapshot.ask:g} | {asian_str} | KZ={kz_str} | Gate={bias_str} | Pos={pos_str}"
        )

        # SMC Judas Sweep Gate Evaluation
        gated_decision = evaluate_judas_sweep_gate(snapshot)
        if gated_decision is not None:
            logger.info(f"[JUDAS GATE] GATED: {gated_decision.action} | Reason: {gated_decision.reason}")
            return gated_decision

        system_prompt = build_judas_sweep_system_prompt(snapshot)
        user_prompt = build_judas_sweep_user_prompt(snapshot)
    else:
        # TMS + ORB Strategy Flow
        regime_str = snapshot.market.regime if snapshot.market else "N/A"
        er_str = f"{snapshot.market.er_session:.2f}" if snapshot.market and snapshot.market.er_session is not None else "N/A"
        pos_str = f"{snapshot.position.resolved_side} pnl={snapshot.position.unrealized_pnl_pips:.1f}p" if snapshot.position else "FLAT"
        sess_str = f"{snapshot.session.phase} ({snapshot.session.minutes_to_end}m)" if snapshot.session else "N/A"
        
        ha_str = "N/A"
        tdi_str = "N/A"
        stoch_str = "N/A"
        if snapshot.bars and len(snapshot.bars) > 0:
            b = snapshot.bars[-1]
            if b.ha_color is not None:
                ha_icon = "🟢" if str(b.ha_color).lower() == "green" else "🔴" if str(b.ha_color).lower() == "red" else str(b.ha_color)
                ha_str = f"HA={ha_icon}"
            if b.tdi_green is not None and b.tdi_red is not None:
                tdi_str = f"TDI_G={b.tdi_green:.2f} TDI_R={b.tdi_red:.2f}"
            if b.stoch_k is not None and b.stoch_d is not None:
                stoch_str = f"Stoch=%K={b.stoch_k:.1f} %D={b.stoch_d:.1f}"
        
        or_str = "OR=N/A"
        if snapshot.orb:
            or_str = f"OR=[{snapshot.orb.or_low:g}...{snapshot.orb.or_high:g}]"

        logger.info(
            f"[SNAPSHOT TMS] {account_id}/{snapshot.bot_id} | {snapshot.symbol} {snapshot.timeframe} | "
            f"Bid={snapshot.bid:g} | {or_str} | {ha_str} {tdi_str} {stoch_str} | "
            f"TMS={snapshot.tms.bias if snapshot.tms else 'N/A'} (age={snapshot.tms.bars_since_cross if snapshot.tms else 0}) | "
            f"Regime={regime_str} (ER={er_str}) | Pos={pos_str} | Session={sess_str}"
        )

        gated_decision = evaluate_cycle_gate(snapshot)
        if gated_decision is not None:
            logger.info(f"[CYCLE GATE] GATED: {gated_decision.action} | Reason: {gated_decision.reason}")
            return gated_decision

        system_prompt = build_system_prompt(snapshot)
        user_prompt = build_user_prompt(snapshot)

    # Check portfolio risk before allowing new trades
    has_open = snapshot.position is not None or (snapshot.active_positions is not None and len(snapshot.active_positions) > 0)
    if not has_open:
        can_trade, reason = portfolio_manager.check_risk(
            symbol=snapshot.symbol,
            side="BUY",  # Will be determined by LLM, checking capacity
            volume=0.01,
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
                reason=f"Portfolio constraint: {reason}",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        kwargs = {"temperature": 0.1}
        if hasattr(llm_client, 'client') and hasattr(llm_client.client, 'chat'):
            kwargs["response_format"] = {"type": "json_object"}
        
        result_str = await llm_client.chat(messages, **kwargs)
        decision_dict = JSONResponseParser.parse(result_str)
        
        # Inject metadata if not in response
        if "request_id" not in decision_dict or not decision_dict["request_id"]:
            decision_dict["request_id"] = snapshot.request_id
        if "bot_id" not in decision_dict or not decision_dict["bot_id"]:
            decision_dict["bot_id"] = snapshot.bot_id
        if "symbol" not in decision_dict or not decision_dict["symbol"]:
            decision_dict["symbol"] = snapshot.symbol
        if "timeframe" not in decision_dict or not decision_dict["timeframe"]:
            decision_dict["timeframe"] = snapshot.timeframe
        if "confidence" not in decision_dict:
            decision_dict["confidence"] = 80.0

        logger.info(
            f"[LLM DECISION] {account_id}/{snapshot.bot_id} -> Action: {decision_dict.get('action', 'HOLD')} | "
            f"Vol: {decision_dict.get('volume_lots', 0.01)} lots | SL: {decision_dict.get('sl_pips', 0)}p | "
            f"TP: {decision_dict.get('tp_pips', 0)}p | Conf: {decision_dict.get('confidence', 80.0):.1f}% | "
            f"Reason: {decision_dict.get('reason', '')}"
        )

        return AgentDecision(**decision_dict)
    except Exception as e:
        logger.error(f"[{account_id}/{snapshot.bot_id}] LLM Error: {e}")
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0,
            tp_pips=0,
            reason=f"Error: {e}",
            request_id=snapshot.request_id,
            bot_id=snapshot.bot_id,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe
        )

@app.post("/api/tick")
@app.post("/api/telemetry_tick")
async def handle_telemetry_tick(request: dict):
    """
    Direct tick telemetry endpoint from cBots (TMS or Judas Sweep).
    Updates account equity/balance and live market prices in portfolio manager.
    """
    try:
        bot_id = sanitize_bot_id(request.get("bot_id", "default"))
        account_number = str(request.get("account_number", "0"))
        registry = get_account_registry()
        account_type = request.get("account_type")
        if not account_type:
            account_type = registry.get_account_type(account_number) or "demo"
        account_type = str(account_type)
        account_label = request.get("account_label")
        balance = float(request.get("balance", request.get("equity", 0.0)) or 0.0)
        equity = float(request.get("equity", request.get("balance", 0.0)) or 0.0)
        
        account_id = registry.upsert_from_bot(
            account_number=account_number,
            account_type=account_type,
            label=account_label,
            balance=balance,
            equity=equity
        )
        
        symbol = request.get("symbol")
        bid = float(request.get("bid", 0.0) or 0.0)
        ask = float(request.get("ask", 0.0) or 0.0)
        if symbol and (bid > 0 or ask > 0):
            portfolio_manager.update_market_price(symbol, bid, ask, bot_id=bot_id)
        try:
            await broadcast_update()
        except Exception:
            pass
            
        return {"status": "ok", "account_id": account_id}
    except Exception as e:
        logger.error(f"Telemetry tick error: {e}")
        return {"status": "error", "message": str(e)}
@app.post("/api/cbot_event")
async def handle_cbot_event(request: dict):
    """
    Direct event telemetry endpoint from cBots (guardrail blocks, custom warnings, execution failures).
    """
    try:
        bot_id = sanitize_bot_id(request.get("bot_id", "default"))
        event_type = request.get("event_type", "GUARDRAIL")
        message = request.get("message", "")
        account_number = str(request.get("account_number", "0"))
        logger.info(f"[CBOT EVENT] {account_number}/{bot_id} | Type: {event_type} | Message: {message}")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling cbot event: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/portfolio/report")
async def report_position(request: dict):
    """
    Report position changes from cBot.
    Expected format: {"bot_id": "...", "action": "open|close", "symbol": "...", ...}
    """
    try:
        bot_id = sanitize_bot_id(request.get("bot_id", "default"))
        action = request.get("action")
        symbol = request.get("symbol")
        
        account_number = str(request.get("account_number", "0"))
        registry = get_account_registry()
        account_type = request.get("account_type")
        if not account_type:
            account_type = registry.get_account_type(account_number) or "demo"
        account_type = str(account_type)
        account_label = request.get("account_label")
        account_balance = float(request.get("account_balance", 0) or 0)
        account_equity = float(request.get("account_equity", 0) or 0)
        
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
    tms = snapshot.tms or TmsSignals()
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
            f"- Price > EMA5: {ctms.price_above_ema}, Price < EMA5: {ctms.price_below_ema}",
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
            f"- PnL: ${pos.unrealized_pnl:.2f} ({pos.unrealized_pnl_pips:.1f} pips)",
            f"- Peak MFE: {pos.mfe_pips:.1f} pips | Giveback: {pos.giveback_pips:.1f} pips",
            f"- SL: {pos.sl_price:.5f} | TP: {pos.tp_price:.5f}",
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
        "1. If session.phase = 'ending' and position open → CLOSE_ALL",
        "2. If position is open and a confirmed reversal occurred (exit_long=true for BUY, exit_short=true for SELL) → CLOSE_ALL",
        "3. If position is open and within normal trend fluctuation → HOLD (allow ATR Stop Loss / Trailing Stop to operate)",
        "4. If long_entry=true and TMS BULLISH and ORB breakout UP and is_decisive → BUY",
        "5. If short_entry=true and TMS BEARISH and ORB breakout DOWN and is_decisive → SELL",
        "6. If loss_streak >= 3 → HOLD",
        "7. Otherwise → HOLD",
        "",
        "SL/TP pips are ignored by the engine (ATR-based) — set sl_pips/tp_pips to 0.",
        "",
        "Output JSON decision.",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    host = os.getenv("SERVER_HOST", "127.0.0.1")
    port = int(os.getenv("SERVER_PORT", "8000"))
    reload = os.getenv("SERVER_RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run("app.server:app", host=host, port=port, reload=reload, reload_dirs=["app"] if reload else None)
