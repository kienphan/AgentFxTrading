"""
TMS (Traders Dynamic Index) Indicator Implementation.

Based on TMSBot.cs and tms-strategy.md.

Components:
- Heiken Ashi candles
- TDI: Green line (RSI Wilder) + Red line (signal)
- Stochastic on HA data
- Signal detection: cross, angle, exit conditions
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TMSConfig:
    """TMS indicator configuration (from TMSBot.cs parameters)."""
    # TDI
    rsi_period: int = 6
    red_period: int = 6
    red_method: str = "EMA"  # "SMA" or "EMA"

    # Stochastic
    stoch_k_period: int = 6
    stoch_d_period: int = 6
    stoch_slowing: int = 4
    stoch_confirm_mode: str = "KAboveD"  # "KAboveD", "KRising", "KCross"

    # Entry
    max_bars_after_cross: int = 5
    min_angle_delta: float = 0.0  # 0 = disabled
    enable_bounce: bool = False

    # Exit
    flat_threshold: float = 0.01
    checkmark_threshold: float = 0.0

    # SL
    sl_lookback: int = 7


@dataclass
class HeikenAshi:
    """Heiken Ashi candle data."""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def is_green(self, i: int) -> bool:
        """Check if HA candle at index i is green (close > open)."""
        return self.close[i] > self.open[i]


@dataclass
class TDI:
    """Traders Dynamic Index data."""
    green: np.ndarray  # RSI line
    red: np.ndarray    # Signal line


@dataclass
class Stochastic:
    """Stochastic oscillator data."""
    k: np.ndarray  # %K line
    d: np.ndarray  # %D line


@dataclass
class TMSSignal:
    """TMS trading signal."""
    action: str  # "BUY", "SELL", "HOLD", "EXIT_LONG", "EXIT_SHORT"
    confidence: float  # 0.0 - 1.0
    reason: str
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None

    # Signal details
    tdi_cross: bool = False
    bars_since_cross: int = 0
    ha_color_change: bool = False
    stoch_confirm: bool = False
    angle_ok: bool = False
    exit_reason: str = ""  # "flat", "hook", "checkmark", "timeout"


class TMSIndicator:
    """
    TMS Indicator calculator.

    Replicates TMSBot.cs logic:
    - Heiken Ashi candles
    - TDI (RSI Wilder + Red signal line)
    - Stochastic on HA data
    - Signal detection
    """

    def __init__(self, config: Optional[TMSConfig] = None) -> None:
        self.cfg = config or TMSConfig()

        # Internal state
        self._ha: Optional[HeikenAshi] = None
        self._tdi: Optional[TDI] = None
        self._stoch: Optional[Stochastic] = None

        # RSI state (Wilder smoothing)
        self._avg_gain = 0.0
        self._avg_loss = 0.0

        # Signal tracking
        self._last_cross_bar = -1
        self._last_cross_dir = 0  # 1 = up, -1 = down

    def calculate(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate all TMS indicators from OHLCV DataFrame.

        Parameters
        ----------
        df : pd.DataFrame
            Columns: open, high, low, close, volume

        Returns
        -------
        dict
            Complete TMS state with signals
        """
        n = len(df)
        if n < 20:
            return {"ok": False, "reason": "insufficient_data", "bars": n}

        # Extract arrays
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        # 1. Calculate Heiken Ashi
        self._ha = self._calc_heikin_ashi(opens, highs, lows, closes)

        # 2. Calculate TDI on HA closes
        self._tdi = self._calc_tdi(self._ha.close)

        # 3. Calculate Stochastic on HA data
        self._stoch = self._calc_stochastic(self._ha)

        # 4. Build result
        i = n - 1  # current bar
        result = self._build_result(i, closes)

        return result

    def _calc_heikin_ashi(
        self,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
    ) -> HeikenAshi:
        """Calculate Heiken Ashi candles."""
        n = len(opens)
        ha_open = np.zeros(n)
        ha_high = np.zeros(n)
        ha_low = np.zeros(n)
        ha_close = np.zeros(n)

        # First candle
        ha_open[0] = (opens[0] + closes[0]) / 2
        ha_close[0] = (opens[0] + highs[0] + lows[0] + closes[0]) / 4
        ha_high[0] = max(highs[0], ha_open[0], ha_close[0])
        ha_low[0] = min(lows[0], ha_open[0], ha_close[0])

        # Subsequent candles
        for i in range(1, n):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
            ha_close[i] = (opens[i] + highs[i] + lows[i] + closes[i]) / 4
            ha_high[i] = max(highs[i], ha_open[i], ha_close[i])
            ha_low[i] = min(lows[i], ha_open[i], ha_close[i])

        return HeikenAshi(open=ha_open, high=ha_high, low=ha_low, close=ha_close)

    def _calc_tdi(self, ha_closes: np.ndarray) -> TDI:
        """
        Calculate TDI: Green (RSI Wilder) + Red (signal line).

        RSI uses Wilder smoothing (not standard RSI).
        Red = SMA/EMA of RSI.
        """
        n = len(ha_closes)
        green = np.full(n, np.nan)
        red = np.full(n, np.nan)

        period = self.cfg.rsi_period
        red_period = self.cfg.red_period

        # Reset RSI state
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        gain_sum = 0.0
        loss_sum = 0.0

        for i in range(1, n):
            delta = ha_closes[i] - ha_closes[i - 1]
            gain = max(delta, 0)
            loss = max(-delta, 0)

            if i <= period:
                # Accumulate for initial SMA
                gain_sum += gain
                loss_sum += loss
                if i == period:
                    self._avg_gain = gain_sum / period
                    self._avg_loss = loss_sum / period
                    green[i] = self._rsi_from_avg()
            else:
                # Wilder smoothing
                self._avg_gain = (self._avg_gain * (period - 1) + gain) / period
                self._avg_loss = (self._avg_loss * (period - 1) + loss) / period
                green[i] = self._rsi_from_avg()

        # Calculate Red line (SMA/EMA of Green)
        warmup = period + red_period - 1
        for i in range(warmup, n):
            if np.isnan(green[i]):
                continue
            if self.cfg.red_method == "SMA":
                red[i] = np.nanmean(green[i - red_period + 1:i + 1])
            else:  # EMA
                alpha = 2.0 / (red_period + 1)
                if i == warmup:
                    red[i] = np.nanmean(green[i - red_period + 1:i + 1])
                else:
                    red[i] = red[i - 1] + alpha * (green[i] - red[i - 1])

        return TDI(green=green, red=red)

    def _rsi_from_avg(self) -> float:
        """Calculate RSI from average gain/loss."""
        if self._avg_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)

    def _calc_stochastic(self, ha: HeikenAshi) -> Stochastic:
        """Calculate Stochastic on Heiken Ashi data."""
        n = len(ha.close)
        raw_k = np.zeros(n)
        k = np.full(n, np.nan)
        d = np.full(n, np.nan)

        k_period = self.cfg.stoch_k_period
        d_period = self.cfg.stoch_d_period
        slowing = self.cfg.stoch_slowing

        # Raw %K
        for i in range(n):
            start = max(0, i - k_period + 1)
            highest = np.max(ha.high[start:i + 1])
            lowest = np.min(ha.low[start:i + 1])
            if highest > lowest:
                raw_k[i] = 100.0 * (ha.close[i] - lowest) / (highest - lowest)
            else:
                raw_k[i] = 50.0

        # %K = SMA(raw_k, slowing)
        for i in range(slowing - 1, n):
            k[i] = np.mean(raw_k[i - slowing + 1:i + 1])

        # %D = SMA(%K, d_period)
        d_warmup = slowing + d_period - 2
        for i in range(d_warmup, n):
            if not np.isnan(k[i]):
                d[i] = np.nanmean(k[i - d_period + 1:i + 1])

        return Stochastic(k=k, d=d)

    def _build_result(self, i: int, original_closes: np.ndarray) -> Dict[str, Any]:
        """Build complete TMS result with signals."""
        if self._ha is None or self._tdi is None or self._stoch is None:
            return {"ok": False, "reason": "not_calculated"}

        n = len(original_closes)
        if i < 2:
            return {"ok": False, "reason": "insufficient_bars"}

        # Current values
        g = self._tdi.green[i]
        g1 = self._tdi.green[i - 1]
        g2 = self._tdi.green[i - 2] if i >= 2 else g1
        r = self._tdi.red[i]
        r1 = self._tdi.red[i - 1]

        k = self._stoch.k[i]
        d = self._stoch.d[i]
        k_prev = self._stoch.k[i - 1]
        d_prev = self._stoch.d[i - 1]

        # Handle NaN
        if np.isnan(g) or np.isnan(r):
            return {"ok": False, "reason": "tdi_not_ready", "bars": n}

        # HA color
        ha_green = self._ha.is_green(i)
        ha_green_p1 = self._ha.is_green(i - 1)
        ha_green_p2 = self._ha.is_green(i - 2) if i >= 2 else ha_green_p1
        ha_turned_green = (ha_green and not ha_green_p1) or (ha_green_p1 and not ha_green_p2)
        ha_turned_red = (not ha_green and ha_green_p1) or (not ha_green_p1 and ha_green_p2)

        # TDI cross detection
        cross_up = g1 <= r1 and g > r
        cross_dn = g1 >= r1 and g < r

        if cross_up:
            self._last_cross_bar = i
            self._last_cross_dir = 1
        elif cross_dn:
            self._last_cross_bar = i
            self._last_cross_dir = -1

        bars_since_cross = i - self._last_cross_bar if self._last_cross_bar >= 0 else 999

        # Stochastic confirmation
        stoch_bull, stoch_bear = self._stoch_confirm(k, d, k_prev, d_prev)

        # Angle filter
        angle_ok_long = self._is_good_angle(g, g1, g2, is_long=True)
        angle_ok_short = self._is_good_angle(g, g1, g2, is_long=False)

        # Entry signals
        within_window = 1 <= bars_since_cross <= self.cfg.max_bars_after_cross

        long_entry = (
            self._last_cross_dir == 1
            and within_window
            and ha_turned_green
            and stoch_bull
            and angle_ok_long
        )

        short_entry = (
            self._last_cross_dir == -1
            and within_window
            and ha_turned_red
            and stoch_bear
            and angle_ok_short
        )

        # Exit signals
        exit_long, exit_short, exit_reason = self._check_exit(g, g1, g2)

        # TDI levels
        tdi_level = "neutral"
        if g < 32:
            tdi_level = "oversold"
        elif g > 68:
            tdi_level = "overbought"

        # Trend from HA
        ha_trend = "bullish" if ha_green else "bearish"

        # Build result (convert numpy bools to Python bools for JSON serialization)
        result = {
            "ok": True,
            "bars": int(n),
            "price": float(original_closes[i]),

            # Heiken Ashi
            "ha": {
                "open": round(float(self._ha.open[i]), 5),
                "high": round(float(self._ha.high[i]), 5),
                "low": round(float(self._ha.low[i]), 5),
                "close": round(float(self._ha.close[i]), 5),
                "color": "green" if ha_green else "red",
                "trend": ha_trend,
                "turned_green": bool(ha_turned_green),
                "turned_red": bool(ha_turned_red),
            },

            # TDI
            "tdi": {
                "green": round(float(g), 2),
                "red": round(float(r), 2) if not np.isnan(r) else None,
                "green_prev": round(float(g1), 2),
                "level": tdi_level,
                "cross_up": bool(cross_up),
                "cross_down": bool(cross_dn),
                "bars_since_cross": int(bars_since_cross),
                "cross_direction": "up" if self._last_cross_dir == 1 else "down" if self._last_cross_dir == -1 else None,
            },

            # Stochastic
            "stoch": {
                "k": round(float(k), 2) if not np.isnan(k) else None,
                "d": round(float(d), 2) if not np.isnan(d) else None,
                "bullish": bool(stoch_bull),
                "bearish": bool(stoch_bear),
            },

            # Signals
            "signal": {
                "long_entry": bool(long_entry),
                "short_entry": bool(short_entry),
                "exit_long": bool(exit_long),
                "exit_short": bool(exit_short),
                "exit_reason": exit_reason,
                "angle_ok_long": bool(angle_ok_long),
                "angle_ok_short": bool(angle_ok_short),
            },

            # Action recommendation
            "action": self._recommend_action(long_entry, short_entry, exit_long, exit_short, g, r),

            # TMS BIAS (for ORB confirmation strategy)
            # Bias = last cross direction, locked until next cross
            "bias": "BULLISH" if self._last_cross_dir == 1 else "BEARISH" if self._last_cross_dir == -1 else "NEUTRAL",
            "bias_since_bar": int(self._last_cross_bar) if self._last_cross_bar >= 0 else None,
        }

        return result

    def _stoch_confirm(self, k: float, d: float, k_prev: float, d_prev: float) -> Tuple[bool, bool]:
        """Check stochastic confirmation based on mode."""
        mode = self.cfg.stoch_confirm_mode

        if mode == "KRising":
            return k > k_prev, k < k_prev
        elif mode == "KCross":
            return k_prev <= d_prev and k > d, k_prev >= d_prev and k < d
        else:  # KAboveD
            return k > d, k < d

    def _is_good_angle(self, g: float, g1: float, g2: float, is_long: bool) -> bool:
        """Check if TDI Green line has sufficient angle."""
        if self.cfg.min_angle_delta <= 0:
            return True

        if is_long:
            rise = g - g2
            return rise >= self.cfg.min_angle_delta
        else:
            fall = g2 - g
            return fall >= self.cfg.min_angle_delta

    def _check_exit(self, g: float, g1: float, g2: float) -> Tuple[bool, bool, str]:
        """Check exit conditions based on TDI Green line behavior."""
        # Flat: Green going horizontal
        flat = abs(g - g1) < self.cfg.flat_threshold

        # Hook: Green reversing direction
        hook_up = g1 < g2 and g > g1  # was going down, now up
        hook_dn = g1 > g2 and g < g1  # was going up, now down

        # Checkmark: strong hook (V shape)
        check_up = hook_up and (g - g2) >= self.cfg.checkmark_threshold
        check_dn = hook_dn and (g2 - g) >= self.cfg.checkmark_threshold

        # Exit long: flat or hook down or checkmark down
        exit_long = flat or hook_dn or check_dn
        exit_short = flat or hook_up or check_up

        reason = ""
        if flat:
            reason = "flat"
        elif check_dn or check_up:
            reason = "checkmark"
        elif hook_dn or hook_up:
            reason = "hook"

        return exit_long, exit_short, reason

    def _recommend_action(
        self,
        long_entry: bool,
        short_entry: bool,
        exit_long: bool,
        exit_short: bool,
        g: float,
        r: float,
    ) -> str:
        """Recommend trading action based on signals."""
        if exit_long:
            return "EXIT_LONG"
        if exit_short:
            return "EXIT_SHORT"
        if long_entry:
            return "BUY"
        if short_entry:
            return "SELL"

        # No clear signal
        if g > r and g > 50:
            return "HOLD_BULLISH"
        elif g < r and g < 50:
            return "HOLD_BEARISH"
        else:
            return "WAIT"


def calculate_tms(df: pd.DataFrame, config: Optional[TMSConfig] = None) -> Dict[str, Any]:
    """
    Convenience function to calculate TMS indicators.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data
    config : TMSConfig, optional
        TMS parameters

    Returns
    -------
    dict
        Complete TMS state
    """
    indicator = TMSIndicator(config)
    return indicator.calculate(df)
