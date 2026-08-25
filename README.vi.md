# AgentFxTrading

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

Hệ thống giao dịch tự động sử dụng AI Agent tích hợp với cTrader cBot, triển khai chiến lược TMS (Trend Momentum Signal) + ORB (Opening Range Breakout).

## Kiến trúc

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • Tính TMS     │      JSON Response  │  • Xây dựng prompt│
│  • Tính ORB     │                     │  • Gọi LLM       │
│  • Gửi snapshot │                     │  • Phân tích quyết định│
└─────────────────┘                     └──────────────────┘
                                                   │
                                                   ▼
                                        ┌──────────────────┐
                                        │   LLM Provider   │
                                        │  • Qwen          │
                                        │  • OpenAI        │
                                        │  • Claude        │
                                        │  • Gemini        │
                                        │  • DeepSeek      │
                                        └──────────────────┘
```

## Tính năng

### cBot (C#)
- **Chỉ báo TMS**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **Logic ORB**: Phát hiện Opening Range, phát hiện breakout
- **Trạng thái TF Green**: Theo dõi momentum (giá trị + độ dốc)
- **Bộ nhớ vị thế**: MFE (Maximum Favorable Excursion), theo dõi giveback
- **Quản lý thoát tự động**: Breakeven, trailing stop, max giveback
- **Quản lý phiên**: Các giai đoạn phiên, tự động đóng cuối ngày
- **Guardrails**: Bảo vệ chuỗi thua, thoát khi bias đảo chiều, kiểm tra breakout quyết định

### Server (Python)
- **Trừu tượng hóa LLM**: Hỗ trợ Qwen, OpenAI, Claude, Gemini, DeepSeek
- **Logic chiến lược**: Căn chỉnh bias TMS + breakout ORB
- **Quy tắc quyết định**: Điều kiện vào/ra, quản lý rủi ro
- **Phản hồi JSON**: Quyết định giao dịch có cấu trúc

## Cài đặt

### 1. Phụ thuộc Python

```bash
pip install -r requirements.txt
```

### 2. Cấu hình LLM Provider

Sao chép `.env.example` thành `.env` và cấu hình:

#### Qwen (Khuyến nghị - Hiệu quả chi phí)
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

#### Gemini
```bash
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your-google-api-key
LLM_MODEL=gemini-1.5-flash
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
2. Tạo bot mới, dán code từ `cBot/AiAgentBot.cs`
3. Build và attach vào chart (M15 hoặc H1)
4. Cấu hình tham số:
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7 (London), Opening Range=15 phút
   - **Session**: End Hour=16 (London đóng cửa)
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

Bot sẽ tự động gọi API mỗi khi nến đóng và thực thi quyết định từ AI.

## Cấu trúc dự án

```
.
├── app/
│   ├── llm_client.py      # Lớp trừu tượng hóa LLM
│   ├── server.py          # FastAPI server (bộ não AI)
│   └── portfolio.py       # Quản lý rủi ro danh mục
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot (bộ thực thi)
├── .env.example           # Mẫu biến môi trường
├── requirements.txt       # Phụ thuộc Python
└── README.vi.md
```

## Chiến lược giao dịch

### TMS (Trend Momentum Signal) - Xác định Bias
- **BULLISH**: Green cắt lên trên Red + HA green + Stoch K > D
- **BEARISH**: Green cắt xuống dưới Red + HA red + Stoch K < D
- Bias được khóa cho đến khi có cắt tiếp theo

### ORB (Opening Range Breakout) - Trigger vào lệnh
- **Opening Range**: High/Low của N nến đầu phiên (mặc định London 7:00-7:15 UTC)
- **Breakout**: Giá đóng cửa trên OR High (bullish) hoặc OR Low (bearish)
- **Decisive**: Breakout phải đủ mạnh (>= 3 pips) để tránh false breakout

### Quy tắc vào lệnh
1. TMS BULLISH + ORB breakout UP + decisive → BUY
2. TMS BEARISH + ORB breakout DOWN + decisive → SELL
3. Mismatch hoặc không decisive → HOLD

### Quy tắc thoát lệnh
- **TDI Exit**: Green flat/hook/checkmark → CLOSE_ALL
- **Bias Flip**: Bias đảo chiều → tự động đóng
- **Session End**: Kết thúc phiên → tự động đóng
- **Breakeven**: Lợi nhuận >= 5p → dời SL về entry
- **Trailing**: Lợi nhuận >= 10p → trail SL 5p
- **Max Giveback**: Giveback >= ngưỡng → tự động đóng

### Guardrails
- Chuỗi thua >= 3 → chặn vào lệnh
- ORB ngược hướng → chặn vào lệnh
- SL/TP được kẹp trong [Min, Max]

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
Chỉnh sửa `SYSTEM_PROMPT` trong `app/server.py` để điều chỉnh logic giao dịch.

### Thêm LLM Provider mới
Thêm class mới trong `app/llm_client.py` kế thừa `LLMClient` và cập nhật `create_llm_client()`.

### Multi-symbol
Chạy nhiều cBot instances trên các chart khác nhau, mỗi bot gọi cùng server.

### Backtest
1. Attach cBot vào chart với Visual Mode
2. Server sẽ nhận request và trả về quyết định
3. Xem lại logs để đánh giá chiến lược

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
- `Min Angle Delta`: 0.0 (tắt)
- `Min Decisive Breakout`: 3.0 pips

### Exit
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 pips
- `Trail Trigger`: 10.0 pips
- `Trail Distance`: 5.0 pips
- `Max Giveback`: 0.0 (tắt)

### ORB
- `Session Start Hour`: 7 (UTC)
- `Opening Range`: 15 phút
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
