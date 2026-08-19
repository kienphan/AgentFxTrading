"""
Risk Management Module.

Handles:
- Position sizing based on account equity and risk %
- Daily loss limits
- Maximum drawdown checks
- Position limits
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Risk management configuration."""
    # Basic risk
    risk_per_trade_pct: float = 1.0  # Risk % per trade (1% = 0.01)
    max_daily_loss_pct: float = 3.0  # Max daily loss % (3% = 0.03)
    max_drawdown_pct: float = 10.0  # Max drawdown % from peak equity
    max_positions: int = 3  # Max concurrent positions
    max_positions_per_symbol: int = 1  # Max positions per symbol
    min_rr_ratio: float = 1.5  # Minimum risk/reward ratio

    # Volatility & spread filters
    max_spread_pips: float = 5.0  # Max spread in pips (skip if wider)
    min_sl_atr_multiple: float = 1.5  # SL must be >= 1.5x ATR (avoid liquidity sweeps)
    max_atr_percentile: float = 90.0  # Skip if ATR > 90th percentile (extreme volatility)

    # News filter
    news_filter_enabled: bool = True  # Skip trading around news events
    news_buffer_minutes: int = 30  # Minutes before/after news to skip

    # Symbol-specific risk (optional overrides)
    # Format: "SYMBOL:risk_pct:max_sl_pips" e.g., "XAUUSD:0.5:50"
    symbol_risk_overrides: str = ""


@dataclass
class RiskDecision:
    """Result of risk check."""
    approved: bool
    reason: str
    volume: float = 0.0
    sl_distance: float = 0.0
    risk_amount: float = 0.0


class RiskManager:
    """
    Risk manager for autonomous trading.

    Checks:
    - Position sizing based on equity and risk %
    - Daily loss limit
    - Max drawdown
    - Position limits
    - Risk/reward ratio
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.cfg = config or RiskConfig()
        self._daily_start_equity: Optional[float] = None
        self._peak_equity: Optional[float] = None
        self._daily_pnl: float = 0.0

    def reset_daily(self, equity: float) -> None:
        """Reset daily tracking (call at start of each trading day)."""
        self._daily_start_equity = equity
        self._peak_equity = max(self._peak_equity or equity, equity)
        self._daily_pnl = 0.0
        logger.info("Risk daily reset: equity=%.2f", equity)

    def update_equity(self, equity: float) -> None:
        """Update equity tracking."""
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self._daily_start_equity is not None:
            self._daily_pnl = equity - self._daily_start_equity

    def check_trade(
        self,
        equity: float,
        entry_price: float,
        sl_price: float,
        tp_price: Optional[float],
        side: str,
        symbol: str,
        current_positions: list,
        spread_pips: Optional[float] = None,
        atr: Optional[float] = None,
        atr_history: Optional[list] = None,
        news_event: Optional[Dict[str, Any]] = None,
    ) -> RiskDecision:
        """
        Check if a trade passes risk rules.

        Parameters
        ----------
        equity : float
            Current account equity
        entry_price : float
            Planned entry price
        sl_price : float
            Stop loss price
        tp_price : float, optional
            Take profit price (for R:R check)
        side : str
            "buy" or "sell"
        symbol : str
            Trading symbol
        current_positions : list
            List of current open positions
        spread_pips : float, optional
            Current spread in pips
        atr : float, optional
            Current ATR value
        atr_history : list, optional
            Historical ATR values for percentile calculation
        news_event : dict, optional
            Upcoming news event info
        """
        self.update_equity(equity)

        # Get symbol-specific overrides
        risk_pct = self.cfg.risk_per_trade_pct
        symbol_config = self._get_symbol_config(symbol)
        if symbol_config:
            risk_pct = symbol_config.get("risk_pct", risk_pct)

        # 1. News filter
        if self.cfg.news_filter_enabled and news_event:
            return RiskDecision(
                approved=False,
                reason=f"News event detected: {news_event.get('event', 'Unknown')} — skipping trade",
            )

        # 2. Spread filter
        if spread_pips is not None and spread_pips > self.cfg.max_spread_pips:
            return RiskDecision(
                approved=False,
                reason=f"Spread too wide: {spread_pips:.1f} pips > {self.cfg.max_spread_pips:.1f} pips",
            )

        # 3. Volatility filter (ATR percentile)
        if atr is not None and atr_history and len(atr_history) > 20:
            import numpy as np
            percentile = np.percentile(atr_history, self.cfg.max_atr_percentile)
            if atr > percentile:
                return RiskDecision(
                    approved=False,
                    reason=f"Volatility too high: ATR {atr:.2f} > {self.cfg.max_atr_percentile}th percentile ({percentile:.2f})",
                )

        # 4. Check daily loss limit
        if self._daily_start_equity is not None:
            daily_loss_pct = -self._daily_pnl / self._daily_start_equity
            if daily_loss_pct >= self.cfg.max_daily_loss_pct:
                return RiskDecision(
                    approved=False,
                    reason=f"Daily loss limit hit: {daily_loss_pct:.2%} >= {self.cfg.max_daily_loss_pct:.2%}",
                )

        # 5. Check max drawdown
        if self._peak_equity is not None and self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - equity) / self._peak_equity
            if drawdown_pct >= self.cfg.max_drawdown_pct:
                return RiskDecision(
                    approved=False,
                    reason=f"Max drawdown hit: {drawdown_pct:.2%} >= {self.cfg.max_drawdown_pct:.2%}",
                )

        # 6. Check position limits
        total_positions = len(current_positions)
        if total_positions >= self.cfg.max_positions:
            return RiskDecision(
                approved=False,
                reason=f"Max positions reached: {total_positions} >= {self.cfg.max_positions}",
            )

        # Check positions per symbol
        symbol_positions = sum(1 for p in current_positions if p.get("symbol") == symbol)
        if symbol_positions >= self.cfg.max_positions_per_symbol:
            return RiskDecision(
                approved=False,
                reason=f"Max positions per symbol reached for {symbol}",
            )

        # 7. Calculate SL distance
        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0:
            return RiskDecision(
                approved=False,
                reason="Invalid stop loss distance",
            )

        # 8. ATR-based SL validation (avoid liquidity sweeps)
        if atr is not None and self.cfg.min_sl_atr_multiple > 0:
            min_sl_distance = atr * self.cfg.min_sl_atr_multiple
            if sl_distance < min_sl_distance:
                return RiskDecision(
                    approved=False,
                    reason=f"SL too tight: {sl_distance:.2f} < {self.cfg.min_sl_atr_multiple}x ATR ({min_sl_distance:.2f}). XAUUSD liquidity sweeps will hit this.",
                )

        # 9. Check risk/reward ratio
        if tp_price is not None:
            tp_distance = abs(tp_price - entry_price)
            rr_ratio = tp_distance / sl_distance
            if rr_ratio < self.cfg.min_rr_ratio:
                return RiskDecision(
                    approved=False,
                    reason=f"R:R ratio too low: {rr_ratio:.2f} < {self.cfg.min_rr_ratio:.2f}",
                )

        # 10. Calculate position size
        risk_amount = equity * risk_pct
        # For XAUUSD: 1 lot = 100 oz, 1 pip = $0.01, pip value = $1 per lot
        # SL distance in price terms, need to convert to lots
        # Simplified: volume = risk_amount / (sl_distance * contract_size)
        # For XAUUSD: contract_size = 100, so volume = risk_amount / (sl_distance * 100)
        # For forex: depends on pair, but we'll use a generic formula

        # Generic formula: risk_amount / (sl_distance * pip_value_per_lot)
        # Assuming pip_value_per_lot is approximately the contract size
        # For XAUUSD: 1 lot = 100 oz, so $1 move = $100 per lot
        contract_size = self._get_contract_size(symbol)
        volume = risk_amount / (sl_distance * contract_size)

        # Round to 2 decimal places (standard lot size)
        volume = round(volume, 2)

        # Minimum volume check
        if volume < 0.01:
            return RiskDecision(
                approved=False,
                reason=f"Calculated volume too small: {volume:.2f} lots",
            )

        return RiskDecision(
            approved=True,
            reason="OK",
            volume=volume,
            sl_distance=sl_distance,
            risk_amount=risk_amount,
        )

    def _get_symbol_config(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get symbol-specific risk configuration."""
        if not self.cfg.symbol_risk_overrides:
            return None

        for entry in self.cfg.symbol_risk_overrides.split(","):
            entry = entry.strip()
            if ":" in entry:
                parts = entry.split(":")
                if len(parts) >= 2 and parts[0].upper() == symbol.upper():
                    return {
                        "risk_pct": float(parts[1]) / 100.0,
                        "max_sl_pips": float(parts[2]) if len(parts) > 2 else None,
                    }
        return None

    def _get_contract_size(self, symbol: str) -> float:
        """Get contract size for a symbol."""
        # XAUUSD: 1 lot = 100 oz
        if "XAU" in symbol:
            return 100.0
        # FX pairs: 1 lot = 100,000 units
        elif "JPY" in symbol:
            return 100000.0 / 100.0  # JPY pairs have different pip value
        else:
            return 100000.0

    def get_status(self, equity: float) -> Dict[str, Any]:
        """Get current risk status."""
        status = {
            "equity": equity,
            "daily_pnl": self._daily_pnl,
        }

        if self._daily_start_equity is not None:
            status["daily_pnl_pct"] = self._daily_pnl / self._daily_start_equity
            status["daily_loss_remaining_pct"] = (
                self.cfg.max_daily_loss_pct + self._daily_pnl / self._daily_start_equity
            )

        if self._peak_equity is not None:
            status["peak_equity"] = self._peak_equity
            status["drawdown_pct"] = (self._peak_equity - equity) / self._peak_equity
            status["drawdown_remaining_pct"] = (
                self.cfg.max_drawdown_pct - (self._peak_equity - equity) / self._peak_equity
            )

        return status
