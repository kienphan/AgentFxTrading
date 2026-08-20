"""
AgentFxTrading — AI Trading Agent CLI

Analyzes market data from cTrader Remote MCP, calculates technical indicators,
and uses LLM (Qwen/DeepSeek/OpenAI/Anthropic/Gemini) for trading decisions.

Usage:
    python agent.py --once                    # Single analysis
    python agent.py --cycle 5                 # Run every 5 minutes
    python agent.py --once --dry-run          # Print prompts, don't call LLM
    python agent.py --once --verbose          # Debug logging
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import get_settings, LLMProvider
from app.core.logger import get_logger, setup_logging
from app.agent.client import create_client
from app.agent.portfolio import PortfolioConfig
from app.agent.risk import RiskConfig
from app.agent.snapshot import MCPClient
from app.agent.trader import AgentTrader
from app.indicators.orb import ORBConfig
from app.indicators.tms import TMSConfig


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="agent",
        description="AI Trading Agent — analyzes markets via cTrader Remote MCP + LLM",
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run single analysis cycle then exit",
    )
    ap.add_argument(
        "--cycle",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Run continuously every N minutes",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts without calling LLM",
    )
    ap.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    ap.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Override symbols (comma-separated, e.g., XAUUSD,EURUSD)",
    )
    ap.add_argument(
        "--timeframe",
        type=str,
        default=None,
        help="Override timeframe (default: from .env or H1)",
    )
    return ap.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    """Main async entry point."""
    # Load settings
    settings = get_settings()

    # Override settings from args
    if args.symbols:
        settings.SYMBOLS = args.symbols
    if args.timeframe:
        settings.TIMEFRAME = args.timeframe

    # Setup logging
    log_level = "DEBUG" if args.verbose else settings.LOG_LEVEL
    setup_logging(log_level=log_level)
    log = get_logger("agent")

    log.info("=" * 60)
    log.info("  AgentFxTrading Starting")
    log.info("  Symbols: {}", settings.symbol_list)
    log.info("  Timeframe: {}", settings.TIMEFRAME)
    log.info("  LLM: {} ({})", settings.LLM_PROVIDER.value, settings.LLM_MODEL)
    log.info("=" * 60)

    # Validate config
    if not settings.CTRADER_MCP_URL:
        log.error("CTRADER_MCP_URL not set. Configure .env with cTrader Remote MCP URL.")
        log.info("See README.md for setup instructions.")
        return 1

    if not settings.LLM_API_KEY:
        log.error("LLM_API_KEY not set. Configure .env with your LLM API key.")
        return 1

    # Create TMS config from settings
    tms_config = TMSConfig(
        rsi_period=settings.TMS_RSI_PERIOD,
        red_period=settings.TMS_RED_PERIOD,
        red_method=settings.TMS_RED_METHOD,
        stoch_k_period=settings.TMS_STOCH_K,
        stoch_d_period=settings.TMS_STOCH_D,
        stoch_slowing=settings.TMS_STOCH_SLOWING,
        stoch_confirm_mode=settings.TMS_STOCH_MODE,
        max_bars_after_cross=settings.TMS_MAX_BARS_AFTER_CROSS,
        min_angle_delta=settings.TMS_MIN_ANGLE_DELTA,
        flat_threshold=settings.TMS_FLAT_THRESHOLD,
    )

    # Create ORB config from settings
    orb_config = None
    if settings.ORB_ENABLED:
        orb_config = ORBConfig(
            timeframe=settings.ORB_TIMEFRAME,
            session=settings.ORB_DEFAULT_SESSION,
            session_start_hour=settings.ORB_DEFAULT_HOUR,
            session_start_minute=settings.ORB_DEFAULT_MINUTE,
            or_candles=settings.ORB_CANDLES,
            min_or_width=settings.ORB_MIN_WIDTH,
            buffer_points=settings.ORB_BUFFER,
            max_bars_after_breakout=settings.ORB_MAX_BARS,
        )

    # Create risk config from settings
    risk_config = RiskConfig(
        risk_per_trade_pct=settings.RISK_PER_TRADE_PCT / 100.0,
        max_daily_loss_pct=settings.MAX_DAILY_LOSS_PCT / 100.0,
        max_drawdown_pct=settings.MAX_DRAWDOWN_PCT / 100.0,
        max_positions=settings.MAX_POSITIONS,
        max_positions_per_symbol=settings.MAX_POSITIONS_PER_SYMBOL,
        min_rr_ratio=settings.MIN_RR_RATIO,
        max_spread_pips=settings.MAX_SPREAD_PIPS,
        min_sl_atr_multiple=settings.MIN_SL_ATR_MULTIPLE,
        max_atr_percentile=settings.MAX_ATR_PERCENTILE,
        news_filter_enabled=settings.NEWS_FILTER_ENABLED,
        news_buffer_minutes=settings.NEWS_BUFFER_MINUTES,
        symbol_risk_overrides=settings.SYMBOL_RISK_OVERRIDES,
    )

    # Create portfolio config from settings
    portfolio_config = PortfolioConfig(
        max_total_heat_pct=settings.MAX_PORTFOLIO_HEAT_PCT,
        max_margin_usage_pct=settings.MAX_MARGIN_USAGE_PCT,
        max_same_currency_exposure=settings.MAX_SAME_CURRENCY_EXPOSURE,
        max_correlated_positions=settings.MAX_CORRELATED_POSITIONS,
        correlation_threshold=settings.CORRELATION_THRESHOLD,
    )

    # Create clients
    llm_client = create_client(
        provider=settings.LLM_PROVIDER,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        base_url=settings.LLM_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
    )

    mcp_client = MCPClient(settings.CTRADER_MCP_URL, settings.CTRADER_MCP_TOKEN)

    try:
        # Connect to MCP
        log.info("Connecting to cTrader Remote MCP...")
        await mcp_client.connect()

        # Create trader
        trader = AgentTrader(settings, llm_client, mcp_client, tms_config, risk_config, portfolio_config, orb_config)

        if args.dry_run:
            log.info("DRY RUN mode — printing snapshots without calling LLM")
            # Fetch and print snapshots for all symbols
            from app.agent.snapshot import build_snapshot
            for symbol in settings.symbol_list:
                candles = await mcp_client.get_candles(symbol, settings.TIMEFRAME, settings.LOOKBACK_BARS)
                snapshot = build_snapshot(symbol, settings.TIMEFRAME, candles, tms_config=tms_config, orb_config=orb_config)
                print(f"\n=== {symbol} ===")
                print(json.dumps(snapshot, indent=2, default=str))
            return 0

        if args.once:
            # Single cycle
            result = await trader.run_once()
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("ok") else 1

        if args.cycle > 0:
            # Continuous mode
            await trader.run_forever(args.cycle)
            return 0

        # Default: single cycle
        result = await trader.run_once()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    except KeyboardInterrupt:
        log.info("Interrupted by user")
        return 0
    except Exception as e:
        log.exception("Fatal error: %s", e)
        return 1
    finally:
        await mcp_client.disconnect()


def main() -> int:
    """Main entry point."""
    load_dotenv()
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
