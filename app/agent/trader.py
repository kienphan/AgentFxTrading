"""
AgentTrader — Autonomous trading agent.

Analyzes market data, makes decisions via LLM, executes trades via cTrader Remote MCP.
Includes risk management and position tracking.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.agent.client import LLMClient
from app.agent.portfolio import PortfolioRiskManager, PortfolioConfig
from app.agent.prompt import SYSTEM_PROMPT, build_user_prompt
from app.agent.risk import RiskManager, RiskConfig
from app.agent.snapshot import MCPClient, build_snapshot
from app.core.config import Settings
from app.indicators.orb import ORBConfig
from app.indicators.tms import TMSConfig
from app.news.calendar import EconomicCalendar

logger = logging.getLogger(__name__)


class AgentTrader:
    """Autonomous trading agent with risk management."""

    def __init__(
        self,
        settings: Settings,
        llm_client: LLMClient,
        mcp_client: Optional[MCPClient] = None,
        tms_config: Optional[TMSConfig] = None,
        risk_config: Optional[RiskConfig] = None,
        portfolio_config: Optional[PortfolioConfig] = None,
        orb_config: Optional[ORBConfig] = None,
    ) -> None:
        self.settings = settings
        self.llm_client = llm_client
        self.mcp_client = mcp_client
        self.tms_config = tms_config
        self.risk_manager = RiskManager(risk_config)
        self.portfolio_manager = PortfolioRiskManager(portfolio_config)
        self.orb_config = orb_config
        self.news_calendar = EconomicCalendar()
        self.cycle_count = 0
        self._last_date: Optional[str] = None

    def _get_symbol_orb_config(self, symbol: str) -> Optional[ORBConfig]:
        """Get ORB config for a specific symbol (per-symbol session support)."""
        if self.orb_config is None:
            return None

        # Get symbol-specific session from settings
        session_name, session_hour, session_minute = self.settings.get_orb_session_for_symbol(symbol)

        # Create symbol-specific ORB config
        return ORBConfig(
            timeframe=self.orb_config.timeframe,
            session=session_name,
            session_start_hour=session_hour,
            session_start_minute=session_minute,
            or_candles=self.orb_config.or_candles,
            min_or_width=self.orb_config.min_or_width,
            buffer_points=self.orb_config.buffer_points,
            max_bars_after_breakout=self.orb_config.max_bars_after_breakout,
        )

    async def run_cycle(self) -> Dict[str, Any]:
        """
        Run one autonomous trading cycle for ALL symbols:
        1. Check if within trading session (London/NY)
        2. Fetch account info + positions (shared)
        3. For each symbol:
           - Fetch candles
           - Calculate TMS indicators
           - Get LLM decision
           - Check risk rules
           - Execute trade if approved
        4. Return combined results
        """
        self.cycle_count += 1
        cycle_id = self.cycle_count

        # Check if within trading session
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour
        is_active, session_name = self.settings.is_trading_session(utc_hour)

        if not is_active:
            logger.info(
                "=== Cycle %d: OFF-HOURS (%s UTC) — skipping trading ===",
                cycle_id, session_name
            )
            return {
                "ok": True,
                "cycle": cycle_id,
                "timestamp": now_utc.isoformat(),
                "session": session_name,
                "status": "off-hours",
                "message": f"Outside trading sessions ({session_name}). Active sessions: {self.settings.TRADING_SESSIONS}",
                "results": [],
            }

        logger.info(
            "=== Cycle %d starting — Session: %s (%d:00 UTC) ===",
            cycle_id, session_name, utc_hour
        )

        try:
            if not self.mcp_client:
                return {"ok": False, "action": "ERROR", "reason": "MCP client not connected"}

            timeframe = self.settings.TIMEFRAME
            lookback = self.settings.LOOKBACK_BARS

            # 1. Fetch shared data (account, positions)
            logger.info("Fetching account info and positions...")
            account_info = await self.mcp_client.get_balance()
            positions = await self.mcp_client.get_positions()

            equity = float(account_info.get("equity", account_info.get("balance", 0)))
            balance = float(account_info.get("balance", 0))
            free_margin = float(account_info.get("free_margin", equity))
            logger.info("Account equity: %.2f", equity)

            # Analyze portfolio state (shared across all symbols)
            portfolio_state = self.portfolio_manager.analyze_portfolio(
                equity=equity,
                balance=balance,
                free_margin=free_margin,
                positions=positions,
            )

            if portfolio_state.correlation_warnings:
                logger.warning("Portfolio correlation warnings: %s", portfolio_state.correlation_warnings)

            # Reset daily tracking if new day
            today = datetime.now(timezone.utc).date().isoformat()
            if self._last_date != today:
                self.risk_manager.reset_daily(equity)
                self._last_date = today

            # 2. Process each symbol
            results = []
            for symbol in self.settings.symbol_list:
                logger.info("--- Processing %s ---", symbol)
                result = await self._process_symbol(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback=lookback,
                    account_info=account_info,
                    positions=positions,
                    portfolio_state=portfolio_state,
                )
                result["symbol"] = symbol
                results.append(result)

                # Update portfolio state after each symbol (in case of new positions)
                if result.get("executed"):
                    positions = await self.mcp_client.get_positions()
                    portfolio_state = self.portfolio_manager.analyze_portfolio(
                        equity=equity,
                        balance=balance,
                        free_margin=free_margin,
                        positions=positions,
                    )

            # 3. Return combined results
            return {
                "ok": True,
                "cycle": cycle_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "timeframe": timeframe,
                "symbols_processed": len(results),
                "results": results,
                "risk_status": self.risk_manager.get_status(equity),
                "portfolio_status": self.portfolio_manager.get_portfolio_summary(portfolio_state),
            }

        except Exception as e:
            logger.exception("Cycle %d failed: %s", cycle_id, e)
            return {
                "ok": False,
                "action": "ERROR",
                "reason": str(e),
                "cycle": cycle_id,
            }

    async def _process_symbol(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        account_info: Dict[str, Any],
        positions: list,
        portfolio_state: Any,
    ) -> Dict[str, Any]:
        """Process a single symbol within a cycle."""
        try:
            # Fetch candles for TMS (H4)
            logger.info("Fetching %d %s candles for %s...", lookback, timeframe, symbol)
            candles = await self.mcp_client.get_candles(symbol, timeframe, lookback)

            if not candles:
                return {"ok": False, "action": "HOLD", "reason": "no_candles"}

            logger.info("Got %d %s candles for %s", len(candles), timeframe, symbol)

            # Fetch candles for ORB (M15/M5) - separate timeframe
            orb_candles = None
            symbol_orb_config = self._get_symbol_orb_config(symbol)
            if symbol_orb_config is not None:
                orb_tf = symbol_orb_config.timeframe
                orb_lookback = 50  # Get enough M15 candles for ORB calculation
                logger.info("Fetching %d %s candles for ORB...", orb_lookback, orb_tf)
                orb_candles = await self.mcp_client.get_candles(symbol, orb_tf, orb_lookback)
                if orb_candles:
                    logger.info("Got %d %s candles for ORB", len(orb_candles), orb_tf)

            # Fetch current price and spread
            price_data = await self.mcp_client.get_price(symbol)
            
            # Extract spread from price data (if available)
            current_spread = None
            if price_data and "spread" in price_data:
                current_spread = float(price_data["spread"])

            # Build snapshot with TMS (H4) and ORB (M15) indicators
            snapshot = build_snapshot(
                symbol, timeframe, candles,
                price_data=price_data,
                positions=positions,
                account_info=account_info,
                portfolio_state=self.portfolio_manager.get_portfolio_summary(portfolio_state),
                tms_config=self.tms_config,
                orb_config=symbol_orb_config,
                orb_candles=orb_candles,
                current_spread_pips=current_spread,
            )

            if not snapshot.get("ok"):
                return {
                    "ok": False,
                    "action": "HOLD",
                    "reason": snapshot.get("reason", "snapshot_failed"),
                }

            logger.info(
                "%s snapshot: price=%s, trend=%s, TDI=%s/%s, action=%s",
                symbol,
                snapshot.get("price"),
                snapshot.get("ha", {}).get("trend"),
                snapshot.get("tdi", {}).get("green"),
                snapshot.get("tdi", {}).get("red"),
                snapshot.get("action"),
            )

            # Get LLM decision
            user_prompt = build_user_prompt(snapshot)
            logger.info("Calling LLM for %s...", symbol)

            decision = await self.llm_client.analyze(SYSTEM_PROMPT, user_prompt)
            action = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0)
            reason = decision.get("reason", "")

            logger.info("%s LLM decision: %s (confidence: %.2f) - %s", symbol, action, confidence, reason)

            # Process decision
            equity = float(account_info.get("equity", 0))
            result = await self._process_decision(
                action=action,
                confidence=confidence,
                reason=reason,
                decision=decision,
                snapshot=snapshot,
                equity=equity,
                positions=positions,
                symbol=symbol,
                portfolio_state=portfolio_state,
            )

            return result

        except Exception as e:
            logger.exception("Failed to process %s: %s", symbol, e)
            return {
                "ok": False,
                "action": "ERROR",
                "reason": str(e),
            }

    async def _process_decision(
        self,
        action: str,
        confidence: float,
        reason: str,
        decision: Dict[str, Any],
        snapshot: Dict[str, Any],
        equity: float,
        positions: list,
        symbol: str,
        portfolio_state: Any = None,
    ) -> Dict[str, Any]:
        """Process LLM decision and execute if approved."""

        action = action.upper()
        price = snapshot.get("price", 0)

        # HOLD/WAIT — no action
        if action in ("HOLD", "WAIT", "HOLD_BULLISH", "HOLD_BEARISH"):
            return {
                "ok": True,
                "action": action,
                "executed": False,
                "reason": reason,
                "confidence": confidence,
            }

        # EXIT signals — close existing positions
        if action in ("EXIT_LONG", "EXIT_SHORT", "CLOSE"):
            return await self._execute_close(positions, reason)

        # BUY/SELL — open new position
        if action in ("BUY", "SELL"):
            return await self._execute_entry(
                action=action,
                decision=decision,
                snapshot=snapshot,
                equity=equity,
                positions=positions,
                symbol=symbol,
                price=price,
                confidence=confidence,
                reason=reason,
                portfolio_state=portfolio_state,
            )

        return {
            "ok": True,
            "action": action,
            "executed": False,
            "reason": f"Unknown action: {action}",
        }

    async def _execute_entry(
        self,
        action: str,
        decision: Dict[str, Any],
        snapshot: Dict[str, Any],
        equity: float,
        positions: list,
        symbol: str,
        price: float,
        confidence: float,
        reason: str,
        portfolio_state: Any = None,
    ) -> Dict[str, Any]:
        """Execute entry order with risk checks."""

        entry = decision.get("entry", price)
        sl = decision.get("sl")
        tp = decision.get("tp")

        if sl is None:
            return {
                "ok": False,
                "action": action,
                "executed": False,
                "reason": "No stop loss provided",
            }

        # News filter check (API-based)
        news_event = None
        if self.settings.NEWS_FILTER_ENABLED:
            news_event = await self.news_calendar.is_near_news(
                buffer_minutes=self.settings.NEWS_BUFFER_MINUTES,
            )
            if news_event:
                logger.warning("News filter triggered: %s %s at %s",
                             news_event.currency, news_event.event, news_event.timestamp)
                return {
                    "ok": False,
                    "action": action,
                    "executed": False,
                    "reason": f"News filter: {news_event.event} ({news_event.currency}) at {news_event.timestamp.strftime('%H:%M UTC')}",
                    "confidence": confidence,
                }

        # Market anomaly check (AI-based news detection)
        anomaly = snapshot.get("anomaly", {})
        if anomaly.get("detected"):
            anomaly_type = anomaly.get("anomaly_type", "unknown")
            severity = anomaly.get("severity", "low")
            details = anomaly.get("details", "")
            
            # Block trading on high-severity anomalies
            if severity == "high":
                logger.warning("Market anomaly detected: %s - %s", anomaly_type, details)
                return {
                    "ok": False,
                    "action": action,
                    "executed": False,
                    "reason": f"Market anomaly: {anomaly_type} — {details}. Possible news event, avoiding trade.",
                    "confidence": confidence,
                }
            elif severity == "medium":
                # Log warning but allow trading with reduced confidence
                logger.warning("Medium anomaly detected: %s - %s", anomaly_type, details)
                confidence *= 0.7  # Reduce confidence by 30%

        # Risk check
        risk_decision = self.risk_manager.check_trade(
            equity=equity,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            side=action.lower(),
            symbol=symbol,
            current_positions=positions,
            news_event=news_event.to_dict() if news_event else None,
        )

        if not risk_decision.approved:
            logger.warning("Risk check FAILED: %s", risk_decision.reason)
            return {
                "ok": False,
                "action": action,
                "executed": False,
                "reason": f"Risk rejected: {risk_decision.reason}",
                "confidence": confidence,
            }

        # Portfolio risk check
        if portfolio_state is not None:
            portfolio_ok, portfolio_reason = self.portfolio_manager.check_new_trade(
                portfolio=portfolio_state,
                new_symbol=symbol,
                new_side=action.lower(),
                new_volume=risk_decision.volume,
                new_risk_amount=risk_decision.risk_amount,
            )
            if not portfolio_ok:
                logger.warning("Portfolio risk check FAILED: %s", portfolio_reason)
                return {
                    "ok": False,
                    "action": action,
                    "executed": False,
                    "reason": f"Portfolio risk rejected: {portfolio_reason}",
                    "confidence": confidence,
                }

        # Execute order
        volume = risk_decision.volume
        logger.info(
            "Executing %s %s: %.2f lots @ %.5f, SL=%.5f, TP=%s",
            action, symbol, volume, entry, sl, tp or "none",
        )

        order_result = await self.mcp_client.place_order(
            symbol=symbol,
            side=action.lower(),
            volume=volume,
            sl=sl,
            tp=tp,
            comment=f"TMS Agent cycle={self.cycle_count}",
        )

        if order_result.get("error"):
            return {
                "ok": False,
                "action": action,
                "executed": False,
                "reason": f"Order failed: {order_result.get('error')}",
                "order_result": order_result,
            }

        logger.info("Order executed: %s", order_result)

        return {
            "ok": True,
            "action": action,
            "executed": True,
            "order": {
                "symbol": symbol,
                "side": action.lower(),
                "volume": volume,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "risk_amount": risk_decision.risk_amount,
            },
            "order_result": order_result,
            "reason": reason,
            "confidence": confidence,
        }

    async def _execute_close(
        self,
        positions: list,
        reason: str,
    ) -> Dict[str, Any]:
        """Execute close for all positions."""

        if not positions:
            return {
                "ok": True,
                "action": "CLOSE",
                "executed": False,
                "reason": "No positions to close",
            }

        closed = []
        for pos in positions:
            pos_id = pos.get("id") or pos.get("position_id")
            if pos_id:
                result = await self.mcp_client.close_position(
                    position_id=int(pos_id),
                    comment=f"TMS Agent exit: {reason}",
                )
                closed.append({"position_id": pos_id, "result": result})
                logger.info("Closed position %d: %s", pos_id, result)

        return {
            "ok": True,
            "action": "CLOSE",
            "executed": True,
            "closed_positions": closed,
            "reason": reason,
        }

    async def run_once(self) -> Dict[str, Any]:
        """Run a single cycle and return result."""
        return await self.run_cycle()

    async def run_forever(self, cycle_minutes: int = 5) -> None:
        """Run cycles continuously every N minutes."""
        import asyncio

        logger.info("Starting autonomous mode: cycle every %d minutes", cycle_minutes)
        logger.info("Press Ctrl+C to stop")

        while True:
            result = await self.run_cycle()

            # Output result as JSON
            print(json.dumps(result, indent=2, default=str))

            # Wait for next cycle
            logger.info("Waiting %d minutes for next cycle...", cycle_minutes)
            await asyncio.sleep(cycle_minutes * 60)
