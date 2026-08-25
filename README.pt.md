# AgentFxTrading

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

Sistema de negociação automatizada usando AI Agent integrado com cTrader cBot, implementando a estratégia TMS (Trend Momentum Signal) + ORB (Opening Range Breakout).

## Arquitetura

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • Calcula TMS  │      JSON Response  │  • Constrói prompt│
│  • Calcula ORB  │                     │  • Chama LLM     │
│  • Envia snapshot│                    │  • Analisa decisão│
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

## Recursos

### cBot (C#)
- **Indicadores TMS**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **Lógica ORB**: Detecção de Opening Range, detecção de breakout
- **Estado TF Green**: Rastreamento de momentum (valor + inclinação)
- **Memória de Posição**: MFE (Maximum Favorable Excursion), rastreamento de giveback
- **Gerenciamento de Saída Automático**: Breakeven, trailing stop, max giveback
- **Gerenciamento de Sessão**: Fases de sessão, fechamento automático no fim do dia
- **Guardrails**: Proteção contra sequência de perdas, saída por inversão de bias, verificação de breakout decisivo

### Server (Python)
- **Abstração LLM**: Suporte a Qwen, OpenAI, Claude, Gemini, DeepSeek
- **Lógica de Estratégia**: Alinhamento de bias TMS + breakout ORB
- **Regras de Decisão**: Condições de entrada/saída, gerenciamento de risco
- **Resposta JSON**: Decisões de negociação estruturadas

## Instalação

### 1. Dependências Python

```bash
pip install -r requirements.txt
```

### 2. Configurar LLM Provider

Copie `.env.example` para `.env` e configure:

#### Qwen (Recomendado - Custo eficiente)
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

## Executando o Servidor

```bash
python app/server.py
```

O servidor será executado em `http://127.0.0.1:8000`

## Executando o cBot

1. Abra cTrader → Automate
2. Crie novo bot, cole o código de `cBot/AiAgentBot.cs`
3. Compile e anexe ao gráfico (M15 ou H1)
4. Configure os parâmetros:
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7 (Londres), Opening Range=15 minutos
   - **Session**: End Hour=16 (Fechamento de Londres)
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

O bot chamará automaticamente a API a cada fechamento de candle e executará as decisões da IA.

## Estrutura do Projeto

```
.
├── app/
│   ├── llm_client.py      # Camada de abstração LLM
│   ├── server.py          # Servidor FastAPI (cérebro da IA)
│   └── portfolio.py       # Gerenciamento de risco de portfólio
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot (executor)
├── .env.example           # Template de variáveis de ambiente
├── requirements.txt       # Dependências Python
└── README.pt.md
```

## Estratégia de Negociação

### TMS (Trend Momentum Signal) - Determinar Bias
- **ALTA**: Green cruza acima de Red + HA green + Stoch K > D
- **BAIXA**: Green cruza abaixo de Red + HA red + Stoch K < D
- Bias é travado até o próximo cruzamento

### ORB (Opening Range Breakout) - Gatilho de Entrada
- **Opening Range**: High/Low das primeiras N velas da sessão (padrão Londres 7:00-7:15 UTC)
- **Breakout**: Preço fecha acima do OR High (alta) ou OR Low (baixa)
- **Decisivo**: Breakout deve ser forte o suficiente (>= 3 pips) para evitar falso breakout

### Regras de Entrada
1. TMS ALTA + ORB breakout UP + decisivo → BUY
2. TMS BAIXA + ORB breakout DOWN + decisivo → SELL
3. Incompatível ou não decisivo → HOLD

### Regras de Saída
- **Saída TDI**: Green flat/hook/checkmark → CLOSE_ALL
- **Inversão de Bias**: Bias inverte → fechamento automático
- **Fim de Sessão**: Sessão termina → fechamento automático
- **Breakeven**: Lucro >= 5p → mover SL para entrada
- **Trailing**: Lucro >= 10p → trail SL 5p
- **Max Giveback**: Giveback >= limite → fechamento automático

### Guardrails
- Sequência de perdas >= 3 → bloquear entrada
- ORB em direção oposta → bloquear entrada
- SL/TP limitados a [Min, Max]

## Endpoint da API

### POST /trade

**Request** (do cBot):
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

**Response** (da IA):
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+16.5p)"
}
```

## Desenvolvimento

### Melhorar Prompt
Edite `SYSTEM_PROMPT` em `app/server.py` para ajustar a lógica de negociação.

### Adicionar Novo LLM Provider
Adicione nova classe em `app/llm_client.py` herdando `LLMClient` e atualize `create_llm_client()`.

### Multi-símbolo
Execute múltiplas instâncias do cBot em gráficos diferentes, cada bot chama o mesmo servidor.

## Parâmetros do cBot

### TDI
- `RSI Period`: 6 (padrão)
- `Red Period`: 6 (padrão)

### Stochastic
- `%K Period`: 6
- `%D Period`: 6
- `Slowing`: 4

### Entrada
- `Max Bars After Cross`: 5
- `Min Angle Delta`: 0.0 (desativado)
- `Min Decisive Breakout`: 3.0 pips

### Saída
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 pips
- `Trail Trigger`: 10.0 pips
- `Trail Distance`: 5.0 pips

### ORB
- `Session Start Hour`: 7 (UTC)
- `Opening Range`: 15 minutos
- `Min OR Width`: 2.0 pips

### Sessão
- `Session End Hour`: 16 (UTC)
- `Session Name`: "london"

### Guardrails
- `Min SL`: 3.0 pips
- `Max SL`: 30.0 pips
- `Max Loss Streak`: 3
- `Bias Flip Exit`: true

## Licença

MIT
