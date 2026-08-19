# AgentFxTrading

**Autonomous AI Trading Agent** for Forex and Commodities.

Combines **TMS (Traders Dynamic Index)** strategy with **ORB (Opening Range Breakout)** confirmation, powered by LLM (Qwen/DeepSeek/OpenAI/Anthropic/Gemini) for autonomous trading via cTrader Remote MCP.

## Features

### Core Strategy
- **TMS (H1)** — Directional bias using TDI (Green/Red lines), Heiken Ashi, and Stochastic
- **ORB (M15)** — Entry confirmation using Opening Range Breakout from London session
- **Multi-symbol** — Trade multiple pairs simultaneously (XAUUSD, EURUSD, GBPUSD, USDJPY, etc.)
- **Session filtering** — Only trade during London (07:00-16:00 UTC) and New York (12:00-21:00 UTC) sessions

### Risk Management
- **Position sizing** — Automatic calculation based on account equity and risk %
- **ATR-based SL validation** — Prevents liquidity sweeps (SL must be ≥ 1.5x ATR)
- **Spread filter** — Skip trades when spread is too wide
- **Volatility filter** — Skip trades during extreme volatility (ATR > 90th percentile)
- **Portfolio risk** — Correlation checks, currency exposure limits, total heat management
- **News filter** — AI-based detection of market anomalies (volatility spikes, spread widening, volume spikes)

### AI Integration
- **Multi-provider support** — Qwen, DeepSeek, OpenAI, Anthropic, Gemini
- **Autonomous execution** — Agent analyzes, decides, and executes trades automatically
- **cTrader Remote MCP** — Direct integration with cTrader broker

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        agent.py (CLI)                                │
│                                                                      │
│  python agent.py --once          # Single analysis                   │
│  python agent.py --cycle 60      # Run every 60 minutes              │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
┌───────────────┐      ┌───────────────────┐      ┌─────────────────┐
│  cTrader MCP  │      │   TMS Indicator   │      │  Risk Manager   │
│               │      │                   │      │                 │
│ • get_candles │      │ • Heiken Ashi     │      │ • Position size │
│ • get_balance │      │ • TDI Green/Red   │      │ • Daily loss    │
│ • place_order │      │ • Stochastic      │      │ • Max drawdown  │
│ • close_pos   │      │ • Bias detection  │      │ • ATR validation│
└───────────────┘      └───────────────────┘      └─────────────────┘
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   │
                                   ▼
                        ┌───────────────────┐
                        │   ORB Indicator   │
                        │                   │
                        │ • Opening Range   │
                        │ • Breakout detect │
                        │ • Entry trigger   │
                        └───────────────────┘
                                   │
                                   ▼
                        ┌───────────────────┐
                        │   LLM (Qwen)      │
                        │                   │
                        │ • TMS bias + ORB  │
                        │ • Decision making │
                        │ • Entry/SL/TP     │
                        └───────────────────┘
                                   │
                                   ▼
                        ┌───────────────────┐
                        │   EXECUTE ORDER   │
                        │   (via MCP)       │
                        └───────────────────┘
```

## Strategy Logic

### TMS = BIAS (H1 timeframe)
- **BULLISH**: TDI Green > Red + HA green + Stoch K > D
- **BEARISH**: TDI Green < Red + HA red + Stoch K < D
- **NEUTRAL**: Mixed signals → NO TRADE

Bias is **locked** at the last TDI cross and stays until the next cross in the opposite direction.

### ORB = ENTRY (M15 timeframe)
- Opening Range = First 15 minutes of London session (07:00-07:15 UTC)
- **Only enter when ORB breaks in direction of TMS bias**

| TMS Bias | ORB Breakout | Action |
|----------|--------------|--------|
| BULLISH | UP | ✅ BUY |
| BEARISH | DOWN | ✅ SELL |
| BULLISH | DOWN | ❌ HOLD (counter-trend) |
| BEARISH | UP | ❌ HOLD (counter-trend) |
| NEUTRAL | Any | ❌ HOLD (no bias) |

## Installation

```bash
# Clone repository
git clone https://github.com/yourusername/AgentFxTrading.git
cd AgentFxTrading

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

## Configuration

### 1. cTrader Remote MCP

1. Login to **cTrader Web** → **Settings** → **Remote MCP**
2. Copy the configuration (URL + Bearer token)
3. Add to `.env`:

```env
CTRADER_MCP_URL=https://mcp.ctrader.com/trading/mcp
CTRADER_MCP_TOKEN=your_bearer_token_here
```

### 2. LLM Provider

**Qwen (default):**
```env
LLM_PROVIDER=qwen
LLM_API_KEY=sk-xxx
LLM_MODEL=qwen-max
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

**DeepSeek:**
```env
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

**Other providers:** OpenAI, Anthropic, Gemini (see `.env.example`)

### 3. Trading Configuration

```env
# Symbols to trade
SYMBOLS=XAUUSD,EURUSD,GBPUSD,USDJPY
TIMEFRAME=H1              # TMS timeframe
ORB_TIMEFRAME=M15         # ORB timeframe

# Trading sessions (UTC)
TRADING_SESSIONS=london,newyork
LONDON_START=7
LONDON_END=16
NEWYORK_START=12
NEWYORK_END=21
```

### 4. Risk Management

```env
# Basic risk
RISK_PER_TRADE_PCT=1.0        # Risk 1% per trade
MAX_DAILY_LOSS_PCT=3.0        # Stop if daily loss > 3%
MAX_DRAWDOWN_PCT=10.0         # Stop if drawdown > 10%
MAX_POSITIONS=3               # Max concurrent positions
MIN_RR_RATIO=1.5              # Minimum risk/reward ratio

# XAUUSD protection
MAX_SPREAD_PIPS=5.0           # Skip if spread > 5 pips
MIN_SL_ATR_MULTIPLE=1.5       # SL must be >= 1.5x ATR
SYMBOL_RISK_OVERRIDES=XAUUSD:0.5:50  # XAUUSD: 0.5% risk, 50 pip max SL
```

### 5. Per-Symbol ORB Sessions

```env
# Different sessions for different symbols
ORB_SYMBOL_SESSIONS=USDJPY:tokyo:0,USDCAD:newyork:12
```

## Usage

### Single Analysis
```bash
python agent.py --once
```

### Continuous Mode
```bash
# Run every 60 minutes
python agent.py --cycle 60
```

### Dry Run (No Execution)
```bash
python agent.py --once --dry-run
```

### Override Symbols
```bash
python agent.py --once --symbols XAUUSD,EURUSD
```

### Verbose Logging
```bash
python agent.py --once --verbose
```

## Output Example

```json
{
  "ok": true,
  "cycle": 1,
  "symbol": "XAUUSD",
  "timeframe": "H1",
  "session": "london",
  "action": "BUY",
  "executed": true,
  "order": {
    "symbol": "XAUUSD",
    "side": "buy",
    "volume": 0.05,
    "entry": 2350.50,
    "sl": 2340.00,
    "tp": 2370.00,
    "risk_amount": 50.00
  },
  "tms_bias": "BULLISH",
  "orb_breakout": "UP",
  "alignment": "ALIGNED",
  "risk_status": {
    "equity": 10000.00,
    "daily_pnl": 0.0,
    "drawdown_pct": 0.0
  }
}
```

## Project Structure

```
AgentFxTrading/
├── agent.py                 # CLI entry point
├── app/
│   ├── agent/
│   │   ├── client.py        # LLM client (multi-provider)
│   │   ├── portfolio.py     # Portfolio risk management
│   │   ├── prompt.py        # TMS + ORB system prompt
│   │   ├── risk.py          # Trade risk management
│   │   ├── snapshot.py      # MCP client + snapshot builder
│   │   └── trader.py        # Autonomous trader
│   ├── core/
│   │   ├── config.py        # Settings (Pydantic)
│   │   └── logger.py        # Logging (Loguru)
│   ├── indicators/
│   │   ├── orb.py           # ORB indicator (M15)
│   │   └── tms.py           # TMS indicator (H1)
│   └── news/
│       ├── anomaly.py       # AI-based news detection
│       └── calendar.py      # API-based news calendar
├── .env.example             # Environment template
├── requirements.txt         # Python dependencies
└── README.md                # This file
```

## Risk Disclaimer

⚠️ **Trading involves substantial risk of loss. This software is provided for educational purposes only.**

- Always test on demo accounts before live trading
- Never risk more than you can afford to lose
- Past performance does not guarantee future results
- The authors are not responsible for any trading losses

## License

MIT License - see LICENSE file for details

## Credits

- **TMS Strategy** — Based on "Best of Big E I & II" by eelfranz (Forex Factory)
- **cTrader Remote MCP** — Official cTrader API integration
- **TDI Indicator** — Traders Dynamic Index

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## Support

For questions or issues, please open a GitHub issue.
