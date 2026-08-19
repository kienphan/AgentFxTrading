"""
ORB (Opening Range Breakout) Indicator for FX.

Adapted from dnse-kash ORB logic for forex markets.

Concept:
- Opening Range = High/Low of first N candles of a trading session
- Breakout: Price closes above OR high → bullish, below OR low → bearish
- Combined with TMS: ORB provides directional bias, TMS provides entry timing

FX Sessions (UTC):
- London: 07:00 - 16:00
- New York: 12:00 - 21:00
- Tokyo: 00:00 - 09:00
- Sydney: 22:00 - 07:00
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    """Trading session configuration."""
    name: str
    start_hour: int  # UTC hour
    start_minute: int = 0
    or_candles: int = 3  # Number of candles to build OR (e.g., 3×M5 = 15min)


# Default FX sessions
LONDON_SESSION = SessionConfig("london", 7, 0, 3)
NEWYORK_SESSION = SessionConfig("newyork", 12, 0, 3)
TOKYO_SESSION = SessionConfig("tokyo", 0, 0, 3)


@dataclass
class ORBLevels:
    """Opening Range levels."""
    session: str
    date: str
    high: float
    low: float
    mid: float
    width: float
    candle_count: int
    complete: bool


@dataclass
class ORBState:
    """Current ORB state."""
    levels: Optional[ORBLevels] = None
    breakout_direction: Optional[str] = None  # "up", "down", None
    breakout_price: Optional[float] = None
    breakout_candle_index: Optional[int] = None
    bars_since_breakout: int = 0
    price_position: str = "inside"  # "above", "below", "inside"


@dataclass
class ORBConfig:
    """ORB indicator configuration."""
    # Timeframe for ORB calculation (M5 or M15)
    timeframe: str = "M15"
    # Session to use for ORB
    session: str = "london"  # "london", "newyork", "tokyo"
    session_start_hour: int = 7  # UTC
    session_start_minute: int = 0
    or_candles: int = 1  # Number of candles to build OR (M15: 1=15min, M5: 3=15min)
    min_or_width: float = 0.0  # Minimum OR width (0 = no filter)
    buffer_points: float = 0.0  # Buffer added to OR levels
    max_bars_after_breakout: int = 5  # Entry window after breakout
    enable_reversal: bool = False  # Allow fade trades (mean reversion)


class ORBIndicator:
    """
    Opening Range Breakout indicator for FX.

    Builds OR from first N candles of a session, detects breakouts.
    """

    def __init__(self, config: Optional[ORBConfig] = None) -> None:
        self.cfg = config or ORBConfig()
        self._levels: Optional[ORBLevels] = None
        self._breakout_dir: Optional[str] = None
        self._breakout_price: Optional[float] = None
        self._breakout_bar: int = -1
        self._last_date: Optional[str] = None

    def calculate(self, df: pd.DataFrame, timestamps: Optional[pd.Series] = None) -> Dict[str, Any]:
        """
        Calculate ORB levels and detect breakout.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data with columns: open, high, low, close
        timestamps : pd.Series, optional
            Datetime index or series for session detection

        Returns
        -------
        dict
            ORB state with levels and breakout info
        """
        if len(df) < self.cfg.or_candles:
            return {"ok": False, "reason": "insufficient_data"}

        # Get timestamps
        if timestamps is None:
            if isinstance(df.index, pd.DatetimeIndex):
                timestamps = df.index
            else:
                # Generate fake timestamps (assume evenly spaced)
                timestamps = pd.date_range(end=datetime.now(timezone.utc), periods=len(df), freq="5min")
        
        # Convert DatetimeIndex to Series for consistent access
        if isinstance(timestamps, pd.DatetimeIndex):
            timestamps = timestamps.to_series().reset_index(drop=True)

        # Detect session and build OR
        current_date = self._get_session_date(timestamps.iloc[-1])
        
        # Reset if new day
        if self._last_date != current_date:
            self._levels = None
            self._breakout_dir = None
            self._breakout_price = None
            self._breakout_bar = -1
            self._last_date = current_date

        # Build OR if not already built
        if self._levels is None or not self._levels.complete:
            self._levels = self._build_opening_range(df, timestamps, current_date)

        if self._levels is None:
            return {"ok": False, "reason": "no_or_data"}

        # Check for breakout
        breakout_info = self._detect_breakout(df, timestamps)

        # Build result
        result = {
            "ok": True,
            "session": self._levels.session,
            "date": current_date,
            "or_high": round(self._levels.high, 5),
            "or_low": round(self._levels.low, 5),
            "or_mid": round(self._levels.mid, 5),
            "or_width": round(self._levels.width, 5),
            "or_complete": self._levels.complete,
            "or_candle_count": self._levels.candle_count,
        }

        # Add breakout info
        result.update(breakout_info)

        # Add price position
        current_price = df["close"].iloc[-1]
        if current_price > self._levels.high + self.cfg.buffer_points:
            result["price_position"] = "above"
        elif current_price < self._levels.low - self.cfg.buffer_points:
            result["price_position"] = "below"
        else:
            result["price_position"] = "inside"

        return result

    def _get_session_date(self, ts: Any) -> str:
        """Get the trading session date for a timestamp."""
        if isinstance(ts, pd.Timestamp):
            dt = ts.to_pydatetime()
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return str(ts)[:10]

        # If before session start, use previous day
        session_time = time(self.cfg.session_start_hour, self.cfg.session_start_minute)
        if dt.time() < session_time:
            dt = dt - pd.Timedelta(days=1)

        return dt.strftime("%Y-%m-%d")

    def _build_opening_range(
        self,
        df: pd.DataFrame,
        timestamps: pd.Series,
        current_date: str,
    ) -> Optional[ORBLevels]:
        """Build Opening Range from first N candles of session."""
        
        # Find candles in the OR window
        or_bars = []
        for i, ts in enumerate(timestamps):
            if isinstance(ts, pd.Timestamp):
                dt = ts.to_pydatetime()
            elif isinstance(ts, datetime):
                dt = ts
            else:
                continue

            # Check if this candle is in the OR window
            bar_date = self._get_session_date(ts)
            if bar_date != current_date:
                continue

            bar_time = dt.time()
            session_start = time(self.cfg.session_start_hour, self.cfg.session_start_minute)
            
            # Calculate OR end time (approximate based on candle count)
            # Assume 5min candles for now
            or_end_minutes = self.cfg.or_candles * 5
            or_end = time(
                self.cfg.session_start_hour,
                self.cfg.session_start_minute + or_end_minutes
            )

            if session_start <= bar_time < or_end:
                or_bars.append({
                    "high": df["high"].iloc[i],
                    "low": df["low"].iloc[i],
                    "index": i,
                })

            # Stop if we have enough bars
            if len(or_bars) >= self.cfg.or_candles:
                break

        if len(or_bars) < self.cfg.or_candles:
            # Not enough bars yet - build partial OR
            if len(or_bars) == 0:
                return None
            
            # Use what we have
            or_high = max(b["high"] for b in or_bars)
            or_low = min(b["low"] for b in or_bars)
            complete = False
        else:
            or_high = max(b["high"] for b in or_bars)
            or_low = min(b["low"] for b in or_bars)
            complete = True

        or_width = or_high - or_low

        # Check minimum width
        if self.cfg.min_or_width > 0 and or_width < self.cfg.min_or_width:
            logger.warning("OR width %.5f < min %.5f, skipping", or_width, self.cfg.min_or_width)
            return None

        return ORBLevels(
            session=self.cfg.session,
            date=current_date,
            high=or_high,
            low=or_low,
            mid=(or_high + or_low) / 2,
            width=or_width,
            candle_count=len(or_bars),
            complete=complete,
        )

    def _detect_breakout(self, df: pd.DataFrame, timestamps: pd.Series) -> Dict[str, Any]:
        """Detect ORB breakout."""
        if self._levels is None:
            return {"breakout": False}

        current_price = df["close"].iloc[-1]
        current_index = len(df) - 1

        # Check for breakout
        if self._breakout_dir is None:
            # Look for new breakout
            buffer = self.cfg.buffer_points
            
            if current_price > self._levels.high + buffer:
                self._breakout_dir = "up"
                self._breakout_price = current_price
                self._breakout_bar = current_index
            elif current_price < self._levels.low - buffer:
                self._breakout_dir = "down"
                self._breakout_price = current_price
                self._breakout_bar = current_index

        # Calculate bars since breakout
        bars_since = 0
        if self._breakout_bar >= 0:
            bars_since = current_index - self._breakout_bar

        # Check if still in entry window
        in_window = bars_since <= self.cfg.max_bars_after_breakout

        result = {
            "breakout": self._breakout_dir is not None,
            "breakout_direction": self._breakout_dir,
            "breakout_price": round(self._breakout_price, 5) if self._breakout_price else None,
            "bars_since_breakout": bars_since,
            "in_entry_window": in_window,
        }

        return result


def calculate_orb(
    df: pd.DataFrame,
    timestamps: Optional[pd.Series] = None,
    config: Optional[ORBConfig] = None,
) -> Dict[str, Any]:
    """
    Convenience function to calculate ORB.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data
    timestamps : pd.Series, optional
        Datetime index for session detection
    config : ORBConfig, optional
        ORB parameters

    Returns
    -------
    dict
        ORB state
    """
    indicator = ORBIndicator(config)
    return indicator.calculate(df, timestamps)
