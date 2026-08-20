"""
Application configuration using Pydantic Settings.
Loads settings from environment variables and .env file.
"""

from enum import Enum
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # cTrader Remote MCP
    CTRADER_MCP_URL: str = Field(default="https://mcp.ctrader.com/trading/mcp", description="cTrader Remote MCP URL")
    CTRADER_MCP_TOKEN: str = Field(default="", description="cTrader Remote MCP Bearer token")

    # Trading (multi-symbol support)
    SYMBOLS: str = Field(default="XAUUSD", description="Comma-separated symbols: XAUUSD,EURUSD,GBPUSD")
    TIMEFRAME: str = Field(default="H1", description="M5, M15, H1, H4, D1")
    LOOKBACK_BARS: int = Field(default=100, ge=20, le=500)

    @property
    def symbol_list(self) -> list[str]:
        """Get list of symbols from comma-separated string."""
        return [s.strip().upper() for s in self.SYMBOLS.split(",") if s.strip()]

    # TMS Indicator Parameters (from TMSBot.cs)
    TMS_RSI_PERIOD: int = Field(default=6, ge=1, description="TDI Green line (RSI) period")
    TMS_RED_PERIOD: int = Field(default=6, ge=1, description="TDI Red line (signal) period")
    TMS_RED_METHOD: str = Field(default="EMA", description="SMA or EMA for Red line")
    TMS_STOCH_K: int = Field(default=6, ge=1, description="Stochastic %K period")
    TMS_STOCH_D: int = Field(default=6, ge=1, description="Stochastic %D period")
    TMS_STOCH_SLOWING: int = Field(default=4, ge=1, description="Stochastic slowing")
    TMS_STOCH_MODE: str = Field(default="KAboveD", description="KAboveD, KRising, or KCross")
    TMS_MAX_BARS_AFTER_CROSS: int = Field(default=5, ge=1, description="Max bars to enter after TDI cross")
    TMS_MIN_ANGLE_DELTA: float = Field(default=0.0, ge=0.0, description="Min angle for TDI Green (0=disabled)")
    TMS_FLAT_THRESHOLD: float = Field(default=0.01, ge=0.0, description="TDI Green flat threshold for exit")

    # ORB (Opening Range Breakout)
    ORB_ENABLED: bool = Field(default=True, description="Enable ORB confirmation")
    ORB_TIMEFRAME: str = Field(default="M15", description="ORB timeframe: M5 or M15")
    ORB_DEFAULT_SESSION: str = Field(default="london", description="Default session: london, newyork, tokyo")
    ORB_DEFAULT_HOUR: int = Field(default=7, ge=0, le=23, description="Default session start hour (UTC)")
    ORB_DEFAULT_MINUTE: int = Field(default=0, ge=0, le=59, description="Default session start minute")
    ORB_CANDLES: int = Field(default=1, ge=1, le=10, description="Number of candles to build OR (M15: 1=15min, M5: 3=15min)")
    ORB_MIN_WIDTH: float = Field(default=0.0, ge=0.0, description="Minimum OR width (0 = no filter)")
    ORB_BUFFER: float = Field(default=0.0, ge=0.0, description="Buffer added to OR levels")
    ORB_MAX_BARS: int = Field(default=5, ge=1, le=20, description="Entry window after breakout")

    # Per-symbol ORB sessions (comma-separated, format: SYMBOL:session:hour)
    # Example: "USDJPY:tokyo:0,USDCAD:newyork:12"
    ORB_SYMBOL_SESSIONS: str = Field(default="", description="Per-symbol ORB sessions")

    # Trading sessions (UTC hours) - only trade during these hours
    TRADING_SESSIONS: str = Field(
        default="london,newyork",
        description="Comma-separated sessions to trade: london,newyork,tokyo,sydney"
    )

    # Session hours (UTC)
    LONDON_START: int = Field(default=7, ge=0, le=23, description="London session start (UTC)")
    LONDON_END: int = Field(default=16, ge=0, le=23, description="London session end (UTC)")
    NEWYORK_START: int = Field(default=12, ge=0, le=23, description="New York session start (UTC)")
    NEWYORK_END: int = Field(default=21, ge=0, le=23, description="New York session end (UTC)")

    # Default session start hours for common sessions
    SESSION_HOURS: dict = {
        "london": 7,
        "newyork": 12,
        "tokyo": 0,
        "sydney": 22,
    }

    def get_orb_session_for_symbol(self, symbol: str) -> tuple[str, int, int]:
        """Get ORB session (name, hour, minute) for a specific symbol."""
        # Check per-symbol config first
        if self.ORB_SYMBOL_SESSIONS:
            for entry in self.ORB_SYMBOL_SESSIONS.split(","):
                entry = entry.strip()
                if ":" in entry:
                    parts = entry.split(":")
                    if len(parts) >= 2:
                        sym = parts[0].strip().upper()
                        session = parts[1].strip().lower()
                        hour = int(parts[2]) if len(parts) > 2 else self.SESSION_HOURS.get(session, 7)
                        if sym == symbol.upper():
                            return (session, hour, 0)

        # Return default
        return (self.ORB_DEFAULT_SESSION, self.ORB_DEFAULT_HOUR, self.ORB_DEFAULT_MINUTE)

    def is_trading_session(self, utc_hour: int) -> tuple[bool, str]:
        """
        Check if the given UTC hour is within active trading sessions.

        Returns
        -------
        (is_active, session_name)
            is_active: True if within trading session
            session_name: Name of active session or "off-hours"
        """
        sessions = [s.strip().lower() for s in self.TRADING_SESSIONS.split(",")]

        for session in sessions:
            if session == "london":
                if self.LONDON_START <= utc_hour < self.LONDON_END:
                    return (True, "london")
            elif session == "newyork":
                if self.NEWYORK_START <= utc_hour < self.NEWYORK_END:
                    return (True, "newyork")
            elif session == "tokyo":
                # Tokyo: 00:00-09:00 UTC
                if 0 <= utc_hour < 9:
                    return (True, "tokyo")
            elif session == "sydney":
                # Sydney: 22:00-07:00 UTC (wraps midnight)
                if utc_hour >= 22 or utc_hour < 7:
                    return (True, "sydney")

        return (False, "off-hours")

    # Risk Management
    RISK_PER_TRADE_PCT: float = Field(default=1.0, ge=0.1, le=5.0, description="Risk % per trade")
    MAX_DAILY_LOSS_PCT: float = Field(default=3.0, ge=0.5, le=10.0, description="Max daily loss %")
    MAX_DRAWDOWN_PCT: float = Field(default=10.0, ge=1.0, le=50.0, description="Max drawdown % from peak")
    MAX_POSITIONS: int = Field(default=3, ge=1, le=10, description="Max concurrent positions")
    MAX_POSITIONS_PER_SYMBOL: int = Field(default=1, ge=1, le=5, description="Max positions per symbol")
    MIN_RR_RATIO: float = Field(default=1.5, ge=1.0, le=5.0, description="Minimum risk/reward ratio")

    # Volatility & spread filters
    MAX_SPREAD_PIPS: float = Field(default=5.0, ge=0.5, le=50.0, description="Max spread in pips")
    MIN_SL_ATR_MULTIPLE: float = Field(default=1.5, ge=0.5, le=5.0, description="SL must be >= Nx ATR (avoid liquidity sweeps)")
    MAX_ATR_PERCENTILE: float = Field(default=90.0, ge=50.0, le=99.0, description="Skip if ATR > Nth percentile")

    # News filter
    NEWS_FILTER_ENABLED: bool = Field(default=True, description="Skip trading around news events")
    NEWS_BUFFER_MINUTES: int = Field(default=30, ge=5, le=120, description="Minutes before/after news to skip")

    # Symbol-specific risk overrides
    # Format: "SYMBOL:risk_pct:max_sl_pips" e.g., "XAUUSD:0.5:50"
    SYMBOL_RISK_OVERRIDES: str = Field(default="", description="Per-symbol risk config")

    # Portfolio Risk (multi-pair)
    MAX_PORTFOLIO_HEAT_PCT: float = Field(default=5.0, ge=1.0, le=20.0, description="Max total risk across all positions %")
    MAX_MARGIN_USAGE_PCT: float = Field(default=50.0, ge=10.0, le=90.0, description="Max margin usage %")
    MAX_SAME_CURRENCY_EXPOSURE: int = Field(default=2, ge=1, le=5, description="Max positions with same currency")
    MAX_CORRELATED_POSITIONS: int = Field(default=2, ge=1, le=5, description="Max highly correlated positions")
    CORRELATION_THRESHOLD: float = Field(default=0.7, ge=0.5, le=1.0, description="Correlation threshold for warnings")

    # LLM
    LLM_PROVIDER: LLMProvider = Field(default=LLMProvider.QWEN)
    LLM_API_KEY: str = Field(default="")
    LLM_MODEL: str = Field(default="qwen-max")
    LLM_BASE_URL: str = Field(default="")
    LLM_MAX_TOKENS: int = Field(default=2048, ge=1, le=32000)
    LLM_TEMPERATURE: float = Field(default=0.1, ge=0.0, le=2.0)
    FALLBACK_TO_TMS_ON_AI_ERROR: bool = Field(default=True, description="Fallback to pure TMS strategy when LLM call fails or hits quota")

    # Cycle
    CYCLE_MINUTES: int = Field(default=5, ge=1, le=1440, description="Minutes between cycles (max 1440 = 24h)")

    # Logging
    LOG_LEVEL: str = Field(default="INFO")

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper

    def safe_dict(self) -> dict:
        sensitive = {"LLM_API_KEY", "CTRADER_MCP_URL"}
        return {k: ("***REDACTED***" if k in sensitive else v) for k, v in self.model_dump().items()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
