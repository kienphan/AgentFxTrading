"""
Market Anomaly Detector — AI-based news detection.

Detects news events by observing market anomalies:
- Volatility spikes (ATR sudden increase)
- Spread widening
- Unusual price movement
- Volume spikes

When anomalies detected → likely news event → avoid trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AnomalyResult:
    """Result of anomaly detection."""
    detected: bool
    anomaly_type: str = ""  # "volatility_spike", "spread_wide", "price_spike", "volume_spike"
    severity: str = "low"  # "low", "medium", "high"
    details: str = ""
    confidence: float = 0.0  # 0-1, how confident we are this is news

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detected": self.detected,
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "details": self.details,
            "confidence": self.confidence,
        }


class MarketAnomalyDetector:
    """
    Detects market anomalies that likely indicate news events.

    Uses statistical methods to detect:
    - Volatility spikes (ATR > 2σ from mean)
    - Spread widening (current spread > 2x average)
    - Price spikes (candle body > 3x ATR)
    - Volume spikes (volume > 3x average)
    """

    def __init__(
        self,
        volatility_threshold: float = 2.5,  # ATR > mean + 2.5*std (less sensitive)
        spread_multiplier: float = 2.5,  # spread > 2.5x average
        price_spike_atr_multiple: float = 3.5,  # candle > 3.5x ATR
        volume_multiplier: float = 4.0,  # volume > 4x average
        lookback_period: int = 50,  # bars to calculate baseline
    ) -> None:
        self.volatility_threshold = volatility_threshold
        self.spread_multiplier = spread_multiplier
        self.price_spike_atr_multiple = price_spike_atr_multiple
        self.volume_multiplier = volume_multiplier
        self.lookback_period = lookback_period

    def detect(
        self,
        df: pd.DataFrame,
        current_spread_pips: Optional[float] = None,
        avg_spread_pips: Optional[float] = None,
    ) -> AnomalyResult:
        """
        Detect market anomalies.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data with columns: open, high, low, close, volume
        current_spread_pips : float, optional
            Current spread in pips
        avg_spread_pips : float, optional
            Average spread in pips (for comparison)

        Returns
        -------
        AnomalyResult
        """
        if len(df) < self.lookback_period:
            return AnomalyResult(detected=False)

        # Calculate baseline statistics
        recent = df.tail(self.lookback_period)

        # 1. Volatility spike detection (ATR)
        atr_anomaly = self._detect_volatility_spike(recent)
        if atr_anomaly.detected:
            return atr_anomaly

        # 2. Spread widening detection
        if current_spread_pips is not None and avg_spread_pips is not None:
            spread_anomaly = self._detect_spread_widening(current_spread_pips, avg_spread_pips)
            if spread_anomaly.detected:
                return spread_anomaly

        # 3. Price spike detection (large candle)
        price_anomaly = self._detect_price_spike(recent)
        if price_anomaly.detected:
            return price_anomaly

        # 4. Volume spike detection
        volume_anomaly = self._detect_volume_spike(recent)
        if volume_anomaly.detected:
            return volume_anomaly

        # No anomaly detected
        return AnomalyResult(detected=False)

    def _detect_volatility_spike(self, df: pd.DataFrame) -> AnomalyResult:
        """Detect if current ATR is significantly higher than recent average."""
        # Calculate true range for each candle
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]  # First candle

        # Calculate ATR (14-period)
        atr_period = 14
        if len(tr) < atr_period:
            return AnomalyResult(detected=False)

        atr = pd.Series(tr).rolling(atr_period).mean().values

        # Current ATR vs recent ATR baseline
        current_atr = atr[-1]
        baseline_atr = atr[-self.lookback_period:-atr_period]

        if len(baseline_atr) == 0 or np.isnan(current_atr):
            return AnomalyResult(detected=False)

        mean_atr = np.mean(baseline_atr)
        std_atr = np.std(baseline_atr)

        if std_atr == 0:
            return AnomalyResult(detected=False)

        # Check if current ATR is > mean + threshold * std
        z_score = (current_atr - mean_atr) / std_atr

        if z_score > self.volatility_threshold:
            severity = "high" if z_score > 3.0 else "medium"
            confidence = min(z_score / 4.0, 1.0)

            return AnomalyResult(
                detected=True,
                anomaly_type="volatility_spike",
                severity=severity,
                details=f"ATR {current_atr:.2f} is {z_score:.1f}σ above mean {mean_atr:.2f}",
                confidence=confidence,
            )

        return AnomalyResult(detected=False)

    def _detect_spread_widening(
        self,
        current_spread: float,
        avg_spread: float,
    ) -> AnomalyResult:
        """Detect if spread is significantly wider than average."""
        if avg_spread <= 0:
            return AnomalyResult(detected=False)

        ratio = current_spread / avg_spread

        if ratio > self.spread_multiplier:
            severity = "high" if ratio > 3.0 else "medium"
            confidence = min((ratio - 1) / 3.0, 1.0)

            return AnomalyResult(
                detected=True,
                anomaly_type="spread_wide",
                severity=severity,
                details=f"Spread {current_spread:.1f} pips is {ratio:.1f}x average {avg_spread:.1f}",
                confidence=confidence,
            )

        return AnomalyResult(detected=False)

    def _detect_price_spike(self, df: pd.DataFrame) -> AnomalyResult:
        """Detect if last candle is unusually large (price spike)."""
        if len(df) < 20:
            return AnomalyResult(detected=False)

        # Calculate ATR for baseline
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]

        atr = np.mean(tr[-20:])  # 20-period average

        if atr <= 0:
            return AnomalyResult(detected=False)

        # Last candle body size
        last_body = abs(close[-1] - df["open"].iloc[-1])
        body_ratio = last_body / atr

        if body_ratio > self.price_spike_atr_multiple:
            severity = "high" if body_ratio > 5.0 else "medium"
            confidence = min((body_ratio - 1) / 5.0, 1.0)

            return AnomalyResult(
                detected=True,
                anomaly_type="price_spike",
                severity=severity,
                details=f"Last candle body {last_body:.2f} is {body_ratio:.1f}x ATR {atr:.2f}",
                confidence=confidence,
            )

        return AnomalyResult(detected=False)

    def _detect_volume_spike(self, df: pd.DataFrame) -> AnomalyResult:
        """Detect if volume is significantly higher than average."""
        if "volume" not in df.columns or df["volume"].sum() == 0:
            return AnomalyResult(detected=False)

        volume = df["volume"].values
        current_volume = volume[-1]
        avg_volume = np.mean(volume[-self.lookback_period:-1])

        if avg_volume <= 0:
            return AnomalyResult(detected=False)

        ratio = current_volume / avg_volume

        if ratio > self.volume_multiplier:
            severity = "high" if ratio > 5.0 else "medium"
            confidence = min((ratio - 1) / 5.0, 1.0)

            return AnomalyResult(
                detected=True,
                anomaly_type="volume_spike",
                severity=severity,
                details=f"Volume {current_volume:.0f} is {ratio:.1f}x average {avg_volume:.0f}",
                confidence=confidence,
            )

        return AnomalyResult(detected=False)
