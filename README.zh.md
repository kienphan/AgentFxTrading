# AgentFxTrading

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

自动化交易系统，使用AI Agent与cTrader cBot集成，实施TMS（趋势动量信号）+ ORB（开盘区间突破）策略。

## 架构

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • 计算 TMS     │      JSON Response  │  • 构建 prompt   │
│  • 计算 ORB     │                     │  • 调用 LLM      │
│  • 发送快照     │                     │  • 解析决策      │
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

## 功能特性

### cBot (C#)
- **TMS 指标**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **ORB 逻辑**: 开盘区间检测，突破检测
- **TF Green 状态**: 动量追踪（数值 + 斜率）
- **持仓记忆**: MFE（最大有利偏移），回吐追踪
- **自动退出管理**: 保本、追踪止损、最大回吐
- **交易时段管理**: 时段阶段，日终自动平仓
- **防护栏**: 连亏保护，偏向翻转退出，决定性突破检查

### Server (Python)
- **LLM 抽象层**: 支持 Qwen, OpenAI, Claude, Gemini, DeepSeek
- **策略逻辑**: TMS 偏向 + ORB 突破对齐
- **决策规则**: 入场/出场条件，风险管理
- **JSON 响应**: 结构化交易决策

## 安装

### 1. Python 依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM Provider

将 `.env.example` 复制为 `.env` 并配置：

#### Qwen（推荐 - 性价比高）
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

## 运行服务器

```bash
python app/server.py
```

服务器将在 `http://127.0.0.1:8000` 运行

## 运行 cBot

1. 打开 cTrader → Automate
2. 创建新 bot，粘贴 `cBot/AiAgentBot.cs` 中的代码
3. 构建并附加到图表（M15 或 H1）
4. 配置参数：
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7（伦敦）, Opening Range=15 分钟
   - **Session**: End Hour=16（伦敦收盘）
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

Bot 将在每根K线收盘时自动调用 API 并执行 AI 决策。

## 项目结构

```
.
├── app/
│   ├── llm_client.py      # LLM 抽象层
│   ├── server.py          # FastAPI 服务器（AI 大脑）
│   └── portfolio.py       # 组合风险管理
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot（执行器）
├── .env.example           # 环境变量模板
├── requirements.txt       # Python 依赖
└── README.zh.md
```

## 交易策略

### TMS（趋势动量信号）- 确定偏向
- **看涨**: Green 上穿 Red + HA green + Stoch K > D
- **看跌**: Green 下穿 Red + HA red + Stoch K < D
- 偏向锁定直到下一次交叉

### ORB（开盘区间突破）- 入场触发
- **开盘区间**: 时段前 N 根K线的 High/Low（默认伦敦 7:00-7:15 UTC）
- **突破**: 价格收于 OR High 上方（看涨）或 OR Low 下方（看跌）
- **决定性**: 突破必须足够强（>= 3 pips）以避免假突破

### 入场规则
1. TMS 看涨 + ORB 向上突破 + 决定性 → BUY
2. TMS 看跌 + ORB 向下突破 + 决定性 → SELL
3. 不匹配或不确定 → HOLD

### 出场规则
- **TDI 退出**: Green 走平/钩子/对号 → CLOSE_ALL
- **偏向翻转**: 偏向反转 → 自动平仓
- **时段结束**: 时段结束 → 自动平仓
- **保本**: 利润 >= 5p → 移动 SL 到入场价
- **追踪**: 利润 >= 10p → 追踪 SL 5p
- **最大回吐**: 回吐 >= 阈值 → 自动平仓

### 防护栏
- 连亏 >= 3 → 阻止入场
- ORB 方向相反 → 阻止入场
- SL/TP 限制在 [最小值, 最大值]

## API 端点

### POST /trade

**请求**（来自 cBot）:
```json
{
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "ask": 2450.15,
  "bid": 2450.10,
  "tms": {
    "bias": "BULLISH",
    "long_entry": true,
    "short_entry": false,
    "green_tf_value": 55.2,
    "green_tf_slope": 0.4
  },
  "orb": {
    "breakout_direction": "up",
    "breakout_distance_pips": 16.5,
    "is_decisive": true
  },
  "position": null,
  "session": {
    "phase": "active",
    "minutes_to_end": 180
  }
}
```

**响应**（来自 AI）:
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+16.5p)"
}
```

## 开发

### 改进 Prompt
编辑 `app/server.py` 中的 `SYSTEM_PROMPT` 以调整交易逻辑。

### 添加新 LLM Provider
在 `app/llm_client.py` 中添加继承 `LLMClient` 的新类并更新 `create_llm_client()`。

### 多品种
在不同图表上运行多个 cBot 实例，每个 bot 调用同一服务器。

## cBot 参数

### TDI
- `RSI Period`: 6（默认）
- `Red Period`: 6（默认）

### Stochastic
- `%K Period`: 6
- `%D Period`: 6
- `Slowing`: 4

### 入场
- `Max Bars After Cross`: 5
- `Min Angle Delta`: 0.0（关闭）
- `Min Decisive Breakout`: 3.0 pips

### 出场
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 pips
- `Trail Trigger`: 10.0 pips
- `Trail Distance`: 5.0 pips

### ORB
- `Session Start Hour`: 7（UTC）
- `Opening Range`: 15 分钟
- `Min OR Width`: 2.0 pips

### 会话
- `Session End Hour`: 16（UTC）
- `Session Name`: "london"

### 防护栏
- `Min SL`: 3.0 pips
- `Max SL`: 30.0 pips
- `Max Loss Streak`: 3
- `Bias Flip Exit`: true

## 许可证

MIT
