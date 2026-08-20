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


def _format_exception(e: BaseException) -> str:
    """Unwrap ExceptionGroup / BaseExceptionGroup to extract the root cause."""
    if hasattr(e, "exceptions") and getattr(e, "exceptions"):
        return "; ".join(_format_exception(sub) for sub in getattr(e, "exceptions"))
    if hasattr(e, "__cause__") and e.__cause__:
        return f"{type(e).__name__}: {e} (caused by: {_format_exception(e.__cause__)})"
    return f"[{type(e).__name__}] {e}"


def _normalize_ctrader_token(token_str: str) -> tuple[str, str]:
    """
    Decodes cTrader Base64 token, ensures 'environment' is 'live' or 'demo',
    and re-encodes it. Returns (normalized_token, env_type).
    """
    import base64
    try:
        padded = token_str + '=' * (-len(token_str) % 4)
        data = json.loads(base64.b64decode(padded).decode('utf-8'))
        raw_env = str(data.get("environment", "")).lower()
        env_type = "live" if "live" in raw_env else "demo"
        data["environment"] = env_type
        normalized = base64.b64encode(json.dumps(data).encode('utf-8')).decode('utf-8')
        return normalized, env_type
    except Exception:
        return token_str, "live"


class MCPClient:
    """Client for cTrader Remote MCP with trading capabilities."""

    def __init__(self, mcp_url: str, auth_token: str = "") -> None:
        self.mcp_url = mcp_url
        self.auth_token = auth_token
        self._session = None
        self._session_ctx = None
        self._http_client = None

    async def connect(self) -> None:
        """Connect to cTrader Remote MCP server via Streamable HTTP."""
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        token_to_use, env_type = _normalize_ctrader_token(self.auth_token) if self.auth_token else ("", "live")

        headers = {
            "Accept": "application/json, text/event-stream",
            "X-Environment": env_type,
        }
        if token_to_use:
            headers["Authorization"] = f"Bearer {token_to_use}"

        try:
            self._http_client = create_mcp_http_client(headers=headers)
            await self._http_client.__aenter__()

            self._session_ctx = streamable_http_client(self.mcp_url, http_client=self._http_client)
            streams = await self._session_ctx.__aenter__()
            read_stream, write_stream = streams[0], streams[1]
            self._session = await ClientSession(read_stream, write_stream).__aenter__()
            await self._session.initialize()
            logger.info("Connected to cTrader Remote MCP (%s environment): %s", env_type, self.mcp_url)
            return
        except BaseException as e:
            err_msg = _format_exception(e)
            logger.error("Failed to connect to cTrader MCP at %s: %s", self.mcp_url, err_msg)
            logger.warning("Remote server closed or reset connection. Please verify CTRADER_MCP_URL, CTRADER_MCP_TOKEN, or network access.")
            await self._cleanup()
            raise ConnectionError(f"Cannot connect to cTrader MCP at {self.mcp_url}: {err_msg}") from e

    async def _cleanup(self) -> None:
        """Internal helper to safely clean up all active contexts."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except BaseException:
                pass
            self._session = None
        if self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except BaseException:
                pass
            self._session_ctx = None
        if self._http_client is not None:
            try:
                await self._http_client.__aexit__(None, None, None)
            except BaseException:
                pass
            self._http_client = None

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        await self._cleanup()
        logger.info("Disconnected from cTrader Remote MCP")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an MCP tool safely and return the result."""
        if not self._session:
            logger.warning("MCP session not connected when calling tool '%s'", tool_name)
            return {"error": "not_connected"}

        try:
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
        except Exception as e:
            logger.warning("MCP call_tool '%s' failed: %s", tool_name, e)
            return {"error": str(e)}

    # ==================== Market Data ====================

    TIMEFRAME_MAP = {
        "M1": "M_1", "m1": "M_1", "1m": "M_1",
        "M5": "M_5", "m5": "M_5", "5m": "M_5",
        "M15": "M_15", "m15": "M_15", "15m": "M_15",
        "M30": "M_30", "m30": "M_30", "30m": "M_30",
        "H1": "H_1", "h1": "H_1", "1h": "H_1",
        "H4": "H_4", "h4": "H_4", "4h": "H_4",
        "D1": "D_1", "d1": "D_1", "1d": "D_1",
        "W1": "W_1", "w1": "W_1",
        "MN1": "MN_1", "MN": "MN_1",
    }

    DEFAULT_SYMBOL_IDS = {
        "EURUSD": 1,
        "GBPUSD": 2,
        "EURJPY": 3,
        "USDJPY": 4,
        "AUDUSD": 5,
        "NZDUSD": 6,
        "GBPJPY": 7,
        "USDCAD": 8,
        "USDCHF": 9,
        "EURGBP": 10,
        "AUDJPY": 11,
        "EURAUD": 12,
        "CADJPY": 13,
        "GBPAUD": 14,
        "NZDJPY": 15,
        "GBPCAD": 16,
        "EURCAD": 17,
        "AUDCAD": 18,
        "GBPCHF": 19,
        "EURCHF": 20,
        "XAUUSD": 41,
        "GOLD": 41,
        "XAGUSD": 42,
        "SILVER": 42,
        "BTCUSD": 43,
        "ETHUSD": 44,
    }

    SYMBOL_ALIASES = {
        "XAUUSD": ["GOLD", "XAUUSD.RAW", "XAUUSD.A", "XAUUSD+", "XAUUSD.ECN", "XAUUSD.M"],
        "GOLD": ["XAUUSD", "XAUUSD.RAW", "XAUUSD.A", "XAUUSD+"],
        "XAGUSD": ["SILVER", "XAGUSD.RAW", "XAGUSD.A", "XAGUSD+"],
        "SILVER": ["XAGUSD", "XAGUSD.RAW", "XAGUSD.A"],
        "EURUSD": ["EURUSD.RAW", "EURUSD.A", "EURUSD+", "EURUSD.ECN", "EURUSD.M"],
        "GBPUSD": ["GBPUSD.RAW", "GBPUSD.A", "GBPUSD+", "GBPUSD.ECN", "GBPUSD.M"],
        "USDJPY": ["USDJPY.RAW", "USDJPY.A", "USDJPY+", "USDJPY.ECN", "USDJPY.M"],
        "AUDUSD": ["AUDUSD.RAW", "AUDUSD.A", "AUDUSD+", "AUDUSD.ECN", "AUDUSD.M"],
        "USDCAD": ["USDCAD.RAW", "USDCAD.A", "USDCAD+", "USDCAD.ECN", "USDCAD.M"],
        "USDCHF": ["USDCHF.RAW", "USDCHF.A", "USDCHF+", "USDCHF.ECN", "USDCHF.M"],
        "NZDUSD": ["NZDUSD.RAW", "NZDUSD.A", "NZDUSD+", "NZDUSD.ECN", "NZDUSD.M"],
    }

    async def get_symbol_id(self, symbol: str) -> Optional[int]:
        """Resolve symbol name (e.g. XAUUSD) to symbolId with broker alias/suffix support."""
        if not hasattr(self, "_symbol_id_cache"):
            self._symbol_id_cache = dict(self.DEFAULT_SYMBOL_IDS)
        
        symbol_upper = symbol.strip().upper()
        if symbol_upper in self._symbol_id_cache:
            return self._symbol_id_cache[symbol_upper]

        try:
            res = await self.call_tool("get_symbols", {})
            symbols_list = []
            if isinstance(res, list):
                symbols_list = res
            elif isinstance(res, dict):
                symbols_list = res.get("symbols") or res.get("data") or []

            for s in symbols_list:
                if isinstance(s, dict):
                    name = s.get("name") or s.get("symbolName") or s.get("symbol")
                    sid = s.get("id") or s.get("symbolId")
                    if name and sid is not None:
                        self._symbol_id_cache[str(name).upper()] = int(sid)

            # 1. Exact match
            if symbol_upper in self._symbol_id_cache:
                return self._symbol_id_cache[symbol_upper]

            # 2. Check aliases
            aliases = self.SYMBOL_ALIASES.get(symbol_upper, [])
            for alias in aliases:
                if alias in self._symbol_id_cache:
                    sid = self._symbol_id_cache[alias]
                    self._symbol_id_cache[symbol_upper] = sid
                    return sid

            # 3. Suffix match (e.g. broker symbols like XAUUSD.raw, EURUSD.ecn)
            for broker_symbol, sid in self._symbol_id_cache.items():
                if broker_symbol.startswith(symbol_upper + ".") or broker_symbol.startswith(symbol_upper + "+"):
                    self._symbol_id_cache[symbol_upper] = sid
                    return sid

            return self.DEFAULT_SYMBOL_IDS.get(symbol_upper)
        except Exception as e:
            logger.warning("Failed to resolve symbolId for %s: %s", symbol, e)
            return self.DEFAULT_SYMBOL_IDS.get(symbol_upper)

    async def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> List[Dict[str, Any]]:
        """Fetch historical candles from cTrader (via get_trendbars with timestamp range)."""
        symbol_id = await self.get_symbol_id(symbol)
        if symbol_id is None:
            return []

        period = self.TIMEFRAME_MAP.get(timeframe, timeframe)
        
        # Calculate time range for get_trendbars (cTrader API requires fromTimestamp & toTimestamp <= 720h)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        # 30 days max (720h) per cTrader API limitation
        days_back = min(30, max(5, int(count * 0.3) if "M" in period else min(30, int(count * 1.5))))
        from_time = (now - timedelta(days=days_back)).isoformat()
        to_time = now.isoformat()

        result = await self.call_tool("get_trendbars", {
            "symbolId": symbol_id,
            "period": period,
            "fromTimestamp": from_time,
            "toTimestamp": to_time,
            "count": count,
        })
        
        raw_bars = []
        if isinstance(result, list):
            raw_bars = result
        elif isinstance(result, dict):
            raw_bars = result.get("trendbars") or result.get("candles") or []

        # Convert cTrader price units (scaled by 100,000) to standard price floats
        candles = []
        for b in raw_bars:
            if isinstance(b, dict):
                o = b.get("open", 0)
                h = b.get("high", 0)
                l = b.get("low", 0)
                c = b.get("close", 0)
                # Scale if raw pipettes/units (e.g. 115625 -> 1.15625, 449304000 -> 4493.04)
                scale = 100000.0 if (abs(o) > 1000 or abs(c) > 1000) else 1.0
                candles.append({
                    "timestamp": b.get("timestamp"),
                    "open": o / scale,
                    "high": h / scale,
                    "low": l / scale,
                    "close": c / scale,
                    "volume": b.get("volume", 0),
                })

        return candles[-count:] if count and len(candles) > count else candles

    async def get_price(self, symbol: str) -> Dict[str, Any]:
        """Get current price for a symbol."""
        symbol_id = await self.get_symbol_id(symbol)
        if symbol_id is not None:
            result = await self.call_tool("get_spot_prices", {"symbolId": [symbol_id]})
            if isinstance(result, list) and result:
                return result[0]
            if isinstance(result, dict):
                prices = result.get("spotPrices") or result.get("prices") or []
                if prices:
                    return prices[0]
                return result

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
        """
        symbol_id = await self.get_symbol_id(symbol)
        trade_side = "BUY" if side.lower() == "buy" else "SELL"

        if symbol_id is not None:
            # cTrader Remote MCP native tool: create_order
            # Note: forex 1 lot = 10_000_000 (volume units = lots * lotSize * 100)
            # metals (XAUUSD) 1 lot = 10_000
            is_metal = "XAU" in symbol.upper() or "XAG" in symbol.upper()
            lot_size = 100 if is_metal else 100000
            vol_units = int(volume * lot_size * 100)

            args: Dict[str, Any] = {
                "symbolId": symbol_id,
                "orderType": "MARKET",
                "tradeSide": trade_side,
                "volume": vol_units,
            }
            if comment:
                args["comment"] = comment

            result = await self.call_tool("create_order", args)
            return result if isinstance(result, dict) else {"result": result}

        # Fallback to generic place_order
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
        """
        args: Dict[str, Any] = {"positionId": position_id}
        if volume is not None:
            args["volume"] = int(volume)

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
