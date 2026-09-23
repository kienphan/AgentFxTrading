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
load_dotenv(PROJECT_ROOT / ".env", override=True)

import datetime
import asyncio
import logging.handlers
import threading
from typing import Optional, List, Dict, Any, Union

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"

class GMT7Formatter(logging.Formatter):
    """Formatter that outputs timestamps in GMT+7 (Asia/Bangkok / Asia/Ho_Chi_Minh)."""
    def converter(self, timestamp):
        dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone(datetime.timedelta(hours=7))).timetuple()

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        if datefmt:
            s = datetime.datetime(*ct[:6]).strftime(datefmt)
        else:
            s = datetime.datetime(*ct[:6]).strftime("%Y-%m-%d %H:%M:%S")
        return s
class DailyDateFileHandler(logging.Handler):
    """
    Daily log file handler that writes directly to logs/agent_YYYY-MM-DD.log based on GMT+7 date.
    Rolls over cleanly at 00:00 GMT+7 without filename mangling.
    """
    def __init__(self, logs_dir: Path, backup_count: int = 14, encoding: str = "utf-8"):
        super().__init__()
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)
        self.backup_count = backup_count
        self.encoding = encoding
        self._current_date = None
        self._file = None
        self._lock = threading.Lock()

    def _get_current_date(self) -> str:
        now_gmt7 = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
        return now_gmt7.strftime("%Y-%m-%d")

    def _cleanup_old_logs(self):
        try:
            log_files = sorted(self.logs_dir.glob("agent_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in log_files[self.backup_count:]:
                f.unlink(missing_ok=True)
        except Exception:
            pass

    def emit(self, record):
        try:
            msg = self.format(record)
            date_str = self._get_current_date()
            with self._lock:
                if self._file is None or self._current_date != date_str:
                    if self._file is not None:
                        try:
                            self._file.close()
                        except Exception:
                            pass
                    self._current_date = date_str
                    log_path = self.logs_dir / f"agent_{date_str}.log"
                    self._file = open(log_path, "a", encoding=self.encoding)
                    self._cleanup_old_logs()
                self._file.write(msg + "\n")
                self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
        super().close()

def is_running_under_test() -> bool:
    return (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or any("pytest" in str(arg).lower() for arg in sys.argv)
        or os.environ.get("ENV") == "test"
        or os.environ.get("TESTING") == "1"
    )

def setup_agent_logging(level=logging.INFO, log_filename: Optional[str] = None):
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    under_test = is_running_under_test()
    if under_test or log_filename:
        # Isolate pytest / test runner logs to logs/test.log instead of polluting live daily logs
        target_log = logs_dir / (log_filename or "test.log")
        file_handler = logging.FileHandler(target_log, mode="a", encoding="utf-8")
    else:
        # Daily rotating file handler (14 days backup, GMT+7 date-aligned) for production live trading
        file_handler = DailyDateFileHandler(logs_dir, backup_count=14, encoding="utf-8")

    file_handler.setFormatter(GMT7Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    file_handler.setLevel(level)
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(GMT7Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
    console_handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    return file_handler, console_handler

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
from app.llm_client import create_llm_client, JSONResponseParser, describe_llm_error, _clean_env_float, _clean_env_int
from app.portfolio import init_portfolio, get_portfolio_manager, is_us_index
from app.dashboard import router as dashboard_router, broadcast_update, broadcast_tick, broadcast_event, broadcast_decision, record_ai_decision, manager as ws_manager, WebSocketLogHandler, tick_levels
from app.accounts import init_account_registry, get_account_registry
from app.cbot_watchdog import record_bot_snapshot

# Attach WebSocket live log handler to root logger
_ws_handler = WebSocketLogHandler(ws_manager)
_ws_handler.setFormatter(GMT7Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
_ws_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_ws_handler)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        loop = asyncio.get_running_loop()
        ws_manager.set_event_loop(loop)
    except Exception as e:
        logger.warning(f"Failed to set event loop for ws_manager: {e}")

    # Start cBot Watchdog service for automatic relogin and crash recovery
    from app.cbot_watchdog import cbot_watchdog
    watchdog_task = asyncio.create_task(cbot_watchdog.run_loop())

    yield

    # Shutdown watchdog cleanly
    cbot_watchdog.stop()
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="TMS+ORB Agent Server", lifespan=lifespan)

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
account_registry = init_account_registry()
account_registry.seed_from_env()

# Initialize Portfolio Manager
portfolio_manager = init_portfolio()
# Create LLM client based on LLM_PROVIDER env variable
# Supports: "qwen", "openai", "anthropic", "deepseek", "openai_compatible"
llm_client = create_llm_client()

# /trade answers a cBot that is waiting on the other end of an HTTP call (FlowRsiBot gives
# up after 60 s), so the client-wide LLM_TIMEOUT x LLM_MAX_RETRIES budget (90 s x 4 tries)
# is far too long here: the bot would time out before the safety fallback ever reached it.
# Each attempt gets its own timeout, and a hard deadline caps retries plus backoff.
TRADE_LLM_TIMEOUT = _clean_env_float("LLM_TRADE_TIMEOUT", 40.0)
TRADE_LLM_MAX_RETRIES = _clean_env_int("LLM_TRADE_MAX_RETRIES", 1)
TRADE_LLM_DEADLINE = _clean_env_float("LLM_TRADE_DEADLINE", 55.0)

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
    bias: str = "NEUTRAL"  # "BULLISH"/"BEARISH" cross lock; NEUTRAL only before the first confirmed cross
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
    pnl_pips: Optional[float] = None  # Alias for unrealized_pnl_pips
    pips: Optional[float] = None  # Alias for unrealized_pnl_pips (the tick route's name)
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

    @property
    def resolved_pnl_pips(self) -> float:
        """The position's P&L in pips, under whichever name the reporting cBot used.

        AiAgentBot sends `unrealized_pnl_pips`; FlowRsiBot and AsianRangeJudasSweepBot use the
        short names they already use on the tick route (`pnl_pips` / `pips`), matching their
        `pnl` alias for the money. Only the bot can produce this figure - the server has no pip
        size and no FX rate - so an unreported name has to fall through to the next alias
        instead of the 0.0 default, which is what printed a flat "(0.0p)" next to a correct
        dollar amount on the dashboard. A pips field the bot did send is authoritative even
        when it is exactly 0.0, hence the membership test rather than a truthiness one.
        """
        if "unrealized_pnl_pips" in self.model_fields_set:
            return self.unrealized_pnl_pips
        for alias in (self.pnl_pips, self.pips):
            if alias is not None:
                return alias
        return self.unrealized_pnl_pips

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
    # Structural invalidation state mirrored from the cBot's Judas Structural Guard:
    # a decisive M15 close beyond the boundary turns the sweep level into a breakout
    # level, so the mean-reversion side is dead for the rest of the Asian session.
    asian_low_broken: bool = False
    asian_high_broken: bool = False
    # Exact Symbol.PipSize from the cBot; 0.0 means an older bot that omitted it.
    pip_size: float = 0.0
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
    spread_pips: Optional[float] = None
    # Exact Symbol.PipSize from the cBot. FlowRsiBot has always sent this at the top
    # level; without the field Pydantic dropped it and the server fell back to the
    # per-asset-class table, which cannot know a broker's digit convention.
    pip_size: float = 0.0
    bars: List[BarData] = []
    tms: Optional[TmsSignals] = None
    chart_tms: Optional[TmsSignals] = None
    orb: Optional[OrbData] = None
    market: Optional[MarketRegimeInfo] = None
    position: Optional[PositionInfo] = None
    session: Optional[SessionInfo] = None
    strategy: Optional[StrategyData] = None
    # FlowRSI / Nested RSI SMC Engine fields
    fast_rsi: Optional[float] = None
    slow_rsi: Optional[float] = None
    rsi_cross_signal: Optional[str] = None
    in_fvg_zone: Optional[bool] = None
    fvg_type: Optional[str] = None
    is_discount: Optional[bool] = None
    is_premium: Optional[bool] = None
    liquidity_swept: Optional[bool] = None
    swept_liquidity_type: Optional[str] = None
    technical_sl_price: Optional[float] = None
    technical_tp_price: Optional[float] = None
    technical_risk_reward: Optional[float] = None
    candidate_action: Optional[str] = None
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
    # Used margin as cTrader reports it (Account.Margin). None from a cBot build that predates
    # the field, in which case check_risk falls back to its per-lot estimate.
    account_margin: Optional[float] = None

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
    volume_lots: Optional[float] = 0.01
    sl_pips: Optional[float] = 0.0
    tp_pips: Optional[float] = 0.0
    new_sl_price: Optional[float] = 0.0
    new_tp_price: Optional[float] = 0.0
    confidence: Optional[float] = 80.0
    reason: str = ""
    request_id: Optional[str] = None
    bot_id: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None

    @field_validator("volume_lots", "sl_pips", "tp_pips", "new_sl_price", "new_tp_price", "confidence", mode="before")
    @classmethod
    def coerce_none_to_default(cls, v, info):
        defaults = {
            "volume_lots": 0.01,
            "sl_pips": 0.0,
            "tp_pips": 0.0,
            "new_sl_price": 0.0,
            "new_tp_price": 0.0,
            "confidence": 80.0,
        }
        if v is None or v == "":
            return defaults.get(info.field_name, 0.0)
        try:
            return float(v)
        except (ValueError, TypeError):
            return defaults.get(info.field_name, 0.0)

def is_judas_sweep_bot(snapshot: MarketSnapshot) -> bool:
    """Detect whether snapshot belongs to an Asian Range Judas Sweep / SMC bot."""
    if snapshot.strategy is not None and (
        snapshot.strategy.asian_high > 0 or 
        snapshot.strategy.killzone_session not in ("NONE", "Outside Killzones", "")
    ):
        return True
    bot_name = (snapshot.bot_id or "").lower()
    return "judas" in bot_name or "asian" in bot_name or "sweep" in bot_name

def is_flow_rsi_bot(snapshot: MarketSnapshot) -> bool:
    """Detect whether snapshot belongs to a FlowRSI / Nested RSI SMC bot."""
    bot_name = (snapshot.bot_id or "").lower()
    if "flowrsi" in bot_name or "flow_rsi" in bot_name or "nestedrsi" in bot_name:
        return True
    if snapshot.fast_rsi is not None and snapshot.slow_rsi is not None:
        return True
    return False

def _pip_size_for_symbol(sym_up: str) -> float:
    """
    Fallback pip scale per asset class.

    Broker digit conventions for indices/commodities vary, so the authoritative value is the
    Symbol.PipSize the cBot reports in the snapshot; this table only covers bots that omit it.
    Verified against live Asian ranges: USTEC 0.1, UK100 0.1, XAUUSD 0.01, GBPUSD 0.0001.
    """
    if any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40", "UK100", "GB100", "JP225", "NIKKEI", "JPN225", "HK50", "HSI", "US500", "SPX500"]):
        return 0.1
    if any(k in sym_up for k in ["BTC", "ETH", "XAU", "GOLD", "JPY"]):
        return 0.01
    return 0.0001

def format_price(price: Optional[float], symbol: str) -> str:
    """Format price dynamically according to symbol asset class and decimal convention."""
    if price is None:
        return "0.0"
    sym = (symbol or "").upper()
    if "JPY" in sym:
        return f"{price:.3f}"
    elif any(k in sym for k in ["XAU", "GOLD", "US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40", "UK100", "GB100", "JP225", "NIKKEI", "JPN225", "HK50", "HSI", "US500", "SPX500", "BTC", "ETH", "SOL", "XRP"]):
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
    is_index = any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40", "UK100", "GB100", "JP225", "NIKKEI", "JPN225", "HK50", "HSI", "US500", "SPX500"])

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
            "• CURRENT REGIME WAS CHOPPY (High failed breakouts / OR flips): The market was oscillating and hunting stops, "
            "but a chop label is backward-looking. A FRESH decisive ORB breakout (few bars since breakout) with aligned "
            "momentum slope means the chop is ending - treat that Model 1 breakout as valid, do not wait for the regime "
            "label to flip. Avoid entering stale/retest setups and never chase extended moves in a choppy regime."
        )
    else:
        regime_guideline = (
            "• CURRENT REGIME IS MIXED/FORMING: Maintain standard trading discipline; entry only on confirmed setups."
        )

    return f"""You are an AUTONOMOUS quantitative trading agent running the TMS (Trend Momentum Signal) + ORB (Opening Range Breakout) strategy for {snapshot.symbol} ({asset_type}).

## Core Contract: "LLM proposes, Code disposes"
You analyze market structure and propose trade actions. The deterministic execution harness (cBot + Portfolio Manager) enforces hard guardrails (spread checks, correlation limits, trailing stops, and EOD force-flatten). Always output valid structured JSON.

## SL/TP AND SIZING ARE COMPUTED DETERMINISTICALLY BY THE ENGINE
- The cBot overrides any sl_pips / tp_pips you return with ATR-based distances.
- Return sl_pips = 0, tp_pips = 0, and volume_lots = 0.0. Your job is strictly DIRECTION (action) and TIMING — never pip targets or lot sizing.

## Strategy Logic

### 1. TMS (Trend Momentum Signal) = DIRECTIONAL BIAS
- The bias is a CROSS LOCK: the most recent confirmed cross (TDI Green/Red cross + Heikin Ashi direction + Stochastic K vs D on the same bar) sets the direction and is HELD until the next confirmed reverse cross. There is no "lines intertwined" NEUTRAL bias.
- **NEUTRAL** is therefore reported only while no confirmed cross exists yet (insufficient history). NEVER enter when the bias is NEUTRAL.
- Entries MUST align with the current TMS bias.

### 2. ORB (Opening Range Breakout) = ENTRY TRIGGER
- Opening Range (OR) defines the high/low of the first 15 minutes of the active session.
- Valid entry requires price closing beyond OR boundary in the direction of TMS bias.
- Breakout must be DECISIVE (breakout_distance_pips >= threshold) and within entry window (bars_since_breakout <= {MODEL1_ENTRY_WINDOW_BARS}).
- A breakout that has re-entered the range is reported as NO breakout (direction = none) — never trade a failed breakout.

### 3. ENTRY MODELS (DIRECT BREAKOUT vs RETEST + TDI BOUNCE)
- **Model 1: Direct Momentum Breakout**: Price closes decisively beyond OR boundary with steep TDI slope in bias direction. Valid when in entry window (`bars_since_breakout <= {MODEL1_ENTRY_WINDOW_BARS}`) AND distance is within fresh direct breakout threshold (`breakout_distance <= 1.0x - 1.5x ATR` or symbol direct cap). If the breakout candle is already oversized/exhausted (`> direct breakout limit`), Model 1 direct entry is strictly PROHIBITED; you MUST wait for Model 2 (Retest + TDI Bounce).
- **Model 2: Breakout Retest / Continuation (High R:R Continuation)**:
  - Price broke out of OR, pulled back toward OR boundary / EMA5 without breaking opposite structure.
  - **Continuation Triggers**: Verified TDI Bounce (`tdi_bounce_bull` / `tdi_bounce_bear`) OR Dynamic EMA Retest Continuation (price holds EMA5 with momentum re-accelerating in trend direction).
  - **Strict Price Action Verification**: Only valid when price is properly positioned relative to the 5 EMA (`price_above_ema = true` for BUY, `price_below_ema = true` for SELL), `bars_since_breakout <= {MAX_BARS_SINCE_BREAKOUT_MODEL2}`, and distance is NOT overextended beyond max ceiling. NEVER enter a trade when price is overextended far from EMA5 or floating at extreme exhaustion levels.
### 4. Market Regime (Kaufman Efficiency Ratio & Chop Detection)
- **er_session / er_recent**: Kaufman Efficiency Ratio (|net move| / total path, 1.0 = pure directional trend, ~0 = pure oscillation).
- **or_flips**: Number of times price broke outside OR and closed back inside (flips >= 5 indicates chop trap day).
{regime_guideline}

### 5. Quantitative Edge-Case Rules (Battle-Tested Discipline)
- **EXHAUSTION BREAKOUT GUARD & ANTI-OVEREXTENSION**: NEVER chase extended breakouts. Direct entry (Model 1) requires price to be close to the OR boundary (breakout distance {breakout_distance_prompt()}). If the breakout candle exceeded this threshold, it is an Exhaustion Breakout -> declare HOLD. Entering on an exhausted breakout without a retest is strictly forbidden, even if the TMS bias cross just occurred.
- **BIAS-FRESH Rule**: A fresh TMS cross (`bars_since_cross <= 1`) validates trend initiation, but does NOT override the Exhaustion Breakout Guard. If the initial breakout candle traveled too far, wait for the first pullback and TDI Bounce / EMA Retest (Model 2) to enter with favorable Risk:Reward.
- **TDI BOUNCE / RETEST EXCEPTION TO ANTI-CHASE**: Standard Anti-Chase blocks entry when `bars_since_breakout >= 4` without a pullback. However, if a valid **TDI Bounce / Dynamic EMA Retest** is confirmed AND `bars_since_breakout <= {MAX_BARS_SINCE_BREAKOUT_MODEL2}` AND price is near EMA5, the pullback has occurred and resolved in favor of the trend -> Enter on the bounce/retest.
- **ANTI-CHASE Rule**: When bars_since_breakout >= 4 under an OLD bias (bars_since_cross >= 5) without a pullback/bounce, DO NOT chase at extremes. Declare HOLD.
- **POST-TP GATE (Anti-FOMO)**: Once a trade hits Take Profit or closes after a major win, the deterministic engine ARMS a blocker (`post_tp_gate_active = true`) preventing immediate re-entry in the same direction (`post_tp_gate_side`). It unlocks automatically only when a real Pullback (>= 0.5x ATR), OR Touch, or Bias Flip occurs. Never re-enter immediately at the peak of a move without a structural pullback.
- **POSITION BREATHING ROOM & PATIENCE**:
  - Trading requires room for normal market fluctuations. Never prematurely cut an open position on minor pullbacks or single-candle noise if price is still structurally valid and aligned with the macro trend. Stop Loss and Trailing Stop are dynamically managed by the engine via ATR.
- **POSITION MEMORY & GIVEBACK FLOOR (PROFIT LOCK-IN)**:
  - position.mfe_pips = PEAK floating profit reached.
  - position.giveback_pips = Profit given back from peak (MFE - Current PnL).
- **Golden Rule (2-Tier Profit Lock-in & Trailing Stop)**:
  - **Tier 1 (Moderate Gains - Trend Inception)**: Provides generous breathing room for the trend to develop through normal pullbacks. Activates when MFE >= 0.8x ATR (Forex/Metals) with 40% giveback cap, or MFE >= 1.5x ATR (min 1000p on US30 / 300p on USTEC) with 55% giveback cap for indices. Trailing stop tracks at standard 1.2x - 1.5x ATR.
  - **Tier 2 (Large Gains / Deep Trend Run)**: Activates when MFE >= 2.5x ATR (or >= 1200p on US30, >= 600p on USTEC, >= 400p on DE40, or >= 65% distance to TP). In Tier 2, giveback tolerance strictly tightens from 55% down to 35% (indices) and 30% (forex/metals), and trailing distance tightens to 0.9x ATR. When a position achieves large floating gains (e.g. +$15+ on US30), lock in at least 65% of peak gains; NEVER let a major win slip below +$11.
- **ASSET SCALE & RISK DISCIPLINE (CRYPTO / INDICES / METALS / FOREX)**:
  - Stop Loss is hard-capped by ATR guardrails and max dollar risk ($10–$15 max per trade on a $700 account).
  - On Gold (XAUUSD), 1 pip = $0.01. Do NOT trade with massive SLs > 1200 pips ($12).
  - On Crypto and Indices, several hundred pips is minimal noise (a small fraction of 1 ATR). Evaluate the actual chart trend structure.
### 6. Risk & Sizing (handled 100% by the engine — context only)
- SL/TP distances are computed by the cBot ATR engine (ATR on the chart timeframe); you do NOT provide them.
- Position volume is computed strictly by the cBot engine from risk-per-trade % (0.2%) and ATR-based SL. Always output volume_lots = 0.0.
## Decision Rules Summary

### Entry Criteria (ALL must be satisfied):
1. TMS Bias is clearly BULLISH (for BUY) or BEARISH (for SELL).
2. Valid Entry Trigger (Any of the following models):
   - **Model 1 (Direct Breakout)**: ORB Breakout (is_decisive = true, in_entry_window = true) AND price agrees with 5 EMA (price_above_ema = true for BUY, price_below_ema = true for SELL) AND breakout distance is NOT exhausted (<= 1.0x ATR / direct limit).
   - **Model 2 (Retest + TDI Bounce / EMA Continuation)**: Breakout Retest/Continuation with confirmed TDI Bounce (`tdi_bounce_bull` for BUY, `tdi_bounce_bear` for SELL) OR Dynamic EMA Retest with momentum slope aligned AND price alignment with 5 EMA (`price_above_ema` for BUY, `price_below_ema` for SELL).
   - **Model 3 (Fakeout Trap / Liquidity Sweep)**: Market is choppy (`or_flips > 0`), price recently broke opposite to Macro Bias (hunting liquidity), but immediately recovered back over 50% OR to trigger a Breakout aligned with Macro Bias.
3. Session is active (not ending / not closed).
4. Loss streak < 3.
5. **US Indices Alignment**: For US indices (US30, USTEC, US500), all open positions MUST align in the same direction. Conflicting trades (e.g. BUY USTEC while US30 is open SELL) are strictly PROHIBITED.
-> Any mismatch or conflicting signal -> HOLD.
### Exit Criteria:
1. session.phase = "ending" -> CLOSE_ALL (EOD safety).
2. Confirmed Reversal Signal: Macro H1 structural reversal (Macro exit_long=true for BUY, Macro exit_short=true for SELL) -> CLOSE_ALL. Intra-timeframe chart noise (M15 pullbacks while Macro H1 trend is intact) MUST be held (HOLD) to allow ATR Trailing Stop and OR range to work.
3. Significant Giveback on Winning Trade: Giveback reaches Tier 1 (40% Forex / 55% Indices) or Tier 2 (30% Forex / 35% Indices on large gains >= 2.5x ATR / 65% TP) with momentum stall or reversal -> CLOSE_ALL (Lock-in profit).
4. Otherwise (trade in normal consolidation or healthy pullback within trend) -> HOLD (let ATR SL/TP and Trailing Stop manage the trade).

## Output Format (JSON only)

{{
  "action": "BUY" | "SELL" | "CLOSE_ALL" | "HOLD",
  "volume_lots": 0.0,
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

# ---- Judas position-management guardrails ----
# ADJUST may only lock profit (move SL past entry) after the position has progressed
# at least this far toward TP; prevents premature break-even / panic management.
JUDAS_ADJUST_MIN_TP_PROGRESS = 0.40


def validate_judas_adjust_decision(snapshot: MarketSnapshot, decision_dict: Dict[str, Any]) -> Optional[str]:
    """Validate an LLM ADJUST decision against an open Judas position.

    Returns a rejection reason when the ADJUST must be downgraded to HOLD with the
    position left untouched, or None when the ADJUST is structurally sound.
    Guards against: SL/TP placed on the wrong side of the market (would force a
    Profit-Lock exit or broker reject) and premature profit-locks at tiny profit.
    """
    pos = snapshot.position
    if pos is None:
        return "No open position to adjust (position is FLAT)"
    side = pos.resolved_side
    entry = pos.entry_price or 0.0
    bid = snapshot.bid or 0.0
    ask = snapshot.ask or 0.0
    new_sl = float(decision_dict.get("new_sl_price") or 0.0)
    new_tp = float(decision_dict.get("new_tp_price") or 0.0)
    tp = float(pos.tp_price or pos.tp or 0.0)

    if side == "BUY":
        if new_sl > 0 and new_sl >= bid:
            return (f"new_sl_price {new_sl:g} is at/above market bid {bid:g} on BUY - "
                    "a stop above the market cannot protect a long")
        if new_tp > 0 and new_tp <= ask:
            return f"new_tp_price {new_tp:g} is at/below market ask {ask:g} on BUY"
        if new_sl > 0 and entry > 0 and new_sl > entry and tp > entry:
            progress = (bid - entry) / (tp - entry)
            if progress < JUDAS_ADJUST_MIN_TP_PROGRESS:
                return (f"premature profit-lock: new_sl_price {new_sl:g} above entry {entry:g} while only "
                        f"+{max(bid - entry, 0.0):g} ({progress:.0%} of TP distance < {JUDAS_ADJUST_MIN_TP_PROGRESS:.0%})")
    elif side == "SELL":
        if new_sl > 0 and new_sl <= ask:
            return (f"new_sl_price {new_sl:g} is at/below market ask {ask:g} on SELL - "
                    "a stop below the market cannot protect a short")
        if new_tp > 0 and new_tp >= bid:
            return f"new_tp_price {new_tp:g} is at/above market bid {bid:g} on SELL"
        if new_sl > 0 and entry > 0 and new_sl < entry and tp > 0 and entry > tp:
            progress = (entry - ask) / (entry - tp)
            if progress < JUDAS_ADJUST_MIN_TP_PROGRESS:
                return (f"premature profit-lock: new_sl_price {new_sl:g} below entry {entry:g} while only "
                        f"+{max(entry - ask, 0.0):g} ({progress:.0%} of TP distance < {JUDAS_ADJUST_MIN_TP_PROGRESS:.0%})")
    return None


# ---- TMS position-management guardrails ----
# Allow a protective exit only after this share of the SL distance is given back.
TMS_CLOSE_MIN_ADVERSE_SL_RATIO = 0.60
# Allow a profit-taking exit from this reward:risk ratio onwards.
TMS_CLOSE_MIN_PROFIT_SL_RATIO = 1.00


def validate_tms_close_decision(snapshot: MarketSnapshot, decision_dict: Dict[str, Any]) -> Optional[str]:
    """Validate an LLM CLOSE_ALL against an open TMS/ORB position.

    Returns a rejection reason when the exit must be downgraded to HOLD, or None when valid.
    Accepts the exit only when one of these holds:
      - Macro H1 exit signal is true (BUY -> macro.exit_long, SELL -> macro.exit_short), or
      - Chart exit signal is true IF macro is unavailable (fallback for chart-only tests/setups), or
      - Adverse excursion >= 60% of the SL distance (capital protection), or
      - Profit >= 1:1 R (locking gains).
    Blocks premature exits on minor M15 pullbacks when Macro H1 structure remains intact.
    """
    pos = snapshot.position
    if pos is None:
        return None

    side = pos.resolved_side
    entry = pos.entry_price or 0.0
    bid = snapshot.bid or 0.0
    ask = snapshot.ask or 0.0
    sl = float(pos.sl_price or pos.sl or 0.0)

    chart = snapshot.chart_tms
    macro = snapshot.tms
    # Priority given to Macro H1 exit signals to avoid getting shaken out by M15 minor pullbacks
    if side == "BUY":
        exit_flag_ok = bool(macro and macro.exit_long) if macro else bool(chart and chart.exit_long)
        flag_desc = f"macro_exit_long={bool(macro and macro.exit_long)}, chart_exit_long={bool(chart and chart.exit_long)}"
    elif side == "SELL":
        exit_flag_ok = bool(macro and macro.exit_short) if macro else bool(chart and chart.exit_short)
        flag_desc = f"macro_exit_short={bool(macro and macro.exit_short)}, chart_exit_short={bool(chart and chart.exit_short)}"
    else:
        return None

    if exit_flag_ok:
        return None

    sl_dist = abs(entry - sl) if (entry > 0 and sl > 0) else 0.0
    if sl_dist <= 0:
        return None  # cannot size the excursion -> do not interfere

    adverse = (entry - bid) if side == "BUY" else (ask - entry)
    profit = (bid - entry) if side == "BUY" else (entry - ask)
    if adverse >= TMS_CLOSE_MIN_ADVERSE_SL_RATIO * sl_dist:
        return None
    if profit >= TMS_CLOSE_MIN_PROFIT_SL_RATIO * sl_dist:
        return None

    return (f"no {side}-side exit signal ({flag_desc}) and only "
            f"{adverse / sl_dist:.0%} of SL distance in drawdown "
            f"(protective exit needs >= {TMS_CLOSE_MIN_ADVERSE_SL_RATIO:.0%} or >= 1:1 R profit)")


def evaluate_judas_sweep_gate(snapshot: MarketSnapshot, account_id: Optional[str] = None) -> Optional[AgentDecision]:
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

        # Gate 2b: Structural invalidation. The cBot's Judas Structural Guard locks a sweep
        # side once an M15 bar closes decisively beyond the Asian boundary; the level is then
        # a breakout level, not a sweep level, so mean-reversion entries are dead for the
        # session. Mirrored here so the LLM is never asked for a trade the cBot must reject.
        if strat.bias_direction in ("BUY", "SELL"):
            broken = strat.asian_low_broken if strat.bias_direction == "BUY" else strat.asian_high_broken
            if broken:
                boundary = "Asian Low" if strat.bias_direction == "BUY" else "Asian High"
                return AgentDecision(
                    action="HOLD",
                    volume_lots=0.01,
                    sl_pips=0.0,
                    tp_pips=0.0,
                    confidence=90.0,
                    reason=(
                        f"Judas Sweep Gate: {boundary} was broken by a decisive M15 close this session "
                        f"(breakout day, mean-reversion invalidated). {strat.bias_direction} sweep side locked by the cBot Structural Guard."
                    ),
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
            elif "BTC" in sym_up:
                min_asian_pips, max_asian_pips = 10000.0, 400000.0  # $100 to $4,000 USD on BTC
            elif any(cr in sym_up for cr in ["ETH", "CRYPTO"]):
                min_asian_pips, max_asian_pips = 800.0, 35000.0     # $8 to $350 USD on ETH
            elif any(idx in sym_up for idx in ["UK100", "GB100"]):
                min_asian_pips, max_asian_pips = 120.0, 800.0       # 12-80 FTSE points (UK100 pip = 0.1 pt)
            elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "DJ30", "GER40", "JP225", "NIKKEI", "HK50", "US500"]):
                min_asian_pips, max_asian_pips = 50.0, 4000.0
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

        # Gate 5: One sweep trade per Asian boundary per session (repeat-liquidity-grab dedupe).
        # Prevents re-entering the same Asian Low/High after it was already swept & traded today.
        if strat.bias_direction in ("BUY", "SELL"):
            side = strat.bias_direction
            try:
                traded = portfolio_manager.count_positions_opened_on(
                    bot_id=snapshot.bot_id,
                    symbol=snapshot.symbol,
                    side=side,
                    account_id=account_id or snapshot.account_id or "default",
                )
            except Exception as ex:
                logger.warning(f"Judas sweep dedupe query failed: {ex}")
                traded = 0
            if traded > 0:
                boundary = "Asian Low" if side == "BUY" else "Asian High"
                return AgentDecision(
                    action="HOLD",
                    volume_lots=0.01,
                    sl_pips=0.0,
                    tp_pips=0.0,
                    confidence=90.0,
                    reason=f"Judas Sweep Gate: {boundary} sweep already traded {traded}x today ({side}); repeat sweep of the same liquidity level blocked until next session",
                    request_id=snapshot.request_id,
                    bot_id=snapshot.bot_id,
                    symbol=snapshot.symbol,
                    timeframe=snapshot.timeframe
                )

    return None

# A genuine expansion ends chop: allow a fresh, momentum-confirmed ORB breakout to
# bypass the choppy-regime gate instead of waiting for the regime label to catch up.
CHOPPY_OVERRIDE_MAX_BARS_SINCE_BREAKOUT = 3
# ...but not in an extreme chop regime (too many failed breakouts = chop trap), and
# only when the expansion is large enough relative to volatility.
CHOPPY_OVERRIDE_MAX_OR_FLIPS = 7
CHOPPY_OVERRIDE_MIN_BREAKOUT_ATR = 0.5

# ---- Calibrated breakout-distance limits per asset class (pips) ----
# direct_*: Model 1 (direct breakout entry) ceiling. Beyond it the breakout candle is
#           exhausted and only a qualified Model 2 retest + TDI bounce may enter.
# absolute_*: hard ceiling that ALSO blocks Model 2, i.e. price is at an extreme far from
#             the OR boundary. Both scale with ATR and are bounded by the per-class cap.
BREAKOUT_DISTANCE_LIMITS = (
    # (symbol tokens, prompt label, direct ATR x, direct cap, absolute ATR x, absolute cap)
    (("USTEC", "NAS100"), "USTEC/NAS100", 1.3, 700.0, 3.2, 2100.0),
    (("US30", "DJ30"), "US30/DJ30", 1.5, 950.0, 3.4, 2800.0),
    (("US500", "SPX500"), "US500/SPX500", 1.4, 150.0, 3.4, 450.0),
    (("DE40", "GER40"), "DE40/GER40", 1.5, 700.0, 3.4, 1900.0),
    (("UK100", "GB100"), "UK100/GB100", 1.5, 550.0, 3.4, 1600.0),
    (("JP225", "NIKKEI", "JPN225"), "JP225/Nikkei", 1.8, 3000.0, 3.8, 7000.0),
    (("HK50", "HSI"), "HK50/HangSeng", 1.5, 800.0, 3.4, 2200.0),
    (("XAU", "GOLD"), "Gold", 1.8, 1500.0, 3.6, 3500.0),
    (("BTC", "CRYPTO"), "BTC/Crypto", 1.5, 37500.0, 3.5, 130000.0),
    (("ETH", "SOL", "XRP"), "ETH/SOL/XRP", 1.5, 5250.0, 3.5, 35000.0),
    (("JPY",), "JPY Crosses", 1.6, 55.0, 3.6, 120.0),
    (("EURUSD", "GBPUSD", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"), "Forex Majors", 1.5, 30.0, 3.4, 70.0),
)

# Model 1 direct-entry window in M15 bars after the ORB breakout. MUST match the cBot
# parameter AiAgentBot.MaxBarsAfterBreakout, which decides `in_entry_window`.
MODEL1_ENTRY_WINDOW_BARS = 8

# Model 2 (retest + TDI bounce) stays valid this many M15 bars after the ORB breakout.
MAX_BARS_SINCE_BREAKOUT_MODEL2 = 20


def resolve_breakout_limits(symbol: str, atr_pips: Optional[float]) -> tuple:
    """(direct ceiling, absolute ceiling) in pips for a symbol, scaled by ATR."""
    sym = (symbol or "").upper()
    for tokens, _label, direct_atr, direct_cap, absolute_atr, absolute_cap in BREAKOUT_DISTANCE_LIMITS:
        if any(token in sym for token in tokens):
            break
    else:
        _tokens, _label, direct_atr, direct_cap, absolute_atr, absolute_cap = BREAKOUT_DISTANCE_LIMITS[-1]
    if atr_pips and atr_pips > 0:
        return min(atr_pips * direct_atr, direct_cap), min(atr_pips * absolute_atr, absolute_cap)
    return direct_cap, absolute_cap


def breakout_distance_prompt() -> str:
    """Per-class Model 1 ceilings for the LLM prompt (single source: BREAKOUT_DISTANCE_LIMITS)."""
    return ", ".join(f"<= {cap:.0f}p on {label}" for _tokens, label, _da, cap, _aa, _acap in BREAKOUT_DISTANCE_LIMITS)
def check_us_index_conflict(symbol: str, snapshot: MarketSnapshot, account_id: str) -> Optional[str]:
    """
    Check if a proposed US index trade direction conflicts with an existing open US index position.
    Returns a description of the opposing open position if a conflict exists, or None.
    """
    if not is_us_index(symbol):
        return None
    try:
        pm = get_portfolio_manager()
        conn = pm._get_conn()
        try:
            cursor = conn.execute(
                "SELECT symbol, side FROM positions WHERE status = 'open' AND account_id = ?",
                (account_id,)
            )
            open_pos = cursor.fetchall()
            if not open_pos:
                return None

            tms_bias = (snapshot.tms.bias or "").upper() if snapshot.tms else ""
            orb_dir = (snapshot.orb.breakout_direction or "").lower() if snapshot.orb else ""

            potential_buy = (tms_bias == "BULLISH" or orb_dir == "up")
            potential_sell = (tms_bias == "BEARISH" or orb_dir == "down")

            opposing = []
            for row in open_pos:
                pos_sym = row[0]
                pos_side = str(row[1]).upper()
                if is_us_index(pos_sym):
                    if pos_side == "BUY" and potential_sell and not potential_buy:
                        opposing.append(f"{pos_sym} ({pos_side})")
                    elif pos_side == "SELL" and potential_buy and not potential_sell:
                        opposing.append(f"{pos_sym} ({pos_side})")

            return ", ".join(opposing) if opposing else None
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"Error checking US index conflict: {e}")
        return None



def evaluate_cycle_gate(snapshot: MarketSnapshot, account_id: Optional[str] = None) -> Optional[AgentDecision]:
    """
    Deterministic Cycle Gate (Cost Gate) for TMS + ORB Strategy.
    Evaluates whether an expensive LLM call can be safely bypassed with an immediate deterministic action.
    Returns AgentDecision if gated, or None if LLM call is required.
    """
    if not snapshot.tms:
        return None

    # 1. When we HAVE an open position:
    # Trao toan quyen quyet dinh quan ly vi the cho AI (LLM) tren tat ca cac bot.
    # Khong can thiep hoac tu y CLOSE_ALL bang cac rule co hoc cung (nhu TDI M15 cross hay MFE giveback),
    # de AI co khong gian cho lenh tho (breathing room) va quan ly vi the thong minh.
    has_open_pos = (
        snapshot.position is not None
        or (snapshot.active_positions is not None and len(snapshot.active_positions) > 0)
    )
    if has_open_pos:
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

    # Gate 2.1.1: NYSE Cash Open Buffer (13:10 – 13:35 UTC / 9:10 – 9:35 AM NY)
    # Khung 15 phút trước và 5 phút sau giờ mở sàn Mỹ (9:30 AM NY) là giai đoạn gom hàng / quét thanh khoản
    # có xác suất False Breakout cao nhất trong ngày của phiên New York.
    # Chặn mở lệnh mới (chuyển về HOLD) trong khoảng thời gian này, bảo toàn vốn trước bẫy Pre-market sweep.
    session_name = (snapshot.session.session_name if snapshot.session else "").lower()
    is_ny_session = (
        "newyork" in session_name
        or "ny" in session_name
        or is_us_index(snapshot.symbol)
        or any(s in snapshot.symbol.upper() for s in ["XAU", "GOLD"])
    )

    if is_ny_session:
        current_dt = None
        if snapshot.bars and getattr(snapshot.bars[0], "time", None):
            try:
                t_str = str(snapshot.bars[0].time)
                if "T" in t_str:
                    current_dt = datetime.datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                else:
                    current_dt = datetime.datetime.strptime(t_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            except Exception:
                current_dt = None

        if current_dt is None:
            current_dt = datetime.datetime.now(datetime.timezone.utc)

        is_us_index_sym = is_us_index(snapshot.symbol)
        is_us_index_premarket = False
        is_nyse_buffer = False
        try:
            from zoneinfo import ZoneInfo
            ny_dt = current_dt.astimezone(ZoneInfo("America/New_York"))
            ny_minute = ny_dt.hour * 60 + ny_dt.minute
            # For US Equity Indices: Pre-market & Cash Open M15 formation (before 9:45 AM NY = 585 min)
            is_us_index_premarket = is_us_index_sym and (ny_minute < 585)
            # For general NY session (Gold, FX): 9:10 AM NY (550 min) to 9:35 AM NY (575 min)
            is_nyse_buffer = (550 <= ny_minute <= 575)
        except Exception:
            utc_dt = current_dt.astimezone(datetime.timezone.utc)
            utc_minute = utc_dt.hour * 60 + utc_dt.minute
            # In summer UTC-4: 9:45 AM NY is 13:45 UTC (825 min); in winter UTC-5: 14:45 UTC (885 min)
            is_us_index_premarket = is_us_index_sym and (utc_minute < 825)
            # 13:10 UTC is 790 min; 13:35 UTC is 815 min
            is_nyse_buffer = (790 <= utc_minute <= 815)

        if is_us_index_premarket:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason="Cycle gate: US Index Pre-market / Cash Open Buffer active (trading prohibited before 9:45 AM NY / 13:45 UTC summer). Awaiting Cash Open Range formation."
            )

        if is_nyse_buffer:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason="Cycle gate: NYSE Cash Open Buffer active (13:10 - 13:35 UTC / 9:10 - 9:35 AM NY). Pre-market liquidity sweep protection."
            )


    # Gate 2.1.3: US Index Alignment Gate (Directional Macro Flow)
    # Đảm bảo các chỉ số chứng khoán Mỹ (US30, USTEC, US500) phải giao dịch đồng pha theo dòng tiền vĩ mô.
    # Ngăn chặn quét tín hiệu / gọi LLM khi đang có vị thế chỉ số Mỹ khác mở ngược chiều.
    if is_us_index(snapshot.symbol):
        acc_id = account_id or _resolve_account(snapshot)
        opposing = check_us_index_conflict(snapshot.symbol, snapshot, acc_id)
        if opposing:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: US Index Alignment Guard - opposing open position in {opposing}. All US indices (US30, USTEC, US500) must trade in the same direction."
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

    # Gate 2.3: TMS Bias Gate. The cross lock never downgrades to NEUTRAL once a confirmed
    # cross exists, so this only fires while the bot has no confirmed macro cross yet.
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

    # Check if a qualified TDI Bounce or Dynamic EMA Retest Continuation (Model 2) is active
    chart_tms = snapshot.chart_tms or snapshot.tms
    is_standard_bounce = (
        (bias == "BULLISH" and chart_tms.tdi_bounce_bull and chart_tms.price_above_ema) or
        (bias == "BEARISH" and chart_tms.tdi_bounce_bear and chart_tms.price_below_ema)
    )
    is_ema_retest_continuation = False
    if bias == "BULLISH":
        is_ema_retest_continuation = (
            chart_tms.price_above_ema
            and chart_tms.green_tf_slope > 0
            and chart_tms.green_tf_value >= 48.0
            and not chart_tms.ha_turned_red
            and (chart_tms.long_entry or chart_tms.stoch_bull or chart_tms.angle_ok_long)
        )
    elif bias == "BEARISH":
        is_ema_retest_continuation = (
            chart_tms.price_below_ema
            and chart_tms.green_tf_slope < 0
            and chart_tms.green_tf_value <= 52.0
            and not chart_tms.ha_turned_green
            and (chart_tms.short_entry or chart_tms.stoch_bear or chart_tms.angle_ok_short)
        )
    has_bounce = is_standard_bounce or is_ema_retest_continuation

    # Gate 2.4.1: Calibrated Breakout Distance & Exhaustion Filter per Symbol/Asset Class
    # 1) max_direct_breakout_dist: Max distance for Model 1 Direct Breakout. Beyond this,
    #    the breakout candle is considered exhausted; direct entry is BANNED, requiring
    #    a structural Retest + TDI Bounce (Model 2).
    # 2) max_breakout_dist: Absolute ceiling beyond which even Model 2 Bounce trades are overextended.
    atr_ref = snapshot.atr_pips if snapshot.atr_pips and snapshot.atr_pips > 0 else None
    sym_upper = snapshot.symbol.upper()

    max_direct_breakout_dist, max_breakout_dist = resolve_breakout_limits(snapshot.symbol, atr_ref)

    # 1. Absolute overextension check (applies to both Model 1 and Model 2)
    if orb.breakout_distance_pips > max_breakout_dist:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Breakout overextended ({orb.breakout_distance_pips:.1f}p > max {max_breakout_dist:.1f}p threshold). Avoid chasing at extremes."
        )

    # 2. Exhaustion Breakout check for Model 1 Direct Entry:
    # If the breakout distance exceeds max_direct_breakout_dist, entering directly is chasing an exhaustion move.
    # Entry is strictly prohibited unless a qualified TDI Bounce (Model 2) has formed.
    if orb.breakout_distance_pips > max_direct_breakout_dist and not has_bounce:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=(
                f"Cycle gate: Breakout candle exhausted ({orb.breakout_distance_pips:.1f}p > direct max {max_direct_breakout_dist:.1f}p threshold). "
                f"Model 1 direct entry prohibited; wait for Retest + TDI Bounce (Model 2)."
            )
        )

    # For Gold (XAUUSD / GOLD), enforce minimum decisive breakout distance >= 150.0 pips ($1.50)
    # to filter out minor noise / false breakouts around OR boundaries.
    min_decisive_threshold = 150.0 if ("XAU" in sym_upper or "GOLD" in sym_upper) else None
    if min_decisive_threshold and orb.breakout_distance_pips < min_decisive_threshold and not has_bounce:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Gold breakout not decisive ({orb.breakout_distance_pips:.1f}p < min {min_decisive_threshold:.1f}p / $1.50 threshold)"
        )

    if not orb.is_decisive and not has_bounce:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            reason=f"Cycle gate: Breakout not decisive ({orb.breakout_distance_pips:.1f}p < threshold)"
        )

    # Model 2 Bounce / Retest stays valid until MAX_BARS_SINCE_BREAKOUT_MODEL2 bars
    if not orb.in_entry_window:
        if orb.bars_since_breakout > MAX_BARS_SINCE_BREAKOUT_MODEL2:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Breakout is stale (bars_since_breakout={orb.bars_since_breakout} > {MAX_BARS_SINCE_BREAKOUT_MODEL2}). Re-entry prohibited."
            )
        if not has_bounce:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=f"Cycle gate: Outside entry window (bars_since_breakout={orb.bars_since_breakout}) with no qualified Bounce/Retest"
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
    # The choppy label is backward-looking (it counts failed breakouts / OR flips), so
    # requiring "not choppy" to enter structurally forbids the very breakout that ends
    # the chop. A fresh, momentum-confirmed breakout is therefore allowed to override
    # the block; anything else stays gated.
    if snapshot.market and snapshot.market.regime == "choppy" and snapshot.market.or_flips >= 5:
        chart = snapshot.chart_tms
        fresh_momentum_confirmed = False
        expansion_ok = atr_ref is None or orb.breakout_distance_pips >= (CHOPPY_OVERRIDE_MIN_BREAKOUT_ATR * atr_ref)
        if (
            chart is not None
            and orb.breakout_direction in ("up", "down")
            and orb.bars_since_breakout <= CHOPPY_OVERRIDE_MAX_BARS_SINCE_BREAKOUT
            and snapshot.market.or_flips <= CHOPPY_OVERRIDE_MAX_OR_FLIPS
            and expansion_ok
        ):
            if orb.breakout_direction == "up":
                fresh_momentum_confirmed = chart.price_above_ema and chart.green_tf_slope > 0 and not chart.ha_turned_red
            else:
                fresh_momentum_confirmed = chart.price_below_ema and chart.green_tf_slope < 0 and not chart.ha_turned_green

        if not fresh_momentum_confirmed:
            return AgentDecision(
                action="HOLD",
                volume_lots=0.01,
                sl_pips=0.0,
                tp_pips=0.0,
                reason=(
                    f"Cycle gate: Market is CHOPPY ({snapshot.market.or_flips} failed OR breakouts) "
                    f"and no fresh momentum-confirmed expansion "
                    f"(bars={orb.bars_since_breakout}, dir={orb.breakout_direction}, "
                    f"dist={orb.breakout_distance_pips:.1f}p, atr={atr_ref if atr_ref else 0:.0f}p, "
                    f"max_flips={CHOPPY_OVERRIDE_MAX_OR_FLIPS}, min_dist={CHOPPY_OVERRIDE_MIN_BREAKOUT_ATR:.1f}x ATR)"
                )
            )
        logger.info(
            f"[CYCLE GATE] {snapshot.bot_id} Choppy override: fresh {orb.breakout_direction} breakout "
            f"(bars_since_breakout={orb.bars_since_breakout}, dist={orb.breakout_distance_pips:.1f}p, "
            f"or_flips={snapshot.market.or_flips}) with aligned momentum -> evaluating entry"
        )

    # All entry criteria met! Valid candidate setup -> Invoke LLM for entry sizing & SL/TP validation
    return None

def build_judas_sweep_system_prompt(snapshot: MarketSnapshot) -> str:
    return "You are an elite Algorithmic Trading AI Co-Pilot for cTrader. Analyze the real-time market snapshot and output strictly valid JSON format with keys: \"action\" (\"BUY\"|\"SELL\"|\"HOLD\"|\"ADJUST\"|\"CLOSE_ALL\"), \"volume_lots\" (number), \"sl_pips\" (number), \"tp_pips\" (number), \"new_sl_price\" (number), \"new_tp_price\" (number), \"confidence\" (number between 0 and 100), \"reason\" (concise technical rationale). MANDATORY FOR BUY/SELL/ADJUST: You MUST provide the exact target price in \"new_tp_price\" (e.g. 2524.00 for ETH, 80120.00 for BTC) and protective stop in \"new_sl_price\" (e.g. 2454.00 for ETH, 79600.00 for BTC), NEVER 0.0. Output NO markdown explanations outside the JSON object."
def build_judas_sweep_user_prompt(snapshot: MarketSnapshot) -> str:
    strat = snapshot.strategy or StrategyData()
    sym_up = snapshot.symbol.upper()
    if "XAU" in sym_up or "GOLD" in sym_up:
        spread_pips = round(abs(snapshot.ask - snapshot.bid) / 0.01, 1)
    elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "UK100", "GB100", "JP225", "NIKKEI", "HK50", "US500", "SPX500"]):
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
        elif any(idx in sym_up for idx in ["US30", "USTEC", "DE40", "NAS100", "UK100", "GB100", "JP225", "NIKKEI", "HK50", "US500", "SPX500"]):
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
    # 1. Format 50 chronological bars with Volume Delta approximation
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
            # Calculate Volume Delta & Imbalance:
            hl_range = h_val - l_val
            if hl_range > 0:
                buy_ratio = max(0.0, min(1.0, (c_val - l_val) / hl_range))
                delta_vol = v_val * (2.0 * buy_ratio - 1.0)
                delta_str = f" | Delta={'+' if delta_vol >= 0 else ''}{delta_vol:.0f}"
            else:
                delta_str = ""
            bar_lines.append(f"Bar[{bar_idx}]: O={format_price(o_val, snapshot.symbol)}, H={format_price(h_val, snapshot.symbol)}, L={format_price(l_val, snapshot.symbol)}, C={format_price(c_val, snapshot.symbol)}, V={v_val:.0f}{delta_str}")
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

    # Mirror of the cBot Judas Structural Guard. A decisive M15 close beyond a boundary
    # converts the sweep level into a breakout level: the mean-reversion side is dead.
    if strat.asian_low_broken and strat.asian_high_broken:
        structural_note = "BOTH Asian boundaries decisively broken -> breakout day, all sweep entries LOCKED. Output 'HOLD'."
    elif strat.asian_low_broken:
        structural_note = "Asian Low decisively broken (M15 close) -> BUY sweep side LOCKED, mean-reversion long invalidated."
    elif strat.asian_high_broken:
        structural_note = "Asian High decisively broken (M15 close) -> SELL sweep side LOCKED, mean-reversion short invalidated."
    else:
        structural_note = "Both Asian boundaries intact (sweep entries permitted)."

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
- Structural State: {structural_note}
⚠️ CONSTRAINT:
  - Gate=BUY -> Price swept Asian Low & rejected back up. You MAY ONLY suggest 'BUY' or 'HOLD'. NEVER 'SELL'.
  - Gate=SELL -> Price swept Asian High & rejected back down. You MAY ONLY suggest 'SELL' or 'HOLD'. NEVER 'BUY'.
  - Gate=MANAGE_ONLY -> Do NOT open new positions. Only 'ADJUST', 'HOLD', or 'CLOSE_ALL'.
  - Bars Since Sweep > 3 -> Signal is STALE. Strongly prefer 'HOLD'.
  - A LOCKED sweep side -> The boundary was already broken on a decisive M15 close. The Judas mean-reversion thesis is dead for this session; output 'HOLD'. NEVER propose an entry against a locked side.
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
3. Technical SL & TP (MANDATORY EXACT PRICES):
   - new_tp_price: Targeted at opposing Asian Range boundary (Asian High for BUY, Asian Low for SELL) or target liquidity pool. You MUST provide the exact price in "new_tp_price".
   - new_sl_price: Placed safely beyond the sweep extreme spike (min floor 200 pips). You MUST provide the exact price in "new_sl_price".
   - sl_pips & tp_pips: Must match the distance between entry and new_sl_price / new_tp_price.

=== 8. VALID ACTIONS ===
- BUY: Validated Bullish Judas Sweep (Asian Low fakeout) + Order Block bounce. Must set new_tp_price and new_sl_price!
- SELL: Validated Bearish Judas Sweep (Asian High fakeout) + Order Block rejection. Must set new_tp_price and new_sl_price!
- HOLD: Choppy consolidation inside Asian Range, no sweep, or conflicting HTF bias.

=== 9. REFERENCE FEW-SHOT EXAMPLES ===
Example 1 (High-Probability Asian Low Sweep -> BUY with exact prices):
{{
  "action": "BUY",
  "volume_lots": 0.0,
  "sl_pips": 2500.0,
  "tp_pips": 5100.0,
  "new_sl_price": 2454.00,
  "new_tp_price": 2524.00,
  "confidence": 88.0,
  "reason": "Price swept Asian Low during London Open, printed pinbar rejection with positive Delta (+450), and aligned with H1 bullish order block. SL below sweep spike at 2454.00, TP at Asian High 2524.00."
}}

Example 2 (False Breakout / Stale Signal -> HOLD):
{{
  "action": "HOLD",
  "volume_lots": 0.0,
  "sl_pips": 0.0,
  "tp_pips": 0.0,
  "new_sl_price": 0.0,
  "new_tp_price": 0.0,
  "confidence": 85.0,
  "reason": "Sweep occurred 4 bars ago without swift displacement back inside range; negative Delta indicates heavy absorption. Holding flat."
}}
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
- Structural State: {structural_note}
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
   - Do NOT panic on minor 1-2 bar pullbacks or wicks on M15 if Higher Timeframe (H1) trend remains aligned and structure is intact. Let the position breathe towards TP!
2. Action Decisions:
   - HOLD: Position healthy and progressing towards TP. (Default choice during normal fluctuations).
   - ADJUST: Move SL to Break-Even OR Trailing Stop behind a verified structural swing/Order Block.
     ⚠️ CRITICAL STOP LOSS GEOMETRY CONSTRAINT:
       * For an open BUY position: new_sl_price MUST BE STRICTLY LESS THAN Current Bid (new_sl_price < {format_price(snapshot.bid, snapshot.symbol)}). A stop at or above market price is geometrically impossible and rejected!
       * For an open SELL position: new_sl_price MUST BE STRICTLY GREATER THAN Current Ask (new_sl_price > {format_price(snapshot.ask, snapshot.symbol)}). A stop at or below market price is geometrically impossible and rejected!
       * If an open position is UNDERWATER and technical structure breaks against it, NEVER attempt to tighten SL past the market price! You MUST output action "CLOSE_ALL" instead!
     ⚠️ MANDATORY PROFIT LOCK-IN & BREAK-EVEN RULES:
       * When a position reaches >= 40% of the distance to TP (or >= 1.0x R:R), you MUST ADJUST SL to Break-Even or trail behind the nearest M15 swing!
       * Minimum profit required BEFORE moving SL to Break-Even / Trailing:
         - BTCUSD: Position in profit by at least +$60.00 price gain (6,000 pips) OR >= 40% distance to TP.
         - ETHUSD: Position in profit by at least +$6.00 price gain (600 pips) OR >= 40% distance to TP.
         - XAUUSD: Position in profit by at least +$3.00 price gain (300 pips) OR >= 40% distance to TP.
         - Forex: Position in profit by at least +15 to +20 pips OR >= 40% distance to TP.
       * SPREAD BUFFER ON BREAK-EVEN: When moving SL to protect an order, set SL with breathing room beyond entry (e.g. entry + $10 on BTC, entry + $1 on ETH, entry + $0.50 on Gold for BUY) to lock in commission/spread!
     ⚠️ MANDATORY OUTPUT: You MUST specify the exact absolute price level in "new_sl_price" (e.g. 2475.50 for ETHUSD, 79900.00 for BTCUSD, 2898.50 for XAUUSD) and/or "new_tp_price". NEVER leave new_sl_price as 0.0 when ADJUSTing!
   - CLOSE_ALL: Exit immediately at market if:
       a) Position is UNDERWATER and M15/H1 technical structure breaks against the trade (e.g. price broke below Asian Low / entry for BUY, or opposing CHoCH). Cut losses immediately at market; NEVER hold all the way to full SL or attempt impossible stops!
       b) Trade was in substantial profit and reverses, printing a confirmed opposing CHoCH on M15 (e.g. decisive close back below Asian Low / entry for BUY). Do NOT hold all the way to full SL!
       c) Major opposing H1/H4 structural reversal occurs.
   - BUY / SELL: Scale-in ONLY if trend is extremely strong with fresh unmitigated Order Block.
=== 7. ASSET-SPECIFIC PIP & PRICE RULES ===
- Crypto (ETHUSD, BTCUSD): 1 pip = 0.01 ($0.01 move). Always calculate and output exact absolute price in "new_sl_price" and "new_tp_price".
- Gold (XAUUSD): 1 pip = 0.01 ($1.00 move = 100 pips). Always output exact absolute price in "new_sl_price".
- Forex (EURUSD, GBPUSD): 1 pip = 0.0001 (EURJPY, GBPJPY: 1 pip = 0.01).

Reply strictly with JSON object."""

def generate_fallback_decision(snapshot: MarketSnapshot, error_msg: str) -> AgentDecision:
    """
    Deterministic rule-based fallback when LLM fails (timeout/error).
    Protects open positions instead of passively returning an empty HOLD.
    """
    pos = snapshot.position
    strat = snapshot.strategy or StrategyData()
    sym = snapshot.symbol
    bid = snapshot.bid
    ask = snapshot.ask

    # Flat position -> safe to HOLD
    if not pos:
        return AgentDecision(
            action="HOLD",
            volume_lots=0.01,
            sl_pips=0.0,
            tp_pips=0.0,
            confidence=50.0,
            reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Flat position held.",
            request_id=snapshot.request_id,
            bot_id=snapshot.bot_id,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe
        )

    side = (pos.resolved_side or pos.type or "").upper()
    entry = pos.entry_price or 0.0
    cur_sl = pos.sl or pos.sl_price or 0.0
    pnl = pos.resolved_pnl or pos.pnl or 0.0

    # 1. Position in profit -> Move SL to Break-Even only if profit is significant (not minor noise)
    sym_up = (sym or "").upper()
    if "BTC" in sym_up:
        min_be_profit_price = 150.0
    elif "ETH" in sym_up:
        min_be_profit_price = 15.0
    elif "XAU" in sym_up or "GOLD" in sym_up:
        min_be_profit_price = 5.0
    elif "JPY" in sym_up:
        min_be_profit_price = 0.20  # ~20 pips
    else:
        min_be_profit_price = 0.0020  # ~20 pips for standard forex

    price_gain = (entry - ask) if side == "SELL" else (bid - entry)
    if price_gain >= min_be_profit_price and entry > 0:
        spread = abs(ask - bid)
        safe_be_sl = round(entry + spread * 0.5, 2) if side == "SELL" else round(entry - spread * 0.5, 2)
        if side == "SELL" and (cur_sl == 0 or cur_sl > safe_be_sl):
            return AgentDecision(
                action="ADJUST",
                new_sl_price=safe_be_sl,
                confidence=75.0,
                reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Position in verified profit (+${price_gain:.2f} >= +${min_be_profit_price:.2f}) -> Moving SL to Break-Even ({safe_be_sl}).",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )
        elif side == "BUY" and (cur_sl == 0 or cur_sl < safe_be_sl):
            return AgentDecision(
                action="ADJUST",
                new_sl_price=safe_be_sl,
                confidence=75.0,
                reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Position in verified profit (+${price_gain:.2f} >= +${min_be_profit_price:.2f}) -> Moving SL to Break-Even ({safe_be_sl}).",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

    # 2. Position in drawdown: check recent swing high/low to tighten SL safely
    if side == "SELL" and strat.recent_high > 0 and ask > 0:
        if strat.recent_high > ask and (cur_sl == 0 or strat.recent_high < cur_sl):
            return AgentDecision(
                action="ADJUST",
                new_sl_price=strat.recent_high,
                confidence=70.0,
                reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Tightening SELL SL to recent swing high ({strat.recent_high}) to cap risk.",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )
    elif side == "BUY" and strat.recent_low > 0 and bid > 0:
        if strat.recent_low < bid and (cur_sl == 0 or strat.recent_low > cur_sl):
            return AgentDecision(
                action="ADJUST",
                new_sl_price=strat.recent_low,
                confidence=70.0,
                reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Tightening BUY SL to recent swing low ({strat.recent_low}) to cap risk.",
                request_id=snapshot.request_id,
                bot_id=snapshot.bot_id,
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe
            )

    # 3. Default fallback: keep position with current protective SL
    return AgentDecision(
        action="HOLD",
        volume_lots=0.01,
        sl_pips=0.0,
        tp_pips=0.0,
        confidence=50.0,
        reason=f"[SAFETY FALLBACK] LLM call failed ({error_msg}). Retaining protective SL ({cur_sl}).",
        request_id=snapshot.request_id,
        bot_id=snapshot.bot_id,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe
    )

@app.post("/trade", response_model=AgentDecision)
async def trade_decision(snapshot: MarketSnapshot):
    account_id = _resolve_account(snapshot)
    is_flowrsi = is_flow_rsi_bot(snapshot)
    is_judas = False if is_flowrsi else is_judas_sweep_bot(snapshot)
    # Heartbeat for the watchdog's stale-bar-feed check: it compares this against the
    # bot's own session window to catch a cBot that is up but no longer processing bars.
    record_bot_snapshot(f"{account_id}/{snapshot.bot_id}")
    
    pos_data = None
    if snapshot.position:
        pos_data = {
            "side": snapshot.position.resolved_side,
            "entry_price": snapshot.position.entry_price,
            "unrealized_pnl": snapshot.position.resolved_pnl,
            "unrealized_pnl_pips": snapshot.position.resolved_pnl_pips,
            "mfe_pips": snapshot.position.mfe_pips,
            "giveback_pips": snapshot.position.giveback_pips,
            "sl_price": snapshot.position.sl or snapshot.position.sl_price,
            "tp_price": snapshot.position.tp or snapshot.position.tp_price,
        }
    portfolio_manager.update_market_price(snapshot.symbol, snapshot.bid, snapshot.ask, bot_id=snapshot.bot_id, position_data=pos_data, account_id=account_id)
    if is_flowrsi:
        pos_str = f"{snapshot.position.resolved_side} pnl=${snapshot.position.resolved_pnl:.2f}" if snapshot.position else "FLAT"
        rsi_cross = snapshot.rsi_cross_signal or "None"
        fvg_str = f"FVG={snapshot.fvg_type}" if snapshot.in_fvg_zone else "FVG=None"
        zone_str = "Discount" if snapshot.is_discount else ("Premium" if snapshot.is_premium else "Eq")
        cand_str = snapshot.candidate_action or "NONE"

        logger.info(
            f"[SNAPSHOT FLOW_RSI] {account_id}/{snapshot.bot_id} | {snapshot.symbol} {snapshot.timeframe} | "
            f"Bid={snapshot.bid:g} Ask={snapshot.ask:g} | RSI_Cross={rsi_cross} (F={snapshot.fast_rsi} S={snapshot.slow_rsi}) | "
            f"{fvg_str} | Zone={zone_str} | Candidate={cand_str} | Pos={pos_str}"
        )

        # Gate Check: If no entry candidate and no open position, gate as HOLD without calling LLM
        if cand_str == "NONE" and not snapshot.position:
            logger.info(
                f"[FLOW_RSI GATE] {account_id}/{snapshot.bot_id} -> GATED: HOLD | "
                f"Reason: No active Nested RSI setup (Fast={snapshot.fast_rsi}, Slow={snapshot.slow_rsi}, Signal={rsi_cross})"
            )
            return AgentDecision(action="HOLD", confidence=85.0, reason=f"No active setup (RSI={rsi_cross}, FVG={snapshot.fvg_type})")

        # Build specialized FlowRSI system & user prompt
        system_prompt = (
            "You are an elite Quantitative FX Co-Pilot specializing in Nested RSI momentum and SMC market structure.\n"
            "Analyze the real-time market snapshot and output strictly valid JSON format with keys:\n"
            '{"action": "BUY"|"SELL"|"HOLD"|"ADJUST"|"CLOSE_ALL", "volume_lots": 0.0, "sl_pips": 0.0, "tp_pips": 0.0, '
            '"new_sl_price": null, "new_tp_price": null, "confidence": 0-100, "reason": "concise rationale"}\n'
            "Rule: Volume is 100% managed by cBot risk engine; keep volume_lots=0.0.\n"
            "Rule: ENTRY STOPS AND TARGETS ARE THE ENGINE'S, NOT YOURS. For BUY/SELL return "
            "sl_pips=0, tp_pips=0, new_sl_price=null, new_tp_price=null — the cBot's ATR/structural "
            "engine sizes them (it is the 'Proposed Technical Setup' shown below, given as context "
            "for your confidence, not as a number to restate or improve). Anything you return on a "
            "BUY/SELL is discarded. Your job there is DIRECTION and TIMING only.\n"
            "Rule: On ADJUST you DO own the stop, but express it as new_sl_price / new_tp_price in "
            "absolute price. Never as pips: your pip scale is not the broker's.\n"
            "=== POSITION MANAGEMENT DISCIPLINE ===\n"
            "1. GIVE POSITIONS BREATHING ROOM (HOLD): Allow open positions breathing room for normal pullbacks and market noise. "
            "Do NOT panic-close or micro-manage positions that are flat, slightly underwater (e.g. within normal spread/minor pullback), or in early development.\n"
            "2. CUT LOSS EARLY (CLOSE_ALL): Only execute CLOSE_ALL when an open position suffers MEANINGFUL adverse movement "
            "(loss >= 0.5R or >= 10 pips FX / >= 100 pips Gold) AND clear market structure decisively breaks against it "
            "(e.g. sustained opposing RSI crossover with structural swing breakdown). Never exit early on minor noise.\n"
            "3. PROTECT PROFITS (ADJUST or CLOSE_ALL): When an open position has captured significant profit (>= 1.0 R:R or >= 15 pips FX / >= 150 pips Gold) "
            "and displays clear momentum exhaustion or structural reversal, lock in gains by adjusting SL or closing. Do not exit prematurely for petty cents.\n"
            "4. NEW ENTRIES: If candidate_action is BUY/SELL, confirm with confidence >= 75% only when Nested RSI cross and SMC zone align."
        )
        user_prompt = (
            f"Symbol: {snapshot.symbol} ({snapshot.timeframe})\n"
            f"Current Bid={snapshot.bid:g}, Ask={snapshot.ask:g}, Spread={snapshot.spread_pips or 0:.1f}p\n"
            f"Nested RSI: Fast={snapshot.fast_rsi}, Slow={snapshot.slow_rsi}, Signal={snapshot.rsi_cross_signal}\n"
            f"SMC: Zone={zone_str}, InFVG={snapshot.in_fvg_zone} ({snapshot.fvg_type}), LiquiditySwept={snapshot.liquidity_swept} ({snapshot.swept_liquidity_type})\n"
            f"Proposed Technical Setup: Candidate={cand_str}, SL={snapshot.technical_sl_price}, TP={snapshot.technical_tp_price}, RR={snapshot.technical_risk_reward}\n"
            f"Open Position Status: {pos_str}\n"
            f"Carefully evaluate Open Position Status: Give trades breathing room; protect gains if >=1.0R; cut loss only on decisive structural breakdown; else HOLD or confirm entry."
        )
    elif is_judas:
        strat = snapshot.strategy
        asian_str = f"Asian=[{strat.asian_low:g}...{strat.asian_high:g}] ({strat.asian_range_pips:.0f}p)" if strat else "Asian=N/A"
        kz_str = strat.killzone_session if strat else "N/A"
        bias_str = f"{strat.bias_direction} ({strat.traditional_signal})" if strat else "N/A"
        pos_str = f"{snapshot.position.resolved_side} pnl=${snapshot.position.resolved_pnl:.2f}" if snapshot.position else "FLAT"
        # The cBot's Judas Structural Guard state, so a blocked-looking BUY is explainable
        # from the server log alone instead of requiring the container logs.
        locked = []
        if strat and strat.asian_low_broken:
            locked.append("Low->BUY_LOCKED")
        if strat and strat.asian_high_broken:
            locked.append("High->SELL_LOCKED")
        lock_str = ",".join(locked) if locked else "none"
        pip_str = f"{strat.pip_size:g}" if strat and strat.pip_size > 0 else "n/a"

        logger.info(
            f"[SNAPSHOT SMC] {account_id}/{snapshot.bot_id} | {snapshot.symbol} {snapshot.timeframe} | "
            f"Bid={snapshot.bid:g} Ask={snapshot.ask:g} | {asian_str} | Locked={lock_str} | PipSize={pip_str} | "
            f"KZ={kz_str} | Gate={bias_str} | Pos={pos_str}"
        )

        # SMC Judas Sweep Gate Evaluation
        gated_decision = evaluate_judas_sweep_gate(snapshot, account_id=account_id)
        if gated_decision is not None:
            logger.info(f"[JUDAS GATE] {account_id}/{snapshot.bot_id} -> GATED: {gated_decision.action} | Reason: {gated_decision.reason}")
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
            b = snapshot.bars[0]
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

        gated_decision = evaluate_cycle_gate(snapshot, account_id=account_id)
        if gated_decision is not None:
            logger.info(f"[CYCLE GATE] {account_id}/{snapshot.bot_id} -> GATED: {gated_decision.action} | Reason: {gated_decision.reason}")
            return gated_decision

        system_prompt = build_system_prompt(snapshot)
        user_prompt = build_user_prompt(snapshot)

    # Check portfolio risk before allowing new trades
    has_open = snapshot.position is not None or (snapshot.active_positions is not None and len(snapshot.active_positions) > 0)
    # Check News Blackout Shield (e.g. Red news or EIA Crude Oil inventories) before opening new positions
    if not has_open:
        try:
            from app.news_service import is_news_blackout_active
            is_bo, bo_title, bo_mins = await is_news_blackout_active(snapshot.symbol, pause_before_mins=30, pause_after_mins=30)
            if is_bo:
                logger.info(f"[{account_id}/{snapshot.bot_id}] News Blackout Shield active: {bo_title} ({bo_mins}m remaining). Gated HOLD.")
                return AgentDecision(
                    action="HOLD",
                    volume_lots=0.01,
                    sl_pips=0,
                    tp_pips=0,
                    reason=f"News Blackout Shield active: {bo_title} ({bo_mins}m remaining)",
                    request_id=snapshot.request_id,
                    bot_id=snapshot.bot_id,
                    symbol=snapshot.symbol,
                    timeframe=snapshot.timeframe
                )
        except Exception as ex_news:
            logger.warning(f"[{account_id}/{snapshot.bot_id}] Error checking news blackout shield: {ex_news}")

    if not has_open:
        can_trade, reason = portfolio_manager.check_risk(
            symbol=snapshot.symbol,
            side="BUY",  # Will be determined by LLM, checking capacity
            volume=0.01,
            account_balance=snapshot.account_balance,
            account_id=account_id,
            used_margin=snapshot.account_margin
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
        
        kwargs = {"temperature": 0.1, "timeout": TRADE_LLM_TIMEOUT, "max_retries": TRADE_LLM_MAX_RETRIES}
        if hasattr(llm_client, 'client') and hasattr(llm_client.client, 'chat'):
            kwargs["response_format"] = {"type": "json_object"}
        
        try:
            result_str = await asyncio.wait_for(llm_client.chat(messages, **kwargs), timeout=TRADE_LLM_DEADLINE)
        except asyncio.TimeoutError:
            raise TimeoutError(f"no LLM answer within the {TRADE_LLM_DEADLINE:.0f}s /trade deadline") from None
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
        if is_flowrsi:
            action_val = str(decision_dict.get("action", "HOLD")).upper()
            pip_size = (
                snapshot.pip_size if snapshot.pip_size > 0
                else _pip_size_for_symbol((snapshot.symbol or "").upper())
            )

            if action_val in ("BUY", "SELL"):
                # "LLM proposes, Code disposes": on an entry the ATR/structural engine owns
                # the stop and the target. FlowRsiBot prefers decision.new_sl_price over its
                # own fallbackSL, so leaving the model's levels in place hands it the whole
                # risk leg -- and it prices them on its own pip scale (ETHUSD 295.4p against
                # the 2891.3p actually placed, BTCUSD 731.02p against 76042.4p). Clear them
                # and the bot falls back to the technicalSL/technicalTP it computed.
                proposed = [decision_dict.get(k) for k in ("sl_pips", "tp_pips", "new_sl_price", "new_tp_price")]
                if any(proposed):
                    logger.info(
                        f"[{account_id}/{snapshot.bot_id}] [FLOW_RSI SL/TP AUTHORITY] Discarded model levels "
                        f"(sl={proposed[0]} tp={proposed[1]} sl_price={proposed[2]} tp_price={proposed[3]}); "
                        f"engine stop {snapshot.technical_sl_price} / target {snapshot.technical_tp_price} stands."
                    )
                decision_dict["sl_pips"] = 0.0
                decision_dict["tp_pips"] = 0.0
                decision_dict["new_sl_price"] = 0.0
                decision_dict["new_tp_price"] = 0.0

            elif action_val == "ADJUST":
                # Managing an open position is the model's call, but only against a price.
                # A bare sl_pips carries no scale: FlowRsiBot multiplies it by Symbol.PipSize,
                # which would turn BTCUSD's 731.02 into a $7.31 stop. Re-derive the pips from
                # the price the model gave, measured where the bot measures them (a long is
                # closed at the bid, a short at the ask), and refuse an adjustment with no price.
                try:
                    new_sl = float(decision_dict.get("new_sl_price") or 0.0)
                    new_tp = float(decision_dict.get("new_tp_price") or 0.0)
                except (TypeError, ValueError):
                    new_sl = new_tp = 0.0
                pos_side = snapshot.position.resolved_side if snapshot.position else "BUY"
                exit_ref = snapshot.bid if pos_side == "BUY" else snapshot.ask

                decision_dict["sl_pips"] = round(abs(new_sl - exit_ref) / pip_size, 1) if new_sl > 0 else 0.0
                decision_dict["tp_pips"] = round(abs(new_tp - exit_ref) / pip_size, 1) if new_tp > 0 else 0.0

                if new_sl <= 0 and new_tp <= 0:
                    logger.warning(
                        f"[{account_id}/{snapshot.bot_id}] [FLOW_RSI ADJUST GUARD] ADJUST -> HOLD: no "
                        f"new_sl_price/new_tp_price to anchor the move; bare pips carry no reliable scale."
                    )
                    decision_dict["action"] = "HOLD"
                    decision_dict["reason"] = (
                        f"[ADJUST Guard] No target price supplied. {decision_dict.get('reason', '')}"
                    )
        if is_judas:
            action_val = str(decision_dict.get("action", "HOLD")).upper()
            sym_up = (snapshot.symbol or "").upper()
            pip_size = (
                snapshot.strategy.pip_size
                if snapshot.strategy and snapshot.strategy.pip_size > 0
                else _pip_size_for_symbol(sym_up)
            )
            entry_ref = snapshot.ask if action_val == "BUY" else snapshot.bid

            # Structural Guard mirror: never forward an entry against a boundary the cBot's
            # Judas Structural Guard already locked (decisive M15 close beyond it). Without
            # this the server logs a confident BUY that the cBot silently discards.
            if snapshot.strategy and action_val in ("BUY", "SELL"):
                locked = (
                    snapshot.strategy.asian_low_broken if action_val == "BUY"
                    else snapshot.strategy.asian_high_broken
                )
                if locked:
                    boundary = "Asian Low" if action_val == "BUY" else "Asian High"
                    logger.warning(
                        f"[{account_id}/{snapshot.bot_id}] [JUDAS STRUCTURAL GUARD] {action_val} blocked -> HOLD: "
                        f"{boundary} already broken by a decisive M15 close this session."
                    )
                    decision_dict["action"] = "HOLD"
                    decision_dict["reason"] = (
                        f"[Structural Guard] {boundary} broken by a decisive M15 close this session. "
                        f"Sweep side locked. {decision_dict.get('reason', '')}"
                    )
                    action_val = "HOLD"

            if action_val in ("BUY", "SELL"):
                try:
                    new_tp = float(decision_dict.get("new_tp_price") or 0.0)
                    new_sl = float(decision_dict.get("new_sl_price") or 0.0)
                    tp_p = float(decision_dict.get("tp_pips") or 0.0)
                    sl_p = float(decision_dict.get("sl_pips") or 0.0)
                    strat = snapshot.strategy

                    # 1. Target TP price fallback to Asian boundary if omitted
                    if new_tp <= 0 and strat:
                        if action_val == "BUY" and strat.asian_high > snapshot.ask:
                            new_tp = strat.asian_high
                            decision_dict["new_tp_price"] = new_tp
                        elif action_val == "SELL" and strat.asian_low > 0 and strat.asian_low < snapshot.bid:
                            new_tp = strat.asian_low
                            decision_dict["new_tp_price"] = new_tp

                    # 2. Harmonize tp_pips with new_tp_price or correct crypto pip scale
                    if new_tp > 0:
                        calc_tp_pips = round(abs(new_tp - entry_ref) / pip_size, 1)
                        if tp_p <= 0 or abs(tp_p - calc_tp_pips) > calc_tp_pips * 0.4:
                            decision_dict["tp_pips"] = calc_tp_pips
                    elif any(c in sym_up for c in ["BTC", "ETH"]) and strat and strat.asian_range_pips > 1000:
                        if 0 < tp_p < 800 and (tp_p * 10) <= strat.asian_range_pips * 1.5:
                            decision_dict["tp_pips"] = tp_p * 10

                    # 3. Harmonize sl_pips with new_sl_price
                    if new_sl > 0:
                        calc_sl_pips = round(abs(entry_ref - new_sl) / pip_size, 1)
                        if sl_p <= 0 or abs(sl_p - calc_sl_pips) > calc_sl_pips * 0.4:
                            decision_dict["sl_pips"] = calc_sl_pips
                except Exception as ex:
                    logger.warning(f"Error harmonizing Judas decision: {ex}")

            # ADJUST Guard: deterministic protection against LLM position-management overreach.
            # Downgrade to HOLD (position untouched) when the proposed SL/TP is geometrically
            # invalid for the open side, or when locking profit before >= 40% progress to TP.
            if action_val == "ADJUST":
                try:
                    adjust_reject_reason = validate_judas_adjust_decision(snapshot, decision_dict)
                except Exception as ex:
                    logger.warning(f"Judas ADJUST validation error: {ex}")
                    adjust_reject_reason = None
                if adjust_reject_reason:
                    pos = snapshot.position
                    underwater = pos and (
                        pos.resolved_pnl < -0.1 or 
                        (pos.entry_price and snapshot.bid and pos.resolved_side == "BUY" and snapshot.bid < pos.entry_price) or
                        (pos.entry_price and snapshot.ask and pos.resolved_side == "SELL" and snapshot.ask > pos.entry_price)
                    )
                    reason_text = str(decision_dict.get("reason", "")).lower()
                    structural_invalidation = any(k in reason_text for k in ["break", "broke", "invalid", "fail", "exit", "close", "reversal", "underwater"])
                    wrong_side_sl = "a stop above the market cannot protect a long" in adjust_reject_reason or "a stop below the market cannot protect a short" in adjust_reject_reason

                    if underwater and wrong_side_sl and structural_invalidation:
                        logger.warning(
                            f"[{account_id}/{snapshot.bot_id}] [JUDAS ADJUST GUARD] LLM attempted impossible SL on underwater position ({adjust_reject_reason}) due to structural breakdown. Converting ADJUST -> CLOSE_ALL to protect capital."
                        )
                        decision_dict["action"] = "CLOSE_ALL"
                        decision_dict["reason"] = (
                            f"[ADJUST Guard -> Emergency Exit] Structural breakdown on underwater position with impossible SL ({adjust_reject_reason}). Converted to CLOSE_ALL. "
                            f"{decision_dict.get('reason', '')}"
                        )
                    else:
                        logger.warning(
                            f"[{account_id}/{snapshot.bot_id}] [JUDAS ADJUST GUARD] ADJUST rejected -> HOLD: "
                            f"{adjust_reject_reason}. Position left untouched."
                        )
                        decision_dict["action"] = "HOLD"
                        decision_dict["reason"] = (
                            f"[ADJUST Guard] {adjust_reject_reason}. Position left untouched. "
                            f"{decision_dict.get('reason', '')}"
                        )
        # Server-side Guardrail: Block BUY/SELL entries with low confidence (< 75.0%)
        min_conf_threshold = 75.0
        action_str = str(decision_dict.get("action", "HOLD")).upper()
        conf_val = float(decision_dict.get("confidence", 80.0) or 0.0)
        if action_str in ("BUY", "SELL") and conf_val < min_conf_threshold:
            logger.warning(
                f"[{account_id}/{snapshot.bot_id}] Guardrail blocked low-confidence {action_str}: "
                f"{conf_val:.1f}% < {min_conf_threshold:.1f}%. Fallback to HOLD."
            )
            decision_dict["action"] = "HOLD"
            decision_dict["reason"] = f"[Guardrail Blocked] Confidence {conf_val:.1f}% < {min_conf_threshold:.1f}% threshold. {decision_dict.get('reason', '')}"

        # Server-side Guardrail: US Index Alignment & Portfolio Risk Check on final decision
        if action_str in ("BUY", "SELL"):
            can_trade, risk_reason = portfolio_manager.check_risk(
                symbol=snapshot.symbol,
                side=action_str,
                volume=float(decision_dict.get("volume_lots") or 0.01),
                account_balance=snapshot.account_balance,
                account_id=account_id,
                used_margin=snapshot.account_margin
            )
            if not can_trade:
                logger.warning(
                    f"[{account_id}/{snapshot.bot_id}] [RISK GUARD] {action_str} rejected -> HOLD: {risk_reason}"
                )
                decision_dict["action"] = "HOLD"
                decision_dict["reason"] = f"[Risk Guard] {risk_reason}. {decision_dict.get('reason', '')}"

        # TMS/ORB CLOSE_ALL Guard: block panic exits with a wrong-side or missing reversal signal.
        if not is_judas and not is_flowrsi and action_str == "CLOSE_ALL":
            close_reject_reason = validate_tms_close_decision(snapshot, decision_dict)
            if close_reject_reason:
                logger.warning(
                    f"[{account_id}/{snapshot.bot_id}] [TMS CLOSE GUARD] CLOSE_ALL rejected -> HOLD: "
                    f"{close_reject_reason}. Position left untouched."
                )
                decision_dict["action"] = "HOLD"
                decision_dict["reason"] = (
                    f"[CLOSE Guard] {close_reject_reason}. Position left untouched. "
                    f"{decision_dict.get('reason', '')}"
                )

        vol_display = "Auto (Risk Engine)" if float(decision_dict.get('volume_lots', 0) or 0) == 0 else f"{decision_dict.get('volume_lots')} lots"
        logger.info(
            f"[LLM DECISION] {account_id}/{snapshot.bot_id} -> Action: {decision_dict.get('action', 'HOLD')} | "
            f"Vol: {vol_display} | SL: {decision_dict.get('sl_pips', 0)}p | "
            f"TP: {decision_dict.get('tp_pips', 0)}p | Conf: {decision_dict.get('confidence', 80.0):.1f}% | "
            f"Reason: {decision_dict.get('reason', '')}"
        )

        try:
            record_ai_decision(decision_dict)
            await broadcast_decision(decision_dict)
        except Exception:
            pass

        return AgentDecision(**decision_dict)
    except Exception as e:
        error_desc = describe_llm_error(e)
        logger.error(f"[{account_id}/{snapshot.bot_id}] LLM Error: {error_desc}")
        fallback = generate_fallback_decision(snapshot, error_desc)
        logger.info(
            f"[FALLBACK DECISION] {account_id}/{snapshot.bot_id} -> Action: {fallback.action} | "
            f"new_sl_price: {fallback.new_sl_price} | Conf: {fallback.confidence:.1f}% | Reason: {fallback.reason}"
        )
        # Record it like a model decision so the dashboard shows the bot running on the fallback.
        try:
            fallback_dict = fallback.model_dump()
            fallback_dict["is_fallback"] = True
            record_ai_decision(fallback_dict)
            await broadcast_decision(fallback_dict)
        except Exception:
            pass
        return fallback

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

        # A tick carries its own P&L sample, so refreshing the dashboard costs one small
        # broadcast instead of rebuilding the whole positions payload for three account
        # scopes from the database (~86 ms of CPU per tick - a 1 Hz stream cannot afford it).
        try:
            pnl = float(request["pnl"]) if request.get("pnl") is not None else None
            pips = float(request["pips"]) if request.get("pips") is not None else None
        except (TypeError, ValueError):
            pnl = pips = None
        # FlowRsiBot posts a `positions` list instead of flat fields; its first entry is the
        # position the dashboard row shows (one per bot).
        levels = tick_levels(request)
        positions = request.get("positions")
        if pnl is None and isinstance(positions, list) and positions and isinstance(positions[0], dict):
            first = positions[0]
            try:
                pnl = float(first["net_profit"]) if first.get("net_profit") is not None else None
                pips = float(first["pips"]) if first.get("pips") is not None else None
            except (TypeError, ValueError):
                pnl = pips = None
            levels = tick_levels(first)
        if pnl is not None:
            portfolio_manager.update_position_metrics(bot_id, pnl, pips or 0.0, account_id=account_id,
                                                      levels=levels)

        try:
            await broadcast_tick(symbol=symbol, bid=bid, ask=ask, account_id=account_id,
                                 bot_id=bot_id, pnl=pnl, pips=pips, levels=levels)
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
        try:
            await broadcast_event(event_type=event_type, message=message, bot_id=bot_id, account_id=account_number)
        except Exception:
            pass
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling cbot event: {e}")
        return {"status": "error", "message": str(e)}


def _level_price(value) -> Optional[float]:
    """An SL/TP price from a bot report; cTrader has no level as null, older bots send 0."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


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

        # The cBot has always sent this; it now narrows the close / partial-close updates
        # to one position instead of every open row for the (bot_id, symbol) pair.
        raw_ctrader_id = request.get("ctrader_id")
        try:
            ctrader_id = int(raw_ctrader_id) if raw_ctrader_id not in (None, "") else None
        except (TypeError, ValueError):
            ctrader_id = None
        
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
                sl_price=_level_price(request.get("sl_price")),
                tp_price=_level_price(request.get("tp_price")),
                bot_id=bot_id,
                symbol=symbol,
                side=side,
                volume=volume,
                entry_price=entry_price,
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                account_id=account_id,
                ctrader_id=ctrader_id
            )
            
            if success:
                logger.info(f"[PORTFOLIO EVENT] OPEN | {account_id}/{bot_id} | {symbol} {side} {volume} lots @ {entry_price} | SL={sl_pips}p TP={tp_pips}p")
                try:
                    await broadcast_update(account_id=account_id)
                except Exception:
                    pass
                try:
                    await broadcast_event("TRADE_OPEN", f"OPEN {symbol} {side} {volume}L {bot_id}", bot_id=bot_id, account_id=str(account_id))
                except Exception:
                    pass
                return {"status": "success", "message": "Position registered"}
            else:
                return {"status": "error", "message": "Failed to register position"}
        
        elif action in ("ping", "sync"):
            logger.info(f"[PORTFOLIO EVENT] SYNC | {account_id}/{bot_id} | Balance: ${account_balance:.2f} | Equity: ${account_equity:.2f}")
            try:
                await broadcast_update(account_id=account_id)
            except Exception:
                pass
            return {"status": "success", "message": f"Account {account_id} synced", "account_id": account_id}
        
        elif action == "partial_close":
            # cTrader's Positions.Closed does not fire on a partial close, so the bot
            # reports it explicitly: bank the realised P&L on the still-open row and
            # shrink its volume to what is actually left running.
            remaining_volume = float(request.get("remaining_volume", 0) or 0)
            realized_pnl = float(request.get("realized_pnl", 0) or 0)
            closed_volume = float(request.get("closed_volume", 0) or 0)

            success = portfolio_manager.record_partial_close(
                bot_id=bot_id,
                symbol=symbol,
                remaining_volume=remaining_volume,
                realized_pnl=realized_pnl,
                account_id=account_id,
                ctrader_id=ctrader_id
            )

            if success:
                logger.info(
                    f"[PORTFOLIO EVENT] PARTIAL CLOSE | {account_id}/{bot_id} | {symbol} | "
                    f"closed {closed_volume} lots for ${realized_pnl:.2f} | {remaining_volume} lots remaining"
                )
                try:
                    await broadcast_update(account_id=account_id)
                except Exception:
                    pass
                try:
                    await broadcast_event(
                        "TRADE_PARTIAL_CLOSE",
                        f"PARTIAL {symbol} {bot_id} {closed_volume}L PnL: ${realized_pnl:.2f}",
                        bot_id=bot_id,
                        account_id=str(account_id),
                    )
                except Exception:
                    pass
                return {"status": "success", "message": "Partial close recorded"}
            else:
                return {"status": "error", "message": "Failed to record partial close"}

        elif action == "close":
            exit_price = request.get("exit_price")
            pnl = request.get("pnl", 0)
            if not exit_price or exit_price == 0:
                latest = portfolio_manager.get_latest_price(symbol)
                if latest:
                    exit_price = latest.get("bid") or latest.get("ask")
            
            # FlowRsiBot sends `reason`; the other bots send `close_reason`. Both are free text
            # composed by the bot (cTrader's close reason plus the bot's own exit rule).
            close_reason = (request.get("close_reason") or request.get("reason") or "").strip() or None
            success = portfolio_manager.close_position(
                bot_id=bot_id,
                symbol=symbol,
                exit_price=exit_price,
                pnl=pnl,
                account_id=account_id,
                ctrader_id=ctrader_id,
                close_reason=close_reason,
                sl_price=_level_price(request.get("sl_price")),
                tp_price=_level_price(request.get("tp_price")),
            )
            
            if success:
                try:
                    await broadcast_update(account_id=account_id)
                except Exception:
                    pass
                logger.info(f"[PORTFOLIO EVENT] CLOSE | {account_id}/{bot_id} | {symbol} | PnL: ${pnl:.2f}")
                try:
                    await broadcast_event("TRADE_CLOSE", f"CLOSE {symbol} {bot_id} PnL: ${pnl:.2f}", bot_id=bot_id, account_id=str(account_id))
                except Exception:
                    pass
                return {"status": "success", "message": "Position closed"}
            else:
                return {"status": "error", "message": "Failed to close position"}
            return {"status": "error", "message": f"Unknown action: {action}"}
    
    except Exception as e:
        logger.error(f"Portfolio report error: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/portfolio/open-positions")
async def get_open_positions(bot_id: str, account_number: str = "0"):
    """
    Open positions with the stop distance recorded at entry.

    A restarting cBot uses this to rebuild its in-RAM initial-SL map: without it, a
    position already moved to break-even looks like it was opened with a ~0.5 pip
    stop, and every R-based decision (trailing trigger, partial close) runs on a
    fabricated R.
    """
    try:
        registry = get_account_registry()
        account_type = registry.get_account_type(str(account_number)) or "demo"
        account_id = registry.resolve_account_id(str(account_number), account_type)
        if not account_id:
            return {"status": "success", "positions": []}
        rows = portfolio_manager.get_open_positions(
            bot_id=sanitize_bot_id(bot_id), account_id=account_id
        )
        return {"status": "success", "positions": rows}
    except Exception as e:
        logger.error(f"Open positions lookup error: {e}")
        return {"status": "error", "message": str(e), "positions": []}


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
        f"- Macro TDI Bounce: Bull={tms.tdi_bounce_bull}, Bear={tms.tdi_bounce_bear}",
        f"- Macro Exit Signals ({macro_tf}): exit_long={tms.exit_long}, exit_short={tms.exit_short} ({tms.exit_reason or 'none'})",
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
