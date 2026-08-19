"""
Snapshot builder — fetches market data from cTrader Remote MCP and calculates TMS indicators.
Includes trading operations for autonomous execution.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from app.indicators.tms import TMSIndicator, TMSConfig
from app.indicators.orb import ORBIndicator, ORBConfig
from app.news.anomaly import MarketAnomalyDetector

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for cTrader Remote MCP with trading capabilities."""

    def __init__(self, mcp_url: str, auth_token: str = "") -> None:
        self.mcp_url = mcp_url
        self.auth_token = auth_token
        self._session = None
        self._session_ctx = None

    async def connect(self) -> None:
        """Connect to MCP server."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            # Prepare headers for authentication
            headers = {}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            # Connect using Streamable HTTP
            self._session_ctx = streamablehttp_client(self.mcp_url, headers=headers)
            streams = await self._session_ctx.__aenter__()
            self._session = await ClientSession(*streams).__aenter__()
            await self._session.initialize()
            logger.info("Connected to cTrader Remote MCP: %s", self.mcp_url)
        except Exception as e:
            logger.error("Failed to connect to MCP: %s", e)
            raise

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        logger.info("Disconnected from cTrader Remote MCP")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool and return the result."""
        if not self._session:
            raise RuntimeError("Not connected to MCP server")

        result = await self._session.call_tool(tool_name, arguments)

        # Parse result content
        if hasattr(result, "content") and result.content:
            for item in result.content:
                if hasattr(item, "text"):
                    try:
                        return json.loads(item.text)
                    except json.JSONDecodeError:
                        return item.text
        return result

    # ==================== Market Data ====================

    async def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> List[Dict[str, Any]]:
        """Fetch historical candles from cTrader."""
        result = await self.call_tool("get_candles", {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count,
        })
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "candles" in result:
            return result["candles"]
        return []

    async def get_price(self, symbol: str) -> Dict[str, Any]:
        """Get current price for a symbol."""
        result = await self.call_tool("get_symbol_price", {"symbol": symbol})
        return result if isinstance(result, dict) else {}

    # ==================== Account Info ====================

    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance and equity."""
        result = await self.call_tool("get_balance", {})
        return result if isinstance(result, dict) else {}

    async def get_equity(self) -> float:
        """Get current account equity."""
        balance_info = await self.get_balance()
        return float(balance_info.get("equity", balance_info.get("balance", 0)))

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions."""
        result = await self.call_tool("get_positions", {})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "positions" in result:
            return result["positions"]
        return []

    # ==================== Trading Operations ====================

    async def place_order(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        volume: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        """
        Place a market order.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "XAUUSD")
        side : str
            "buy" or "sell"
        volume : float
            Volume in lots
        sl : float, optional
            Stop loss price
        tp : float, optional
            Take profit price
        comment : str
            Order comment

        Returns
        -------
        dict
            Order result with position_id, error, etc.
        """
        args = {
            "symbol": symbol,
            "side": side.lower(),
            "volume": volume,
            "order_type": "market",
        }
        if sl is not None:
            args["stop_loss"] = sl
        if tp is not None:
            args["take_profit"] = tp
        if comment:
            args["comment"] = comment

        logger.info("Placing order: %s %s %.2f lots SL=%s TP=%s",
                   side.upper(), symbol, volume, sl, tp)

        result = await self.call_tool("place_order", args)
        return result if isinstance(result, dict) else {"error": "unknown_response"}

    async def close_position(
        self,
        position_id: int,
        volume: Optional[float] = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        """
        Close a position (full or partial).

        Parameters
        ----------
        position_id : int
            Position ID to close
        volume : float, optional
            Volume to close (None = full close)
        comment : str
            Close comment

        Returns
        -------
        dict
            Close result
        """
        args = {"position_id": position_id}
        if volume is not None:
            args["volume"] = volume
        if comment:
            args["comment"] = comment

        logger.info("Closing position %d (volume=%s)", position_id, volume or "full")

        result = await self.call_tool("close_position", args)
        return result if isinstance(result, dict) else {"error": "unknown_response"}

    async def modify_position(
        self,
        position_id: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Modify position SL/TP.

        Parameters
        ----------
        position_id : int
            Position ID to modify
        sl : float, optional
            New stop loss price
        tp : float, optional
            New take profit price

        Returns
        -------
        dict
            Modify result
        """
        args = {"position_id": position_id}
        if sl is not None:
            args["stop_loss"] = sl
        if tp is not None:
            args["take_profit"] = tp

        logger.info("Modifying position %d: SL=%s TP=%s", position_id, sl, tp)

        result = await self.call_tool("modify_position", args)
        return result if isinstance(result, dict) else {"error": "unknown_response"}


def candles_to_dataframe(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert candle list to pandas DataFrame."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(candles)

    # Normalize column names (handle different formats)
    column_map = {
        "time": "timestamp",
        "datetime": "timestamp",
        "date": "timestamp",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=column_map)

    # Ensure required columns exist
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            logger.warning("Missing column: %s", col)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    if "volume" not in df.columns:
        df["volume"] = 0

    # Convert to numeric
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with NaN
    df = df.dropna(subset=["open", "high", "low", "close"])

    return df[["open", "high", "low", "close", "volume"]].reset_index(drop=True)


def build_snapshot(
    symbol: str,
    timeframe: str,
    candles: List[Dict[str, Any]],
    price_data: Optional[Dict[str, Any]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
    account_info: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    tms_config: Optional[TMSConfig] = None,
    orb_config: Optional[ORBConfig] = None,
    orb_candles: Optional[List[Dict[str, Any]]] = None,
    current_spread_pips: Optional[float] = None,
    avg_spread_pips: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build a market snapshot with TMS and ORB indicators.

    Parameters
    ----------
    symbol : str
        Trading symbol
    timeframe : str
        TMS timeframe (e.g., H4)
    candles : list
        Candle data for TMS (H4)
    orb_candles : list, optional
        Candle data for ORB (M5/M15), separate from TMS data
    """
    df = candles_to_dataframe(candles)

    if df.empty:
        return {
            "ok": False,
            "reason": "no_candles",
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Calculate TMS indicators on H4 data
    tms = TMSIndicator(tms_config)
    tms_result = tms.calculate(df)

    # Calculate ORB indicators on M5/M15 data (separate timeframe)
    orb_result = {}
    if orb_config is not None and orb_candles is not None:
        orb_df = candles_to_dataframe(orb_candles)
        if not orb_df.empty:
            orb = ORBIndicator(orb_config)
            orb_result = orb.calculate(orb_df)
            orb_result["timeframe"] = orb_config.timeframe  # Add timeframe info

    # Detect market anomalies (AI-based news detection)
    anomaly_detector = MarketAnomalyDetector()
    anomaly_result = anomaly_detector.detect(df, current_spread_pips, avg_spread_pips)

    if not tms_result.get("ok"):
        return {
            "ok": False,
            "reason": tms_result.get("reason", "tms_calc_failed"),
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bars": len(df),
        }

    # Build snapshot
    snapshot = {
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bar_count": len(df),
        "price": tms_result.get("price"),

        # TMS indicators
        "ha": tms_result.get("ha", {}),
        "tdi": tms_result.get("tdi", {}),
        "stoch": tms_result.get("stoch", {}),
        "signal": tms_result.get("signal", {}),
        "action": tms_result.get("action", "WAIT"),
        "bias": tms_result.get("bias", "NEUTRAL"),  # TMS directional bias

        # ORB indicators
        "orb": orb_result,

        # Market anomaly (AI-based news detection)
        "anomaly": anomaly_result.to_dict(),

        # Additional context
        "indicators": {
            "price": tms_result.get("price"),
            "ha_color": tms_result.get("ha", {}).get("color"),
            "ha_trend": tms_result.get("ha", {}).get("trend"),
            "tdi_green": tms_result.get("tdi", {}).get("green"),
            "tdi_red": tms_result.get("tdi", {}).get("red"),
            "tdi_level": tms_result.get("tdi", {}).get("level"),
            "stoch_k": tms_result.get("stoch", {}).get("k"),
            "stoch_d": tms_result.get("stoch", {}).get("d"),
        },
    }

    # Add current price from MCP if available
    if price_data:
        snapshot["live_price"] = price_data

    # Add positions if available
    if positions:
        snapshot["positions"] = positions
        snapshot["position_count"] = len(positions)

    # Add account info if available
    if account_info:
        snapshot["account"] = account_info

    # Add portfolio state if available
    if portfolio_state:
        snapshot["portfolio"] = portfolio_state

    return snapshot
