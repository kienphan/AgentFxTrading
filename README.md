# AgentFxTrading

Hệ thống giao dịch tự động sử dụng AI Agent kết hợp cTrader cBot với chiến lược TMS (Trend Momentum Signal) + ORB (Opening Range Breakout).

## Kiến trúc

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • Tính TMS     │      JSON Response  │  • Build prompt  │
│  • Tính ORB     │                     │  • Gọi LLM       │
│  • Gửi snapshot │                     │  • Parse decision│
└─────────────────┘                     └──────────────────┘
                                                   │
                                                   ▼
                                        ┌──────────────────┐
                                        │   LLM Provider   │
                                        │  • Qwen (DashScope)│
                                        │  • OpenAI        │
                                        │  • Claude        │
                                        │  • DeepSeek      │
                                        └──────────────────┘
```

## Features

### cBot (C#)
- **TMS Indicators**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **ORB Logic**: Opening Range detection, breakout detection
- **TF Green State**: Momentum tracking (value + slope)
- **Position Memory**: MFE (Maximum Favorable Excursion), giveback tracking
- **Auto Exit Management**: Breakeven, trailing stop, max giveback
- **Session Management**: Session phases, EOD auto-close
- **Guardrails**: Loss streak protection, bias flip exit, decisive breakout check

### Server (Python)
- **LLM Abstraction**: Support Qwen, OpenAI, Claude, DeepSeek
- **Strategy Logic**: TMS bias + ORB breakout alignment
- **Decision Rules**: Entry/exit conditions, risk management
- **JSON Response**: Structured trading decisions

## Cài đặt

### 1. Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Cấu hình LLM Provider

Copy `.env.example` thành `.env` và cấu hình:

#### Qwen (Recommended - Cost efficient)
```bash
LLM_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-your-dashscope-key
LLM_MODEL=qwen-max
```

#### OpenAI
```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-key
LLM_MODEL=gpt-4o-mini
```

#### Claude
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key
LLM_MODEL=claude-3-5-sonnet-20241022
```

#### DeepSeek
```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-key
LLM_MODEL=deepseek-chat
```

## Chạy Server

```bash
python app/server.py
```

Server sẽ chạy tại `http://127.0.0.1:8000`

## Chạy cBot

1. Mở cTrader → Automate
2. Tạo bot mới, paste code từ `cBot/AiAgentBot.cs`
3. Build và attach vào chart (M15 hoặc H1)
4. Cấu hình parameters:
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7 (London), Opening Range=15 minutes
   - **Session**: End Hour=16 (London close)
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

Bot sẽ tự động gọi API mỗi khi nến đóng và thực thi quyết định từ AI.

## Cấu trúc Project

```
.
├── app/
│   ├── llm_client.py      # LLM abstraction layer
│   └── server.py          # FastAPI server (bộ não AI)
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot (người thực thi)
├── .env.example           # Template biến môi trường
├── requirements.txt       # Python dependencies
└── README.md
```

## Chiến lược Giao dịch

### TMS (Trend Momentum Signal) - Xác định Bias
- **BULLISH**: Green cắt lên Red + HA green + Stoch K > D
- **BEARISH**: Green cắt xuống Red + HA red + Stoch K < D
- Bias được khóa cho đến khi có cross ngược lại

### ORB (Opening Range Breakout) - Trigger Entry
- **Opening Range**: High/Low của N nến đầu phiên (mặc định London 7:00-7:15 UTC)
- **Breakout**: Giá đóng cửa vượt OR High (bullish) hoặc OR Low (bearish)
- **Decisive**: Breakout phải đủ mạnh (>= 3 pips) để tránh false breakout

### Entry Rules
1. TMS BULLISH + ORB breakout UP + decisive → BUY
2. TMS BEARISH + ORB breakout DOWN + decisive → SELL
3. Mismatch hoặc không decisive → HOLD

### Exit Rules
- **TDI Exit**: Green flat/hook/checkmark → CLOSE_ALL
- **Bias Flip**: Bias đảo chiều → auto close
- **Session End**: Hết phiên → auto close
- **Breakeven**: Profit >= 5p → dời SL về entry
- **Trailing**: Profit >= 10p → trail SL 5p
- **Max Giveback**: Giveback >= threshold → auto close

### Guardrails
- Loss streak >= 3 → block entry
- ORB ngược chiều → block entry
- SL/TP clamped vào [Min, Max]

## API Endpoint

### POST /trade

**Request** (từ cBot):
```json
{
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "ask": 2450.15,
  "bid": 2450.10,
  "bars": [
    {"ha_color": "Green", "tdi_green": 55.2, "tdi_red": 52.1, "stoch_k": 75.0, "stoch_d": 70.0},
    {"ha_color": "Green", "tdi_green": 54.8, "tdi_red": 51.9, "stoch_k": 72.0, "stoch_d": 68.0},
    {"ha_color": "Red", "tdi_green": 53.5, "tdi_red": 52.5, "stoch_k": 65.0, "stoch_d": 62.0}
  ],
  "tms": {
    "bias": "BULLISH",
    "bars_since_cross": 2,
    "cross_direction": "up",
    "long_entry": true,
    "short_entry": false,
    "exit_long": false,
    "exit_short": false,
    "green_tf_value": 55.2,
    "green_tf_slope": 0.4
  },
  "orb": {
    "or_high": 2448.50,
    "or_low": 2445.20,
    "or_complete": true,
    "breakout_direction": "up",
    "breakout_distance_pips": 16.5,
    "is_decisive": true,
    "bars_since_breakout": 1,
    "in_entry_window": true
  },
  "position": null,
  "session": {
    "session_name": "london",
    "phase": "active",
    "minutes_to_end": 180,
    "is_trading_time": true
  },
  "loss_streak": 0,
  "day_pnl": 0,
  "trades_today": 0
}
```

**Response** (từ AI):
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+16.5p), momentum rising"
}
```

## Phát triển

### Cải thiện Prompt
Edit `SYSTEM_PROMPT` trong `app/server.py` để điều chỉnh logic giao dịch.

### Thêm LLM Provider mới
Thêm class mới trong `app/llm_client.py` kế thừa `LLMClient` và update `create_llm_client()`.

### Multi-symbol
Chạy nhiều cBot instances trên các chart khác nhau, mỗi bot gọi cùng server.

### Backtest
1. Attach cBot vào chart với Visual Mode
2. Server sẽ nhận request và trả về decision
3. Review logs để đánh giá chiến lược

## Tham số cBot

### TDI
- `RSI Period`: 6 (mặc định)
- `Red Period`: 6 (mặc định)

### Stochastic
- `%K Period`: 6
- `%D Period`: 6
- `Slowing`: 4

### Entry
- `Max Bars After Cross`: 5
- `Min Angle Delta`: 0.0 (off)
- `Min Decisive Breakout`: 3.0 pips

### Exit
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 pips
- `Trail Trigger`: 10.0 pips
- `Trail Distance`: 5.0 pips
- `Max Giveback`: 0.0 (off)

### ORB
- `Session Start Hour`: 7 (UTC)
- `Opening Range`: 15 minutes
- `Min OR Width`: 2.0 pips
- `Max Bars After Breakout`: 5

### Session
- `Session End Hour`: 16 (UTC)
- `Session Name`: "london"

### Guardrails
- `Min SL`: 3.0 pips
- `Max SL`: 30.0 pips
- `Max Loss Streak`: 3
- `Bias Flip Exit`: true

## License

MIT
