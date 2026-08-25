# AgentFxTrading

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

Автоматизированная торговая система с использованием AI Agent, интегрированного с cTrader cBot, реализующая стратегию TMS (Trend Momentum Signal) + ORB (Opening Range Breakout).

## Архитектура

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • Расчет TMS   │      JSON Response  │  • Создание prompt│
│  • Расчет ORB   │                     │  • Вызов LLM     │
│  • Отправка snapshot│                  │  • Анализ решения│
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

## Возможности

### cBot (C#)
- **Индикаторы TMS**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **Логика ORB**: Обнаружение Opening Range, обнаружение прорыва
- **Состояние TF Green**: Отслеживание импульса (значение + наклон)
- **Память позиции**: MFE (Maximum Favorable Excursion), отслеживание отката
- **Автоматическое управление выходом**: Breakeven, trailing stop, max giveback
- **Управление сессией**: Фазы сессии, автоматическое закрытие в конце дня
- **Защитные механизмы**: Защита от серии убытков, выход при смене bias, проверка решающего прорыва

### Server (Python)
- **Абстракция LLM**: Поддержка Qwen, OpenAI, Claude, Gemini, DeepSeek
- **Логика стратегии**: Выравнивание bias TMS + прорыв ORB
- **Правила принятия решений**: Условия входа/выхода, управление рисками
- **JSON ответ**: Структурированные торговые решения

## Установка

### 1. Зависимости Python

```bash
pip install -r requirements.txt
```

### 2. Настройка LLM Provider

Скопируйте `.env.example` в `.env` и настройте:

#### Qwen (Рекомендуется - экономичный)
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

## Запуск сервера

```bash
python app/server.py
```

Сервер будет работать на `http://127.0.0.1:8000`

## Запуск cBot

1. Откройте cTrader → Automate
2. Создайте нового бота, вставьте код из `cBot/AiAgentBot.cs`
3. Соберите и прикрепите к графику (M15 или H1)
4. Настройте параметры:
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7 (Лондон), Opening Range=15 минут
   - **Session**: End Hour=16 (Закрытие Лондона)
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

Бот будет автоматически вызывать API при закрытии каждого бара и выполнять решения ИИ.

## Структура проекта

```
.
├── app/
│   ├── llm_client.py      # Слой абстракции LLM
│   ├── server.py          # FastAPI сервер (мозг ИИ)
│   └── portfolio.py       # Управление рисками портфеля
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot (исполнитель)
├── .env.example           # Шаблон переменных окружения
├── requirements.txt       # Зависимости Python
└── README.ru.md
```

## Торговая стратегия

### TMS (Trend Momentum Signal) - Определение Bias
- **БЫЧИЙ**: Green пересекает Red сверху + HA green + Stoch K > D
- **МЕДВЕЖИЙ**: Green пересекает Red снизу + HA red + Stoch K < D
- Bias заблокирован до следующего пересечения

### ORB (Opening Range Breakout) - Триггер входа
- **Opening Range**: High/Low первых N свечей сессии (по умолчанию Лондон 7:00-7:15 UTC)
- **Прорыв**: Цена закрывается выше OR High (бычий) или ниже OR Low (медвежий)
- **Решающий**: Прорыв должен быть достаточно сильным (>= 3 пипсов), чтобы избежать ложного прорыва

### Правила входа
1. TMS БЫЧИЙ + ORB прорыв UP + решающий → BUY
2. TMS МЕДВЕЖИЙ + ORB прорыв DOWN + решающий → SELL
3. Несоответствие или нерешающий → HOLD

### Правила выхода
- **TDI выход**: Green flat/hook/checkmark → CLOSE_ALL
- **Смена bias**: Bias меняется → автоматическое закрытие
- **Конец сессии**: Сессия заканчивается → автоматическое закрытие
- **Breakeven**: Прибыль >= 5p → переместить SL на entry
- **Trailing**: Прибыль >= 10p → trailing SL 5p
- **Max Giveback**: Откат >= порога → автоматическое закрытие

### Защитные механизмы
- Серия убытков >= 3 → блокировка входа
- ORB в противоположном направлении → блокировка входа
- SL/TP ограничены [Min, Max]

## API Endpoint

### POST /trade

**Запрос** (от cBot):
```json
{
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "tms": {
    "bias": "BULLISH",
    "long_entry": true,
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

**Ответ** (от ИИ):
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+16.5p)"
}
```

## Разработка

### Улучшение Prompt
Отредактируйте `SYSTEM_PROMPT` в `app/server.py` для настройки торговой логики.

### Добавление нового LLM Provider
Добавьте новый класс в `app/llm_client.py`, наследующий `LLMClient`, и обновите `create_llm_client()`.

### Множественные символы
Запустите несколько экземпляров cBot на разных графиках, каждый бот вызывает один и тот же сервер.

## Параметры cBot

### TDI
- `RSI Period`: 6 (по умолчанию)
- `Red Period`: 6 (по умолчанию)

### Stochastic
- `%K Period`: 6
- `%D Period`: 6
- `Slowing`: 4

### Вход
- `Max Bars After Cross`: 5
- `Min Angle Delta`: 0.0 (выключено)
- `Min Decisive Breakout`: 3.0 пипсов

### Выход
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 пипсов
- `Trail Trigger`: 10.0 пипсов
- `Trail Distance`: 5.0 пипсов

### ORB
- `Session Start Hour`: 7 (UTC)
- `Opening Range`: 15 минут
- `Min OR Width`: 2.0 пипсов

### Сессия
- `Session End Hour`: 16 (UTC)
- `Session Name`: "london"

### Защитные механизмы
- `Min SL`: 3.0 пипсов
- `Max SL`: 30.0 пипсов
- `Max Loss Streak`: 3
- `Bias Flip Exit`: true

## Лицензия

MIT
