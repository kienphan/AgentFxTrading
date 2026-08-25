# AgentFxTrading

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

cTrader cBotとAI Agentを統合した自動取引システム。TMS（Trend Momentum Signal）+ ORB（Opening Range Breakout）戦略を実装。

## アーキテクチャ

```
┌─────────────────┐      HTTP POST      ┌──────────────────┐
│  cTrader cBot   │ ──────────────────► │  FastAPI Server  │
│     (C#)        │                     │    (Python)      │
│                 │ ◄────────────────── │                  │
│  • TMS計算      │      JSON Response  │  • プロンプト構築 │
│  • ORB計算      │                     │  • LLM呼び出し   │
│  • スナップショット│                    │  • 判断解析      │
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

## 機能

### cBot (C#)
- **TMSインジケーター**: Heiken Ashi, TDI (RSI + Signal), Stochastic
- **ORBロジック**: オーニングレンジ検出、ブレイクアウト検出
- **TF Green状態**: モメンタム追跡（値 + 傾き）
- **ポジション記憶**: MFE（最大有利エクスカージョン）、ギブバック追跡
- **自動エグジット管理**: ブレークイーブン、トレーリングストップ、最大ギブバック
- **セッション管理**: セッションフェーズ、日末自動クローズ
- **ガードレール**: 連敗保護、バイアス反転エグジット、決定的ブレイクアウトチェック

### Server (Python)
- **LLM抽象化**: Qwen, OpenAI, Claude, Gemini, DeepSeek対応
- **戦略ロジック**: TMSバイアス + ORBブレイクアウト整合
- **判断ルール**: エントリー/イグジット条件、リスク管理
- **JSONレスポンス**: 構造化された取引判断

## インストール

### 1. Python依存関係

```bash
pip install -r requirements.txt
```

### 2. LLM Provider設定

`.env.example`を`.env`にコピーして設定：

#### Qwen（推奨 - コスト効率）
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

## サーバー実行

```bash
python app/server.py
```

サーバーは`http://127.0.0.1:8000`で実行されます。

## cBot実行

1. cTrader → Automateを開く
2. 新しいボットを作成し、`cBot/AiAgentBot.cs`のコードを貼り付け
3. ビルドしてチャートにアタッチ（M15またはH1）
4. パラメーターを設定：
   - **API**: `http://127.0.0.1:8000/trade`
   - **TDI**: RSI Period=6, Red Period=6
   - **Stochastic**: K=6, D=6, Slowing=4
   - **ORB**: Session Start Hour=7（ロンドン）, Opening Range=15分
   - **Session**: End Hour=16（ロンドンクローズ）
   - **Exit**: Breakeven Trigger=5p, Trail Trigger=10p
   - **Guardrails**: Min SL=3p, Max SL=30p, Max Loss Streak=3

ボットは各バーのクローズ時に自動的にAPIを呼び出し、AIの判断を実行します。

## プロジェクト構造

```
.
├── app/
│   ├── llm_client.py      # LLM抽象化レイヤー
│   ├── server.py          # FastAPIサーバー（AI脳）
│   └── portfolio.py       # ポートフォリオリスク管理
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot（実行機）
├── .env.example           # 環境変数テンプレート
├── requirements.txt       # Python依存関係
└── README.ja.md
```

## 取引戦略

### TMS（Trend Momentum Signal）- バイアス決定
- **強気**: GreenがRedを上回る + HA green + Stoch K > D
- **弱気**: GreenがRedを下回る + HA red + Stoch K < D
- バイアスは次のクロスまでロックされます

### ORB（Opening Range Breakout）- エントリートリガー
- **Opening Range**: セッションの最初のN本のローソク足のHigh/Low（デフォルト：ロンドン7:00-7:15 UTC）
- **ブレイクアウト**: 価格がOR High以上でクローズ（強気）またはOR Low以下でクローズ（弱気）
- **決定的**: ブレイクアウトは偽のブレイクアウトを避けるために十分強い必要があります（>= 3 pips）

### エントリールール
1. TMS強気 + ORBブレイクアウトUP + 決定的 → BUY
2. TMS弱気 + ORBブレイクアウトDOWN + 決定的 → SELL
3. 不一致または決定的でない → HOLD

### エグジットルール
- **TDIエグジット**: Green flat/hook/checkmark → CLOSE_ALL
- **バイアス反転**: バイアスが反転 → 自動クローズ
- **セッション終了**: セッション終了 → 自動クローズ
- **ブレークイーブン**: 利益 >= 5p → SLをエントリーに移動
- **トレーリング**: 利益 >= 10p → SLを5pトレーリング
- **最大ギブバック**: ギブバック >= 閾値 → 自動クローズ

### ガードレール
- 連敗 >= 3 → エントリーブロック
- ORB逆方向 → エントリーブロック
- SL/TPは[Min, Max]にクランプ

## APIエンドポイント

### POST /trade

**リクエスト**（cBotから）:
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

**レスポンス**（AIから）:
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 10.0,
  "tp_pips": 20.0,
  "reason": "TMS BULLISH bias confirmed, ORB decisive breakout UP (+16.5p)"
}
```

## 開発

### プロンプト改善
`app/server.py`の`SYSTEM_PROMPT`を編集して取引ロジックを調整します。

### 新しいLLM Provider追加
`app/llm_client.py`に`LLMClient`を継承する新しいクラスを追加し、`create_llm_client()`を更新します。

### 複数シンボル
異なるチャートで複数のcBotインスタンスを実行し、各ボットが同じサーバーを呼び出します。

## cBotパラメーター

### TDI
- `RSI Period`: 6（デフォルト）
- `Red Period`: 6（デフォルト）

### Stochastic
- `%K Period`: 6
- `%D Period`: 6
- `Slowing`: 4

### エントリー
- `Max Bars After Cross`: 5
- `Min Angle Delta`: 0.0（オフ）
- `Min Decisive Breakout`: 3.0 pips

### エグジット
- `Flat Threshold`: 0.01
- `Breakeven Trigger`: 5.0 pips
- `Trail Trigger`: 10.0 pips
- `Trail Distance`: 5.0 pips

### ORB
- `Session Start Hour`: 7（UTC）
- `Opening Range`: 15分
- `Min OR Width`: 2.0 pips

### セッション
- `Session End Hour`: 16（UTC）
- `Session Name`: "london"

### ガードレール
- `Min SL`: 3.0 pips
- `Max SL`: 30.0 pips
- `Max Loss Streak`: 3
- `Bias Flip Exit`: true

## ライセンス

MIT
