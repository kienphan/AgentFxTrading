"""
Portfolio-level Risk Management.

Tracks and manages risk across all open positions:
- Total portfolio heat (sum of all position risks)
- Currency exposure (avoid double exposure)
- Correlation check (avoid correlated positions)
- Margin usage
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# FX Pair correlation groups (simplified)
# Pairs in same group tend to move together
CORRELATION_GROUPS = {
    "USD": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "GBPCHF"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY"],
    "AUD": ["AUDUSD", "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD"],
    "CAD": ["USDCAD", "AUDCAD", "CADJPY", "CADCHF"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF", "AUDCHF", "CADCHF", "CHFJPY"],
    "NZD": ["NZDUSD", "AUDNZD", "NZDJPY"],
    "XAU": ["XAUUSD"],
}

# High correlation pairs (same base or quote currency)
HIGH_CORRELATION = {
    ("EURUSD", "GBPUSD"): 0.85,
    ("EURUSD", "AUDUSD"): 0.80,
    ("GBPUSD", "AUDUSD"): 0.75,
    ("EURUSD", "EURJPY"): 0.70,
    ("GBPUSD", "GBPJPY"): 0.70,
    ("AUDUSD", "AUDJPY"): 0.70,
}


@dataclass
class PortfolioConfig:
    """Portfolio risk configuration."""
    max_total_heat_pct: float = 5.0  # Max total risk across all positions (%)
    max_margin_usage_pct: float = 50.0  # Max margin usage (%)
    max_same_currency_exposure: int = 2  # Max positions with same currency
    max_correlated_positions: int = 2  # Max highly correlated positions
    correlation_threshold: float = 0.7  # Threshold for "high correlation"


@dataclass
class PositionInfo:
    """Simplified position info for portfolio tracking."""
    symbol: str
    side: str  # "buy" or "sell"
    volume: float
    entry_price: float
    sl_price: Optional[float] = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def base_currency(self) -> str:
        """Get base currency (first 3 chars)."""
        return self.symbol[:3] if len(self.symbol) >= 3 else self.symbol

    @property
    def quote_currency(self) -> str:
        """Get quote currency (last 3 chars)."""
        return self.symbol[-3:] if len(self.symbol) >= 3 else self.symbol

    @property
    def risk_amount(self) -> float:
        """Calculate risk amount based on SL distance."""
        if self.sl_price is None:
            return 0.0
        sl_distance = abs(self.entry_price - self.sl_price)
        # Simplified: for XAUUSD, 1 lot = 100 oz, so $1 move = $100
        # For forex, depends on pair
        contract_size = 100 if "XAU" in self.symbol else 100000
        return self.volume * sl_distance * contract_size


@dataclass
class PortfolioState:
    """Current portfolio state."""
    equity: float = 0.0
    balance: float = 0.0
    free_margin: float = 0.0
    margin_used: float = 0.0
    total_pnl: float = 0.0
    positions: List[PositionInfo] = field(default_factory=list)

    # Calculated metrics
    total_heat: float = 0.0  # Total risk amount
    heat_pct: float = 0.0  # Total risk as % of equity
    margin_usage_pct: float = 0.0

    # Exposure by currency
    currency_exposure: Dict[str, int] = field(default_factory=dict)

    # Correlation warnings
    correlation_warnings: List[str] = field(default_factory=list)


class PortfolioRiskManager:
    """
    Portfolio-level risk manager.

    Checks before opening new position:
    - Total portfolio heat
    - Currency exposure
    - Correlation with existing positions
    - Margin usage
    """

    def __init__(self, config: Optional[PortfolioConfig] = None) -> None:
        self.cfg = config or PortfolioConfig()

    def analyze_portfolio(
        self,
        equity: float,
        balance: float,
        free_margin: float,
        positions: List[Dict[str, Any]],
    ) -> PortfolioState:
        """Analyze current portfolio state."""
        state = PortfolioState(
            equity=equity,
            balance=balance,
            free_margin=free_margin,
        )

        # Calculate margin usage
        if equity > 0:
            state.margin_used = balance - free_margin
            state.margin_usage_pct = (state.margin_used / equity) * 100

        # Parse positions
        for pos in positions:
            pos_info = PositionInfo(
                symbol=pos.get("symbol", ""),
                side=pos.get("side", ""),
                volume=float(pos.get("volume", 0)),
                entry_price=float(pos.get("entry_price", 0)),
                sl_price=float(pos.get("sl")) if pos.get("sl") else None,
                current_price=float(pos.get("current_price", 0)),
                unrealized_pnl=float(pos.get("profit", 0)),
            )
            state.positions.append(pos_info)
            state.total_pnl += pos_info.unrealized_pnl

            # Track currency exposure
            base = pos_info.base_currency
            quote = pos_info.quote_currency
            state.currency_exposure[base] = state.currency_exposure.get(base, 0) + 1
            state.currency_exposure[quote] = state.currency_exposure.get(quote, 0) + 1

        # Calculate total heat (risk)
        state.total_heat = sum(p.risk_amount for p in state.positions)
        if equity > 0:
            state.heat_pct = (state.total_heat / equity) * 100

        # Check correlations
        state.correlation_warnings = self._check_correlations(state.positions)

        return state

    def check_new_trade(
        self,
        portfolio: PortfolioState,
        new_symbol: str,
        new_side: str,
        new_volume: float,
        new_risk_amount: float,
    ) -> Tuple[bool, str]:
        """
        Check if new trade is allowed based on portfolio state.

        Returns
        -------
        (allowed, reason)
        """
        # 1. Check total heat
        new_heat_pct = ((portfolio.total_heat + new_risk_amount) / portfolio.equity) * 100
        if new_heat_pct > self.cfg.max_total_heat_pct:
            return False, f"Portfolio heat too high: {new_heat_pct:.2f}% > {self.cfg.max_total_heat_pct:.2f}%"

        # 2. Check margin usage
        # Simplified: assume new position uses some margin
        # In reality, should calculate based on leverage
        if portfolio.margin_usage_pct > self.cfg.max_margin_usage_pct:
            return False, f"Margin usage too high: {portfolio.margin_usage_pct:.2f}%"

        # 3. Check currency exposure
        base = new_symbol[:3] if len(new_symbol) >= 3 else new_symbol
        quote = new_symbol[-3:] if len(new_symbol) >= 3 else new_symbol

        base_count = portfolio.currency_exposure.get(base, 0)
        quote_count = portfolio.currency_exposure.get(quote, 0)

        # Count existing positions with same currency
        same_base = sum(1 for p in portfolio.positions if p.base_currency == base or p.quote_currency == base)
        same_quote = sum(1 for p in portfolio.positions if p.base_currency == quote or p.quote_currency == quote)

        if same_base >= self.cfg.max_same_currency_exposure:
            return False, f"Too much {base} exposure ({same_base} positions)"
        if same_quote >= self.cfg.max_same_currency_exposure:
            return False, f"Too much {quote} exposure ({same_quote} positions)"

        # 4. Check correlation
        correlated_count = 0
        for pos in portfolio.positions:
            corr = self._get_correlation(new_symbol, pos.symbol)
            if corr >= self.cfg.correlation_threshold:
                # Same direction = higher risk
                if new_side.lower() == pos.side.lower():
                    correlated_count += 1

        if correlated_count >= self.cfg.max_correlated_positions:
            return False, f"Too many correlated positions ({correlated_count})"

        return True, "OK"

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols."""
        if symbol1 == symbol2:
            return 1.0

        # Check known correlations
        key = tuple(sorted([symbol1, symbol2]))
        if key in HIGH_CORRELATION:
            return HIGH_CORRELATION[key]

        # Check if same base or quote currency
        base1, quote1 = symbol1[:3], symbol1[-3:]
        base2, quote2 = symbol2[:3], symbol2[-3:]

        if base1 == base2 or quote1 == quote2:
            return 0.6  # Moderate correlation
        if base1 == quote2 or quote1 == base2:
            return -0.4  # Negative correlation (inverse)

        return 0.0  # No correlation

    def _check_correlations(self, positions: List[PositionInfo]) -> List[str]:
        """Check for correlation warnings in current positions."""
        warnings = []

        for i, pos1 in enumerate(positions):
            for pos2 in positions[i + 1:]:
                corr = self._get_correlation(pos1.symbol, pos2.symbol)
                if corr >= self.cfg.correlation_threshold:
                    if pos1.side == pos2.side:
                        warnings.append(
                            f"High correlation ({corr:.2f}): {pos1.symbol} {pos1.side} + {pos2.symbol} {pos2.side}"
                        )

        return warnings

    def get_portfolio_summary(self, portfolio: PortfolioState) -> Dict[str, Any]:
        """Get portfolio summary for LLM context."""
        return {
            "equity": portfolio.equity,
            "balance": portfolio.balance,
            "free_margin": portfolio.free_margin,
            "margin_usage_pct": round(portfolio.margin_usage_pct, 2),
            "total_pnl": round(portfolio.total_pnl, 2),
            "total_heat": round(portfolio.total_heat, 2),
            "heat_pct": round(portfolio.heat_pct, 2),
            "position_count": len(portfolio.positions),
            "currency_exposure": portfolio.currency_exposure,
            "correlation_warnings": portfolio.correlation_warnings,
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "volume": p.volume,
                    "entry": p.entry_price,
                    "pnl": round(p.unrealized_pnl, 2),
                }
                for p in portfolio.positions
            ],
        }
