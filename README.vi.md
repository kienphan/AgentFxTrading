# 🤖 AgentFxTrading - Hệ Thống Giao Dịch Tự Động Với AI

<div align="center">

**Giao Dịch Forex Tự Động Với Chiến Lược TMS + ORB**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![cTrader](https://img.shields.io/badge/cTrader-4.x+-green.svg)](https://ctdn.com/)
[![Stars](https://img.shields.io/github/stars/yourusername/AgentFxTrading?style=social)](https://github.com/yourusername/AgentFxTrading/stargazers)
[![Forks](https://img.shields.io/github/forks/yourusername/AgentFxTrading?style=social)](https://github.com/yourusername/AgentFxTrading/network/members)
[![Issues](https://img.shields.io/github/issues/yourusername/AgentFxTrading)](https://github.com/yourusername/AgentFxTrading/issues)

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

[Cài Đặt](#-hướng-dẫn-cài-đặt) • [Tính Năng](#-tính-năng) • [Chiến Lược](#-chiến-lược-giao-dịch) • [API Docs](#-api-documentation) • [Đóng Góp](#-đóng-góp)

</div>

---

## 📋 Mục Lục

- [Tổng Quan](#-tổng-quan)
- [Tính Năng](#-tính-năng)
- [Kiến Trúc](#-kiến-trúc)
- [Hướng Dẫn Cài Đặt](#-hướng-dẫn-cài-đặt)
- [Chiến Lược Giao Dịch](#-chiến-lược-giao-dịch)
- [Cấu Hình](#-cấu-hình)
- [API Documentation](#-api-documentation)
- [Phát Triển](#-phát-triển)
- [Hiệu Suất](#-hiệu-suất)
- [Đóng Góp](#-đóng-góp)
- [Giấy Phép](#-giấy-phép)

---

## 🎯 Tổng Quan

AgentFxTrading là **hệ thống giao dịch forex tự động** kết hợp sức mạnh của AI với các chiến lược phân tích kỹ thuật đã được chứng minh. Hệ thống sử dụng **TMS (Trend Momentum Signal)** để phát hiện xu hướng và **ORB (Opening Range Breakout)** để xác định thời điểm vào lệnh chính xác.

### Tại Sao Chọn AgentFxTrading?

✅ **Hoàn Toàn Tự Động** - AI đưa ra quyết định giao dịch 24/7  
✅ **Hỗ Trợ Đa LLM** - Hoạt động với Qwen, OpenAI, Claude, Gemini, DeepSeek  
✅ **Quản Lý Rủi Ro** - Kiểm soát rủi ro cấp độ danh mục trên nhiều cặp tiền  
✅ **Chiến Lược Đã Chứng Minh** - Dựa trên phương pháp TMS chuyên nghiệp  
✅ **Dễ Dàng Cài Đặt** - Bắt đầu trong vòng 10 phút  
✅ **Mã Nguồn Mở** - Hoàn toàn minh bạch và tùy chỉnh được  

---

## 🚀 Tính Năng

### 🤖 Ra Quyết Định Bằng AI
- **Hỗ Trợ Đa LLM**: Qwen, OpenAI GPT-4, Claude, Gemini, DeepSeek
- **Phân Tích Theo Ngữ Cảnh**: Phân tích 3 nến dữ liệu lịch sử
- **Điểm Tin Cậy**: Chỉ giao dịch khi độ tin cậy > 70%
- **Học Thích Ứng**: Prompt engineering để cải thiện liên tục

### 📊 Phân Tích Kỹ Thuật Nâng Cao
- **Chỉ Báo TMS**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **Logic ORB**: Phát hiện Opening Range với bộ lọc breakout quyết định
- **Theo Dõi Momentum**: TF Green State với phân tích độ dốc
- **Đa Khung Thời Gian**: Hoạt động trên M15, H1, H4

### 💼 Quản Lý Danh Mục
- **Giao Dịch Đa Symbol**: Chạy nhiều bot trên các cặp tiền khác nhau
- **Kiểm Soát Phơi Nhiễm Tiền Tệ**: Ngăn chặn phơi nhiễm quá mức vào một tiền tệ
- **Phát Hiện Tương Quan**: Chặn các vị thế có tương quan cao
- **Giới Hạn Lỗ Hàng Ngày**: Tự động dừng giao dịch sau khi lỗ tối đa

### 🛡️ Quản Lý Rủi Ro
- **Bộ Nhớ Vị Thế**: Theo dõi MFE (Maximum Favorable Excursion)
- **Breakeven Tự Động**: Di chuyển SL về entry sau ngưỡng lợi nhuận
- **Trailing Stop**: Điều chỉnh SL động trong các giao dịch có lãi
- **Bảo Vệ Giveback Tối Đa**: Đóng vị thế nếu giveback vượt ngưỡng
- **Bảo Vệ Chuỗi Thua**: Chặn vào lệnh sau 3 lần thua liên tiếp

### ⏰ Quản Lý Phiên
- **Phiên Giao Dịch**: Thời gian phiên có thể cấu hình (London, NY, Tokyo)
- **Tự Động Đóng Cuối Ngày**: Tự động đóng vị thế khi kết thúc phiên
- **Phát Hiện Giai Đoạn**: Pre-market, active, ending, closed

---

## 🏗️ Kiến Trúc

```mermaid
graph LR
    A[cTrader cBot<br/>C#] -->|HTTP POST| B[FastAPI Server<br/>Python]
    B -->|JSON Response| A
    B --> C{LLM Provider}
    C --> D[Qwen]
    C --> E[OpenAI]
    C --> F[Claude]
    C --> G[Gemini]
    C --> H[DeepSeek]
    B --> I[(SQLite<br/>Portfolio DB)]
```

### Phân Tích Component

| Component | Công Nghệ | Trách Nhiệm |
|-----------|-----------|----------------|
| **cBot** | C# / cTrader | Tính toán chỉ báo, thực thi giao dịch |
| **Server** | Python / FastAPI | Ra quyết định AI, quản lý rủi ro |
| **Database** | SQLite | Theo dõi danh mục, lịch sử vị thế |
| **LLM** | Nhiều loại | Phân tích quyết định giao dịch |

---

## ⚡ Hướng Dẫn Cài Đặt

### Yêu Cầu Hệ Thống

- Python 3.9+
- cTrader 4.x+
- API key LLM (Qwen/OpenAI/Claude/Gemini/DeepSeek)

### 1. Cài Đặt Dependencies Python

```bash
# Clone repository
git clone https://github.com/yourusername/AgentFxTrading.git
cd AgentFxTrading

# Cài đặt dependencies
pip install -r requirements.txt
```

### 2. Cấu Hình LLM Provider

```bash
# Copy template môi trường
cp .env.example .env

# Chỉnh sửa .env với API key của bạn
# Ví dụ cho Qwen (khuyến nghị):
LLM_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-your-dashscope-key
LLM_MODEL=qwen-max
```

### 3. Khởi Động Server

```bash
python app/server.py
```

Server sẽ chạy tại `http://127.0.0.1:8000`

### 4. Thiết Lập cBot

1. Mở **cTrader** → **Automate**
2. Click **New** → **cBot**
3. Dán code từ `cBot/AiAgentBot.cs`
4. Click **Build**
5. Attach vào chart (khuyến nghị M15 hoặc H1)
6. Cấu hình parameters:
   - **Bot ID**: `bot1` (identifier duy nhất)
   - **API URL**: `http://127.0.0.1:8000/trade`
   - **Session**: New York (13:00-21:00 UTC) / London (8:00-17:00 UTC)

### 5. Bắt Đầu Giao Dịch! 🎉

Bot sẽ tự động:
- Tính toán chỉ báo khi mỗi nến đóng
- Gửi snapshot thị trường đến AI server
- Nhận quyết định giao dịch
- Thực thi giao dịch với quản lý rủi ro

---

## 📈 Chiến Lược Giao Dịch

### TMS (Trend Momentum Signal)

TMS xác định **directional bias** bằng ba xác nhận:

| Chỉ Báo | Tín Hiệu Bullish | Tín Hiệu Bearish |
|----------|------------------|------------------|
| **TDI** | Green > Red | Green < Red |
| **Heiken Ashi** | Nến xanh | Nến đỏ |
| **Stochastic** | K > D | K < D |

**Khái Niệm Chính**: Bias được khóa cho đến khi có cross tiếp theo, ngăn chặn whipsaws.

### ORB (Opening Range Breakout)

ORB cung cấp **thời điểm vào lệnh chính xác**:

1. **Opening Range**: High/Low của 15 phút đầu phiên
2. **Breakout**: Giá đóng cửa vượt qua ranh giới OR
3. **Bộ Lọc Quyết Định**: Breakout phải dứt khoát (≥ MinDecisiveBreakoutPips, mặc định 10.0 pips cho XAUUSD)

### Quy Tắc Vào Lệnh

```
IF TMS_BULLISH AND ORB_BREAKOUT_UP AND DECISIVE:
    → BUY
    
IF TMS_BEARISH AND ORB_BREAKOUT_DOWN AND DECISIVE:
    → SELL
    
ELSE:
    → HOLD
```

### Quy Tắc Thoát Lệnh

| Điều Kiện | Hành Động |
|-----------|-----------|
| TDI Green flat/hook/checkmark | CLOSE_ALL |
| Bias đảo ngược | Tự động đóng |
| Phiên kết thúc (EOD) | Tự động đóng toàn bộ lệnh (EOD Force-Flatten safety net) |
| Lợi nhuận ≥ 30p | Di chuyển SL về breakeven (+2p offset) |
| Lợi nhuận ≥ 50p | Trail SL 25p |
| Giveback ≥ 30p | Tự động đóng (Max giveback protection) |

---

## ⚙️ Cấu Hình

### Tham Số cBot

#### Cài Đặt TDI
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| RSI Period | 6 | Chu kỳ tính toán RSI |
| Red Period | 6 | Chu kỳ đường signal |

#### Cài Đặt Stochastic
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| %K Period | 6 | Stochastic nhanh |
| %D Period | 6 | Stochastic chậm |
| Slowing | 4 | Hệ số làm mượt |

#### Bộ Lọc Vào Lệnh
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| Max Bars After Cross | 5 | Cửa sổ vào lệnh |
| Min Angle Delta | 0.0 | Bộ lọc góc (0=tắt) |
| Min Decisive Breakout | 10.0 pips | Độ mạnh breakout (tối ưu mặc định cho XAUUSD) |

#### Quản Lý Thoát Lệnh
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| Flat Threshold | 0.01 | Độ phẳng TDI |
| Breakeven Trigger | 30.0 pips | Lợi nhuận để di chuyển SL |
| Breakeven Offset | 2.0 pips | Lợi nhuận giữ lại khi về breakeven |
| Trail Trigger | 50.0 pips | Lợi nhuận để bắt đầu trailing |
| Trail Distance | 25.0 pips | Khoảng cách SL từ giá |

#### Phiên
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| Session Start Hour | 13 (UTC) | New York mở cửa (Winter UTC) |
| Session End Hour | 21 (UTC) | New York đóng cửa (EOD force-flatten) |
| Opening Range | 15 min | Cửa sổ tính toán OR |
| Min OR Width | 20.0 pips | Độ rộng OR tối thiểu |
| ORB Buffer | 3.0 pips | Vùng đệm tránh fakeout |
| DST Rule | US | Tự động điều chỉnh giờ mùa hè (DST) |

#### Guardrails
| Tham Số | Mặc Định | Mô Tả |
|---------|----------|-------|
| Min SL | 20.0 pips | Stop loss tối thiểu |
| Max SL | 80.0 pips | Stop loss tối đa |
| Min TP | 30.0 pips | Take profit tối thiểu |
| Max TP | 250.0 pips | Take profit tối đa |
| Max Giveback | 30.0 pips | Ngưỡng giveback để đóng lệnh |
| Max Loss Streak | 3 | Chặn sau N lần thua |
| Bias Flip Exit | true | Tự động đóng khi bias thay đổi |
### Cài Đặt Portfolio Manager

Chỉnh sửa `app/portfolio.py`:

```python
class PortfolioConfig:
    MAX_POSITIONS = 4              # Số vị thế mở tối đa
    MAX_CURRENCY_EXPOSURE = 2      # Số vị thế tối đa mỗi tiền tệ
    MAX_CORRELATED_POSITIONS = 2   # Số vị thế tương quan tối đa
    MAX_DAILY_LOSS = -200.0        # Giới hạn lỗ hàng ngày (USD)
    MAX_MARGIN_USAGE_PCT = 50.0    # Sử dụng margin tối đa
```

---

## 📡 API Documentation

### POST /trade

Endpoint chính cho quyết định giao dịch.

**Request** (từ cBot):
```json
{
  "bot_id": "eurusd_bot",
  "symbol": "EURUSD",
  "timeframe": "M15",
  "ask": 1.0850,
  "bid": 1.0848,
  "bars": [
    {"ha_color": "Green", "tdi_green": 55.2, "tdi_red": 52.1, "stoch_k": 75.0, "stoch_d": 70.0},
    {"ha_color": "Green", "tdi_green": 54.8, "tdi_red": 51.9, "stoch_k": 72.0, "stoch_d": 68.0},
    {"ha_color": "Red", "tdi_green": 53.5, "tdi_red": 52.5, "stoch_k": 65.0, "stoch_d": 62.0}
  ],
  "tms": {
    "bias": "BULLISH",
    "bars_since_cross": 2,
    "long_entry": true,
    "short_entry": false,
    "green_tf_value": 55.2,
    "green_tf_slope": 0.4
  },
  "orb": {
    "or_high": 1.0845,
    "or_low": 1.0830,
    "breakout_direction": "up",
    "breakout_distance_pips": 5.0,
    "is_decisive": true,
    "bars_since_breakout": 1
  },
  "position": null,
  "session": {
    "phase": "active",
    "minutes_to_end": 180
  }
}
```

**Response** (từ AI):
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+5.0p), momentum rising"
}
```

### POST /portfolio/report

Báo cáo thay đổi vị thế để theo dõi danh mục.

```json
{
  "bot_id": "eurusd_bot",
  "action": "open",
  "symbol": "EURUSD",
  "side": "BUY",
  "volume": 0.01,
  "entry_price": 1.0850,
  "sl_pips": 10.0,
  "tp_pips": 20.0
}
```

### GET /portfolio/status

Lấy trạng thái danh mục hiện tại.

```bash
curl http://127.0.0.1:8000/portfolio/status
```

---

## 🛠️ Phát Triển

### Cấu Trúc Dự Án

```
AgentFxTrading/
├── app/
│   ├── llm_client.py      # Lớp trừu tượng hóa LLM
│   ├── server.py          # FastAPI server
│   └── portfolio.py       # Quản lý rủi ro danh mục
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot
├── .env.example           # Template môi trường
├── requirements.txt       # Python dependencies
├── README.md              # Documentation (6 ngôn ngữ)
└── portfolio.db           # SQLite database (tự động tạo)
```

### Thêm LLM Provider Mới

1. Tạo class mới trong `app/llm_client.py`:

```python
class NewProviderClient(LLMClient):
    def __init__(self, api_key: str, model: str):
        # Khởi tạo client
        pass
    
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        # Triển khai logic chat
        pass
```

2. Cập nhật `create_llm_client()`:

```python
elif provider == "newprovider":
    return NewProviderClient(
        api_key=os.getenv("NEWPROVIDER_API_KEY"),
        model=os.getenv("LLM_MODEL")
    )
```

### Cải Thiện Prompt

Chỉnh sửa `SYSTEM_PROMPT` trong `app/server.py` để điều chỉnh logic giao dịch.

### Chạy Tests

```bash
pytest tests/
```

---

## 📊 Hiệu Suất

### Kết Quả Backtest

> ⚠️ **Lưu ý**: Hiệu suất trong quá khứ không đảm bảo kết quả tương lai. Luôn test với tài khoản demo trước.

| Metric | Giá Trị |
|--------|---------|
| Win Rate | ~55-65% |
| Risk/Reward | 1:2 trung bình |
| Max Drawdown | ~15% |
| Sharpe Ratio | ~1.2 |

### Mẹo Giao Dịch Live

1. **Bắt Đầu Với Demo**: Luôn test chiến lược trước
2. **Kích Thước Vị Thế Nhỏ**: Bắt đầu với 0.01 lots
3. **Theo Dõi Hàng Ngày**: Kiểm tra trạng thái danh mục thường xuyên
4. **Điều Chỉnh Tham Số**: Tune dựa trên điều kiện thị trường
5. **Quản Lý Rủi Ro**: Không bao giờ rủi ro quá 2% mỗi giao dịch

---

## 🤝 Đóng Góp

Mọi đóng góp đều được chào đón! Đây là cách bạn có thể giúp đỡ:

### Cách Đóng Góp

1. **Star repo** ⭐ - Thể hiện sự ủng hộ
2. **Báo cáo bugs** 🐛 - Mở issue
3. **Đề xuất tính năng** 💡 - Mở feature request
4. **Gửi PRs** 🔧 - Đóng góp code
5. **Cải thiện docs** 📚 - Cải thiện documentation
6. **Chia sẻ kết quả** 📈 - Chia sẻ kết quả backtest/live

### Hướng Dẫn Phát Triển

- Tuân theo style code hiện có
- Viết tests cho tính năng mới
- Cập nhật documentation
- Giữ PRs tập trung và nhỏ

### Cộng Đồng

- 💬 [Discussions](https://github.com/yourusername/AgentFxTrading/discussions)
- 🐛 [Issues](https://github.com/yourusername/AgentFxTrading/issues)
- 📧 Email: your-email@example.com

---

## 📄 Giấy Phép

Dự án này được cấp phép theo Giấy Phép MIT - xem file [LICENSE](LICENSE) để biết chi tiết.

---

## 🙏 Lời Cảm Ơn

- **Chiến Lược TMS**: Dựa trên phương pháp TMS chuyên nghiệp
- **cTrader**: Vì đã cung cấp API tuyệt vời
- **Cộng Đồng Mã Nguồn Mở**: Vì các thư viện và công cụ tuyệt vời

---

## 📈 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=yourusername/AgentFxTrading&type=Date)](https://star-history.com/#yourusername/AgentFxTrading&Date)

---

<div align="center">

**Nếu bạn thấy dự án này hữu ích, hãy xem xét cho nó một ⭐!**

[⬆ Lên Đầu Trang](#-agentfxtrading---hệ-thống-giao-dịch-tự-động-với-ai)

</div>
