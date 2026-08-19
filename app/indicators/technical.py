"""
Technical indicators calculation using pandas-ta.

Provides EMA, RSI, MACD, ATR, Bollinger Bands, Stochastic, ADX.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from typing import Any, Dict


def calculate_all(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculate all technical indicators for the given OHLCV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with columns: open, high, low, close, volume
        Index should be datetime or integer.

    Returns
    -------
    dict
        Dictionary with indicator values (latest) and series.
    """
    if df.empty or len(df) < 20:
        return {}

    result: Dict[str, Any] = {}

    # --- Trend ---
    # EMA
    ema_9 = ta.ema(df["close"], length=9)
    ema_21 = ta.ema(df["close"], length=21)
    ema_50 = ta.ema(df["close"], length=50)
    result["ema_9"] = round(ema_9.iloc[-1], 4) if len(ema_9) > 0 else None
    result["ema_21"] = round(ema_21.iloc[-1], 4) if len(ema_21) > 0 else None
    result["ema_50"] = round(ema_50.iloc[-1], 4) if len(ema_50) > 0 else None

    # Trend direction
    if result["ema_9"] and result["ema_21"] and result["ema_50"]:
        if result["ema_9"] > result["ema_21"] > result["ema_50"]:
            result["trend"] = "STRONG_BULLISH"
        elif result["ema_9"] > result["ema_21"]:
            result["trend"] = "BULLISH"
        elif result["ema_9"] < result["ema_21"] < result["ema_50"]:
            result["trend"] = "STRONG_BEARISH"
        elif result["ema_9"] < result["ema_21"]:
            result["trend"] = "BEARISH"
        else:
            result["trend"] = "NEUTRAL"

    # ADX
    adx = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx is not None and len(adx) > 0:
        adx_col = [c for c in adx.columns if "ADX" in c and "_" not in c]
        if adx_col:
            result["adx"] = round(adx[adx_col[0]].iloc[-1], 2)
            result["trend_strength"] = "STRONG" if result["adx"] > 25 else "WEAK"

    # --- Momentum ---
    # RSI
    rsi = ta.rsi(df["close"], length=14)
    if rsi is not None and len(rsi) > 0:
        result["rsi"] = round(rsi.iloc[-1], 2)
        if result["rsi"] > 70:
            result["rsi_signal"] = "OVERBOUGHT"
        elif result["rsi"] < 30:
            result["rsi_signal"] = "OVERSOLD"
        else:
            result["rsi_signal"] = "NEUTRAL"

    # MACD
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None and len(macd) > 0:
        macd_col = [c for c in macd.columns if "MACD_" in c and "h" not in c.lower() and "s" not in c.lower()]
        hist_col = [c for c in macd.columns if "h" in c.lower()]
        if macd_col:
            result["macd"] = round(macd[macd_col[0]].iloc[-1], 4)
        if hist_col:
            result["macd_histogram"] = round(macd[hist_col[0]].iloc[-1], 4)
            if result["macd_histogram"] > 0:
                result["macd_signal"] = "BULLISH"
            else:
                result["macd_signal"] = "BEARISH"

    # Stochastic
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch is not None and len(stoch) > 0:
        k_col = [c for c in stoch.columns if "k" in c.lower() or "K" in c]
        d_col = [c for c in stoch.columns if "d" in c.lower() or "D" in c]
        if k_col:
            result["stoch_k"] = round(stoch[k_col[0]].iloc[-1], 2)
        if d_col:
            result["stoch_d"] = round(stoch[d_col[0]].iloc[-1], 2)
        if result.get("stoch_k") is not None and result.get("stoch_d") is not None:
            if result["stoch_k"] < 20 and result["stoch_d"] < 20:
                result["stoch_signal"] = "OVERSOLD"
            elif result["stoch_k"] > 80 and result["stoch_d"] > 80:
                result["stoch_signal"] = "OVERBOUGHT"
            else:
                result["stoch_signal"] = "NEUTRAL"

    # --- Volatility ---
    # ATR
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr is not None and len(atr) > 0:
        result["atr"] = round(atr.iloc[-1], 4)
        result["atr_percent"] = round((result["atr"] / df["close"].iloc[-1]) * 100, 2)

    # Bollinger Bands
    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is not None and len(bbands) > 0:
        upper_col = [c for c in bbands.columns if "u" in c.lower() or "upper" in c.lower()]
        lower_col = [c for c in bbands.columns if "l" in c.lower() or "lower" in c.lower()]
        mid_col = [c for c in bbands.columns if "m" in c.lower() or "mid" in c.lower()]
        if upper_col:
            result["bb_upper"] = round(bbands[upper_col[0]].iloc[-1], 4)
        if lower_col:
            result["bb_lower"] = round(bbands[lower_col[0]].iloc[-1], 4)
        if mid_col:
            result["bb_middle"] = round(bbands[mid_col[0]].iloc[-1], 4)

        # Position relative to bands
        price = df["close"].iloc[-1]
        if result.get("bb_upper") and result.get("bb_lower"):
            bb_width = result["bb_upper"] - result["bb_lower"]
            if bb_width > 0:
                result["bb_position"] = round((price - result["bb_lower"]) / bb_width, 2)

    # --- Price Action ---
    result["price"] = round(df["close"].iloc[-1], 4)
    result["high"] = round(df["high"].iloc[-1], 4)
    result["low"] = round(df["low"].iloc[-1], 4)

    # Recent candles pattern
    if len(df) >= 3:
        last_3 = df.tail(3)
        result["last_3_candles"] = {
            "closes": [round(c, 4) for c in last_3["close"].tolist()],
            "volumes": last_3["volume"].tolist() if "volume" in last_3.columns else [],
        }

    return result


def calculate_ema(closes: pd.Series, period: int) -> pd.Series:
    """Calculate EMA for a series of closes."""
    return ta.ema(closes, length=period)


def calculate_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI for a series of closes."""
    return ta.rsi(closes, length=period)


def calculate_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Calculate ATR."""
    return ta.atr(highs, lows, closes, length=period)
