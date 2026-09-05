# 🤖 AgentFxTrading - AI搭載自動取引システム

<div align="center">

**TMS + ORB戦略による自動外国為替取引**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![cTrader](https://img.shields.io/badge/cTrader-4.x+-green.svg)](https://ctdn.com/)
[![Stars](https://img.shields.io/github/stars/yourusername/AgentFxTrading?style=social)](https://github.com/yourusername/AgentFxTrading/stargazers)
[![Forks](https://img.shields.io/github/forks/yourusername/AgentFxTrading?style=social)](https://github.com/yourusername/AgentFxTrading/network/members)
[![Issues](https://img.shields.io/github/issues/yourusername/AgentFxTrading)](https://github.com/yourusername/AgentFxTrading/issues)
[![Donate](https://img.shields.io/badge/Donate-Buy%20Me%20A%20Coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/kaz126)

[🇬🇧 English](README.md) | [🇻🇳 Tiếng Việt](README.vi.md) | [🇨🇳 中文](README.zh.md) | [🇵🇹 Português](README.pt.md) | [🇯🇵 日本語](README.ja.md) | [🇷🇺 Русский](README.ru.md)

[インストール](#-クイックスタート) • [機能](#-機能) • [戦略](#-取引戦略) • [APIドキュメント](#-apiドキュメント) • [サポート＆おすすめブローカー](#-サポートおすすめブローカー) • [貢献](#-貢献)

</div>

---

## 📋 目次

- [概要](#-概要)
- [機能](#-機能)
- [アーキテクチャ](#-アーキテクチャ)
- [クイックスタート](#-クイックスタート)
- [取引戦略](#-取引戦略)
- [設定](#-設定)
- [APIドキュメント](#-apiドキュメント)
- [開発](#-開発)
- [パフォーマンス](#-パフォーマンス)
- [サポート＆おすすめブローカー](#-サポートおすすめブローカー)
- [貢献](#-貢献)
- [ライセンス](#-ライセンス)

---

## 🎯 概要

AgentFxTradingは、AIの力を実証済みのテクニカル分析戦略と組み合わせた**自動外国為替取引システム**です。**TMS（Trend Momentum Signal）**を使用してトレンド検出を行い、**ORB（Opening Range Breakout）**を使用して正確なエントリータイミングを提供します。

### AgentFxTradingを選ぶ理由

✅ **完全自律型** - AIが24時間365日取引判断  
✅ **マルチLLM対応** - Qwen、OpenAI、Claude、Gemini、DeepSeekで動作  
✅ **リスク管理** - 複数の通貨ペアにわたるポートフォリオレベルのリスク管理  
✅ **実証済み戦略** - プロフェッショナルなTMS方法論に基づく  
✅ **簡単セットアップ** - 10分以内に開始  
✅ **オープンソース** - 完全に透明でカスタマイズ可能  

---

## 🚀 機能

### 🤖 デュアルAI戦略エンジン
- **1. TMS + ORB エンジン (`AiAgentBot`)**: トレンドモメンタムシグナル（Heikin Ashi + TDI + Stochastic）とオープニングレンジブレイクアウトを組み合わせ、動的カウフマン効率相場判定を実装。
- **2. アジアンレンジ・ジューダススイープ エンジン (`AsianRangeJudasSweepBot`)**: ICTスマートマネーコンセプト（SMC）に基づき、東京・アジアセッション高値・安値（00:00–06:00 UTC）の流動性ハント（ダマシ）をロンドン（07:00–10:00 UTC）およびNY重なり（12:30–16:00 UTC）キルゾーンで捕らえ、Order Block / FVGで高R:Rスナイパー反転を狙う。
- **マルチLLM対応**：Qwen、OpenAI GPT-4o、Claude 3.5 Sonnet、Gemini 2.0 Flash、DeepSeek V3/R1。
- **マルチタイムフレーム分析**：M15 + H1 + H4のトレンド同期、スイング構造分析、リアルタイム経済指標フィルター。
### 💼 ポートフォリオ管理
- **マルチシンボル取引**：異なる通貨ペアで複数のボットを実行
- **通貨エクスポージャー制御**：単一通貨への過剰エクスポージャーを防止
- **相関検出**：高度に相関するポジションをブロック
- **日次損失制限**：最大損失後に自動取引停止

- **ポジションメモリ**：毎Tick最高含み益（MFE）を追跡
- **自動ブレークイーブン**：利益が $\ge 0.8\times$ ATR に達した際、SLを建値に移動（+0.1x ATRオフセット）
- **トレーリングストップ**：利益が $1.2\times$ ATR に達した際、動的SL調整を開始（トレール距離 $0.7\times$ ATR）
- **利益確定・ギブバック保護**：最高含み益 (MFE) からのギブバックが $\ge 40\%$ または $\ge 0.6\times$ ATR に達した場合に強制決済して利益を確保
- **過大乖離フィルター (Anti-Overextension Guard)**：オープニングレンジ境界から $2.5\times$ ATR 以上乖離した過熱・出尽くしブレイクアウトの追随を禁止
- **最大金額損失制限 (Max Dollar Risk Cap)**：最小ロットのボラティリティが大きい銘柄に対して1トレードの最大損失額 ($12.00) を制限
- **連敗保護**：3連敗後にエントリーをブロック
- **サイクルゲーティング (Cost Gate)**：セッション外、OR内、過熱状態、連敗中にLLM呼び出しを自動スキップし、APIコストを80-90%削減
- **トレンド時固定利確解除 (Trend TP Disabled)**：強いトレンド相場 (`trending`) で固定TPを自動解除し、トレーリングSLとギブバックフロアで利益を最大化
- **銘柄別動的精度管理 (Dynamic Precision)**：小数を自動スケール（通常FXは5桁、JPYクロスは3桁、ゴールド/指数/仮想通貨は2桁）、AIプロンプト内のローソク足歪みを防止
- **実効ATR Pips正規化 (True ATR Scaling)**：銘柄ごとの生ボラティリティ数値を実効Pips値に自動換算し、LLMがボラティリティを正確に評価
- **暗号資産適応型サイクルゲート**：暗号資産分類 (`BTC`, `ETH`, `SOL`, `XRP`) を正確に認識し、最大60,000 pipsの実勢ブレイクアウト許容距離を設定
- **銘柄別アジアンレンジフィルター (Adaptive Asian Range)**：ゴールド (`[200p, 8000p]`)、FX (`[12p, 100p]`) の専用レンジ境界とノーポジ時の不要リクエスト抑制
- **cBotガードレール・テレメトリ統合**：cBot内部のエントリーブロック理由をリアルタイムに `/api/cbot_event` 経由でFastAPIサーバーに通知
### ⏰ セッション管理
- **取引セッション**：設定可能なセッション時間（ロンドン、NY、東京）
- **日末自動クローズ**：セッション終了時に自動的にポジションをクローズ
- **フェーズ検出**：プレマーケット、アクティブ、エンディング、クローズドフェーズ

---

## 🏗️ アーキテクチャ

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

### コンポーネント詳細

| コンポーネント | 技術 | 責任 |
|--------------|------|------|
| **cBot** | C# / cTrader | 指標計算、取引実行 |
| **Server** | Python / FastAPI | AI意思決定、リスク管理 |
| **Database** | SQLite | ポートフォリオ追跡、ポジション履歴 |
| **LLM** | 複数 | 取引判断分析 |


---

## 📊 ダッシュボード (Dashboard)

モダンなWebダッシュボードを通じてリアルタイムに取引システムを監視・管理できます：
```
http://127.0.0.1:8000/dashboard
```

### ダッシュボード機能

- **リアルタイム更新**：WebSocketによるポジション・損益のライブ同期
- **ポートフォリオ概要**：保有ポジション、当日P&L、勝率、連敗数、口座資産・有効証拠金
- **保有ポジション一覧 (Active Positions)**：戦略バッジ（`Judas SMC` 紫バッジ vs `TMS+ORB` 青バッジ）とcBot名、通貨ペア、ロット数、現在値、含み損益を明確に識別
- **取引履歴 (Recent Trades)**：決済済みトレード一覧と戦略ラベル・純損益
- **日次P&Lチャート**：パフォーマンス推移の可視化グラフ
- **ガードレール監視**：cBot側のエントリーブロック事由のリアルタイム表示

### APIエンドポイント

```
GET  /dashboard                # Webダッシュボード画面
GET  /api/dashboard/summary    # ポートフォリオKPIサマリー (JSON)
GET  /api/dashboard/positions  # 保有ポジション一覧 (JSON)
GET  /api/dashboard/history    # 決済済み取引履歴 (JSON)
GET  /api/dashboard/pnl-history # 日次P&L履歴 (JSON)
GET  /api/dashboard/logs       # システムリアルタイムログ (JSON)
POST /api/tick                 # cBotレート・残高テレメトリ
POST /api/cbot_event           # cBotガードレール警告・ブロック通知
POST /portfolio/report         # ポジション開始・決済レポート
WS   /ws/dashboard             # リアルタイムWebSocket配信
```
---

## ⚡ クイックスタート

### 前提条件

- Python 3.9+
- cTrader 4.x+（口座をお持ちでない場合は、低スプレッド・低遅延の [IC Markets cTrader](https://ic.com/?camp=95400) での口座開設を推奨します）
- LLM APIキー（Qwen/OpenAI/Claude/Gemini/DeepSeek）

### 1. Python依存関係のインストール

```bash
# リポジトリのクローン
git clone https://github.com/yourusername/AgentFxTrading.git
cd AgentFxTrading

# 依存関係のインストール
pip install -r requirements.txt
```

### 2. LLM Providerの設定

```bash
# 環境テンプレートのコピー
cp .env.example .env

# .envをAPIキーで編集
# Qwenの例（推奨）：
LLM_PROVIDER=qwen
DASHSCOPE_API_KEY=sk-your-dashscope-key
LLM_MODEL=qwen-max
```

### 3. サーバーの起動

```bash
python app/server.py
```

サーバーは`http://127.0.0.1:8000`で実行されます

### 4. cBotの設定と実行

cBotは**cTraderデスクトップGUI**または**ヘッドレスDocker CLI**（`ctrader-console`）のいずれかで実行できます。

#### オプションA：cTraderデスクトップGUI

1. **cTrader** → **Automate**を開く
2. **New** → **cBot**をクリック
3. `cBot/AiAgentBot.cs`からコードを貼り付け
4. **Build**をクリック
5. チャートにアタッチ（M15またはH1推奨）
6. パラメータを設定：
   - **Bot ID**：`xauusd_m15`（一意の識別子）
   - **API URL**：`http://127.0.0.1:8000/trade`
   - **Session**：New York（13:00-21:00 UTC）/ London（8:00-17:00 UTC）/ Tokyo（0:00-9:00 UTC）

#### オプションB：ヘッドレスDocker CLI（`ctrader-console`）

1. **cTID認証情報ファイルの準備**:
   ```bash
   mkdir -p /root/ctrader_data
   echo "your_ctid_password" > /root/ctrader_data/ctid_pwd
   chmod 600 /root/ctrader_data/ctid_pwd
   ```

2. **`.algo`パッケージのビルド/コンパイル**:
   ```bash
   # 1. TMS+ORB Botのビルド (AiAgentBot)
   docker run --rm -v $(pwd):/workspace -v /root:/root \
     ghcr.io/spotware/ctrader-console:latest create cbot AiAgentBot
   cp cBot/AiAgentBot.cs /root/cAlgo/Sources/Robots/AiAgentBot/AiAgentBot/AiAgentBot.cs
   docker run --rm -v $(pwd):/workspace -v /root:/root \
     ghcr.io/spotware/ctrader-console:latest build /root/cAlgo/Sources/Robots/AiAgentBot/AiAgentBot/AiAgentBot.csproj
   cp /root/cAlgo/Sources/Robots/AiAgentBot.algo cBot/AiAgentBot.algo

   # 2. Asian Range Judas Sweep Botのビルド (AsianRangeJudasSweepBot)
   docker run --rm -v $(pwd):/workspace -v /root:/root \
     ghcr.io/spotware/ctrader-console:latest create cbot AsianRangeJudasSweepBot
   cp cBot/AsianRangeJudasSweepBot.cs /root/cAlgo/Sources/Robots/AsianRangeJudasSweepBot/AsianRangeJudasSweepBot/AsianRangeJudasSweepBot.cs
   docker run --rm -v $(pwd):/workspace -v /root:/root \
     ghcr.io/spotware/ctrader-console:latest build /root/cAlgo/Sources/Robots/AsianRangeJudasSweepBot/AsianRangeJudasSweepBot/AsianRangeJudasSweepBot.csproj
   cp /root/cAlgo/Sources/Robots/AsianRangeJudasSweepBot.algo cBot/AsianRangeJudasSweepBot.algo
   ```
3. **各通貨ペア・インデックスのDockerコンテナ起動**:

   * **XAUUSD アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-xauusd-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=XAUUSD \
       --period=m15 \
       --full-access \
       --BotId="cbot-xauusd-judas" \
       --label="cbot-xauusd-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=200.0 \
       --maxAsianRangePips=8000.0 \
       --sweepBufferPips=30.0 \
       --AiSlMinFloorPips=200.0 \
       --breakEvenTrigger=250.0 \
       --stoplossPip=200.0 \
       --takeprofitPip=450.0 \
       --enableBreakEvenPrice=true

   * **GBPUSD アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-gbpusd-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=GBPUSD \
       --period=m15 \
       --full-access \
       --BotId="cbot-gbpusd-judas" \
       --label="cbot-gbpusd-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=15.0 \
       --maxAsianRangePips=45.0 \
       --sweepBufferPips=3.5 \
       --AiSlMinFloorPips=15.0 \
       --breakEvenTrigger=20.0 \
       --stoplossPip=15.0 \
       --takeprofitPip=35.0 \
       --enableBreakEvenPrice=true
     ```

   * **EURUSD アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-eurusd-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=EURUSD \
       --period=m15 \
       --full-access \
       --BotId="cbot-eurusd-judas" \
       --label="cbot-eurusd-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=15.0 \
       --maxAsianRangePips=45.0 \
       --sweepBufferPips=3.5 \
       --AiSlMinFloorPips=15.0 \
       --breakEvenTrigger=20.0 \
       --stoplossPip=15.0 \
       --takeprofitPip=35.0 \
       --enableBreakEvenPrice=true
     ```

   * **GBPJPY アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-gbpjpy-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=GBPJPY \
       --period=m15 \
       --full-access \
       --BotId="cbot-gbpjpy-judas" \
       --label="cbot-gbpjpy-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=25.0 \
       --maxAsianRangePips=70.0 \
       --sweepBufferPips=5.0 \
       --AiSlMinFloorPips=25.0 \
       --breakEvenTrigger=30.0 \
       --stoplossPip=25.0 \
       --takeprofitPip=50.0 \
       --enableBreakEvenPrice=true
     ```

   * **EURJPY アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-eurjpy-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=EURJPY \
       --period=m15 \
       --full-access \
       --BotId="cbot-eurjpy-judas" \
       --label="cbot-eurjpy-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=25.0 \
       --maxAsianRangePips=70.0 \
       --sweepBufferPips=5.0 \
       --AiSlMinFloorPips=25.0 \
       --breakEvenTrigger=30.0 \
       --stoplossPip=25.0 \
       --takeprofitPip=50.0 \
       --enableBreakEvenPrice=true
     ```

   * **BTCUSD アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-btcusd-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=BTCUSD \
       --period=m15 \
       --full-access \
       --BotId="cbot-btcusd-judas" \
       --label="cbot-btcusd-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=10000.0 \
       --maxAsianRangePips=400000.0 \
       --sweepBufferPips=1500.0 \
       --AiSlMinFloorPips=20000.0 \
       --breakEvenTrigger=25000.0 \
       --stoplossPip=25000.0 \
       --takeprofitPip=60000.0 \
       --enableBreakEvenPrice=true \
       --riskFactor=1.0
     ```

   * **ETHUSD アジアンレンジ・ジューダススイープ (M15 - ICT Judas Sweep)**:
     ```bash
     docker run -d \
       --name cbot-ethusd-judas \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AsianRangeJudasSweepBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=ETHUSD \
       --period=m15 \
       --full-access \
       --BotId="cbot-ethusd-judas" \
       --label="cbot-ethusd-judas" \
       --DashboardServerUrl="http://127.0.0.1:8000" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --UseDirectAiApi=false \
       --UseAiGateMode=true \
       --minAsianRangePips=800.0 \
       --maxAsianRangePips=35000.0 \
       --sweepBufferPips=150.0 \
       --AiSlMinFloorPips=1500.0 \
       --breakEvenTrigger=2000.0 \
       --stoplossPip=2000.0 \
       --takeprofitPip=5000.0 \
       --enableBreakEvenPrice=true \
       --riskFactor=1.0
     ```

   * **XAUUSD TMS+ORB (M15 - ニューヨークセッション)**:
     ```bash
     docker run -d \
       --name cbot-xauusd \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=XAUUSD \
       --period=m15 \
       --full-access \
       --BotId="xauusd_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --SessionName="newyork" \
       --OrbStartHour=13 \
       --SessionEndHour=21 \
       --SessionDstRule="US" \
       --MinDecisiveBreakoutPips=200.0 \
       --MinOrWidthPips=400.0 \
       --OrbBufferPips=50.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=10 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **EURUSD (M15 - ロンドンセッション)**:
     ```bash
     docker run -d \
       --name cbot-eurusd \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=EURUSD \
       --period=m15 \
       --full-access \
       --BotId="eurusd_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="london" \
       --OrbStartHour=8 \
       --SessionEndHour=17 \
       --SessionDstRule="Europe" \
       --MinDecisiveBreakoutPips=3.0 \
       --MinOrWidthPips=6.0 \
       --OrbBufferPips=1.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=5 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **GBPUSD (M15 - ロンドンセッション)**:
     ```bash
     docker run -d \
       --name cbot-gbpusd \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=GBPUSD \
       --period=m15 \
       --full-access \
       --BotId="gbpusd_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="london" \
       --OrbStartHour=8 \
       --SessionEndHour=17 \
       --SessionDstRule="Europe" \
       --MinDecisiveBreakoutPips=4.5 \
       --MinOrWidthPips=10.0 \
       --OrbBufferPips=1.5 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=10 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **USDJPY (M15 - 東京セッション)**:
     ```bash
     docker run -d \
       --name cbot-usdjpy \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=USDJPY \
       --period=m15 \
       --full-access \
       --BotId="usdjpy_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="tokyo" \
       --OrbStartHour=0 \
       --SessionEndHour=9 \
       --SessionDstRule="None" \
       --MinDecisiveBreakoutPips=4.0 \
       --MinOrWidthPips=8.0 \
       --OrbBufferPips=1.5 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=3 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **US30 (M15 - ニューヨークインデックスセッション)**:
     ```bash
     docker run -d \
       --name cbot-us30 \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=US30 \
       --period=m15 \
       --full-access \
       --BotId="us30_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="newyork_index" \
       --OrbStartHour=13 \
       --SessionEndHour=20 \
       --SessionDstRule="US" \
       --MinDecisiveBreakoutPips=30.0 \
       --MinOrWidthPips=80.0 \
       --OrbBufferPips=15.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=30 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **USTEC / NAS100 (M5 - ニューヨークインデックスセッション)** *(注意：ブローカーによって `USTEC` または `NAS100` を使用)*:
     ```bash
     docker run -d \
       --name cbot-ustec \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=USTEC \
       --period=m5 \
       --full-access \
       --BotId="ustec_m5" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="newyork_index" \
       --OrbStartHour=13 \
       --SessionEndHour=20 \
       --SessionDstRule="US" \
       --MinDecisiveBreakoutPips=25.0 \
       --MinOrWidthPips=70.0 \
       --OrbBufferPips=12.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=25 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **GBPJPY (M15 - ロンドンセッション / 高ボラティリティクロス)**:
     ```bash
     docker run -d \
       --name cbot-gbpjpy \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=GBPJPY \
       --period=m15 \
       --full-access \
       --BotId="gbpjpy_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="london" \
       --OrbStartHour=8 \
       --SessionEndHour=17 \
       --SessionDstRule="Europe" \
       --MinDecisiveBreakoutPips=6.0 \
       --MinOrWidthPips=15.0 \
       --OrbBufferPips=2.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=5 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **EURJPY (M15 - ロンドンセッション / 高ボラティリティクロス)**:
     ```bash
     docker run -d \
       --name cbot-eurjpy \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=EURJPY \
       --period=m15 \
       --full-access \
       --BotId="eurjpy_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="london" \
       --OrbStartHour=8 \
       --SessionEndHour=17 \
       --SessionDstRule="Europe" \
       --MinDecisiveBreakoutPips=5.0 \
       --MinOrWidthPips=12.0 \
       --OrbBufferPips=1.5 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=5 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **USDCAD (M15 - ニューヨークセッション / コモディティFX)**:
     ```bash
     docker run -d \
       --name cbot-usdcad \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=USDCAD \
       --period=m15 \
       --full-access \
       --BotId="usdcad_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="newyork" \
       --OrbStartHour=13 \
       --SessionEndHour=21 \
       --SessionDstRule="US" \
       --MinDecisiveBreakoutPips=4.0 \
       --MinOrWidthPips=10.0 \
       --OrbBufferPips=1.5 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=4 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **AUDUSD (M15 - アジア/東京セッション / コモディティFX)**:
     ```bash
     docker run -d \
       --name cbot-audusd \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=AUDUSD \
       --period=m15 \
       --full-access \
       --BotId="audusd_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="tokyo" \
       --OrbStartHour=0 \
       --SessionEndHour=9 \
       --SessionDstRule="None" \
       --MinDecisiveBreakoutPips=3.0 \
       --MinOrWidthPips=8.0 \
       --OrbBufferPips=1.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=3 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **DE40 / DAX40 (M5 - ヨーロッパ/ロンドンセッション / ドイツ株式指数)** *(注意：ブローカーによって `DE40` または `GER40` を使用)*:
     ```bash
     docker run -d \
       --name cbot-de40 \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=DE40 \
       --period=m15 \
       --full-access \
       --BotId="de40_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="london" \
       --OrbStartHour=8 \
       --SessionEndHour=16 \
       --SessionDstRule="Europe" \
       --MinDecisiveBreakoutPips=20.0 \
       --MinOrWidthPips=60.0 \
       --OrbBufferPips=10.0 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=25 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

   * **AUDJPY (M15 - アジア/東京セッション / リスクバロメータークロス)**:
     ```bash
     docker run -d \
       --name cbot-audjpy \
       --restart unless-stopped \
       --network host \
       -v $(pwd):/workspace \
       -v /root:/root \
       ghcr.io/spotware/ctrader-console:latest \
       run /workspace/cBot/AiAgentBot.algo \
       --ctid=your_email@example.com \
       --pwd-file=/root/ctrader_data/ctid_pwd \
       --account=YOUR_ACCOUNT_ID \
       --symbol=AUDJPY \
       --period=m15 \
       --full-access \
       --BotId="audjpy_m15" \
       --ApiUrl="http://127.0.0.1:8000/trade" \
       --AccountLabel="demo" \
       --TmsTimeFrame="Hour" \
       --EmaPeriod=5 \
       --SessionName="tokyo" \
       --OrbStartHour=0 \
       --SessionEndHour=9 \
       --SessionDstRule="None" \
       --MinDecisiveBreakoutPips=4.0 \
       --MinOrWidthPips=10.0 \
       --OrbBufferPips=1.5 \
       --BreakevenTriggerAtr=1.2 \
       --BreakevenOffsetAtr=0.1 \
       --TrailTriggerAtr=2.0 \
       --TrailDistanceAtr=1.0 \
       --PartialCloseRatio=0.5 \
       --MinSlAtr=0.8 \
       --MaxSlAtr=3.0 \
       --MinTpAtr=1.0 \
       --MaxTpAtr=6.0 \
       --MaxGivebackAtr=1.0 \
       --EnablePostTpGate=true \
       --PostTpPullbackAtr=0.5 \
       --BounceTradeEnabled=true \
       --BounceDistanceThreshold=4 \
       --RiskPerTradePercent=0.2 \
       --TrendTpDisabled=true
     ```

### 5. 取引開始！🎉

ボットは自動的に：
- 各バーのクローズ時に指標を計算
- AIサーバーに市場スナップショットを送信
- 取引判断を受信
- リスク管理付きで取引を実行

---

## 📈 取引戦略

### TMS（Trend Momentum Signal）

TMSは3つの確認を使用して**方向性バイアス**を識別します：

| 指標 | 強気シグナル | 弱気シグナル |
|------|-------------|-------------|
| **TDI** | Green > Red | Green < Red |
| **Heiken Ashi** | 緑のローソク足 | 赤のローソク足 |
| **Stochastic** | K > D | K < D |

**重要な概念**：バイアスは次のクロスまでロックされ、ウィップソーを防ぎます。

### ORB（Opening Range Breakout）

ORBは**正確なエントリータイミング**を提供します：

1. **オープニングレンジ**：セッションの最初の15分のHigh/Low
2. **ブレイクアウト**：価格がOR境界を超えてクローズ
3. **決定的フィルタ**：ブレイクアウトは決定的である必要があります（≥ MinDecisiveBreakoutPips、XAUUSDデフォルトは10.0 pips）

### 市場レジーム検出 (Market Regime)

システムはリアルタイムの資金効率指標を計算し、取引および決済動作を動的に適応させます：
- **`er_session` & `er_recent`**：カウフマン効率比率 ($ER = \frac{|\text{純移動距離}|}{\sum |\text{ローソク足変動}|}$)。$1.0$ はきれいな一方向トレンド、$\approx 0.0$ はノイズの多いもみ合いを示します。
- **`or_flips`**：オープニングレンジをブレイクした後に再びレンジ内に終値で戻った騙しの回数をカウントします。
- **4つの市場レジーム**：
  - **`trending`** ($ER \ge 0.35$)：固定TPを自動解除 (`TrendTpDisabled = true`) し、トレーリングSLとギブバックフロアで大きなトレンド利益を獲得。
  - **`choppy`** (`or_flips \ge 5`)：騙しブレイクアウトの危険が高い → サイクルゲートが強制的に `HOLD` を選択。
  - **`mixed`**：標準的な取引規律 ($R:R \ge 1.5$)。
  - **`forming`**：セッション初期のレンジ形成フェーズ ($< 6$ 本)。

### エントリーモデル & 定量的取引規律
- **モデル 1: ダイレクト・モメンタム・ブレイクアウト**: 価格がエントリーウィンドウ内（$\le 5$本）でオープニングレンジ境界を決定的に突破し、過大乖離していない場合（$\le 2.5\times$ ATR）。
- **モデル 2: ブレイクアウトリテスト + TDIバウンス (押し目・戻り継続)**: ブレイクアウトが進行した場合（$5 < \text{本} \le 10$）、検証済みの**TDIバウンス**（`tdi_bounce_bull` / `tdi_bounce_bear`）が発生し、価格が5 EMA近傍に構造的に位置しており、過大乖離していない場合のみエントリーを許可。
- **過大乖離防止ルール (ANTI-OVEREXTENSION)**：価格がOR境界から過剰に乖離している場合（$> 2.5\times$ ATR、またはゴールド $> 1500$ pips / $\$15.00$、BTC $> 30,000$ pips、株価指数 $> 1500$ pips、為替 $> 50$ pips）、**絶対に追随エントリーしない** → `HOLD` を維持。
- **BIAS-FRESH 例外**：TDIクロスが直近で発生した場合（$\le 1$ 本前）、早期のブレイクアウト推進力は**新しいトレンド波の開始**とみなされ、買われすぎ/売られすぎではないと判断 → 順張りエントリーを強く推奨。
- **ANTI-CHASE ルール**：古いバイアス下で有効な押し目なく $\ge 4$ 本以上ブレイクアウトが進んでいる場合、**高値・安値を追随して飛び乗ることを禁止** → `HOLD` で押し目・戻りを待つ。
- **利確後ゲート (Post-TP Gate Anti-FOMO)**：TP到達または大きな勝ちトレード決済後、十分な構造的押し目・戻り（$\ge 0.5\times$ ATR）、OR接触、またはバイアス反転が発生するまで同一方向への再エントリーを厳格にブロック。
- **利益確定 & ギブバックフロア**：短期的なノイズに対してポジションに呼吸スペースを提供。最高含み益が $\ge 0.8\times$ ATR に達した後、ピークから $\ge 40\%$ のギブバックが発生するかモメンタムが失速した場合、直ちに決済して利益を確保。

### 🏹 アジアンレンジ・ジューダススイープ戦略 (ICT Smart Money Concepts)

**Asian Range Judas Sweep AI Bot** は **XAUUSD (ゴールド M15)** において機関投資家レベルの流動性ハント反転モデルを実行します：

1. **アジアセッション追跡 (`00:00 – 06:00 UTC`)**:
   - 流動性境界を設定：`Asian High`（買い流動性 / BSL）および `Asian Low`（売り流動性 / SSL）。
   - アジアレンジの適正幅を検証（`50`〜`350` pips）。
2. **ゴールデン・キルゾーン**:
   - **ロンドンオープン・キルゾーン**: `07:00 – 10:00 UTC`（流動性ハント発生の最大ウィンドウ）。
   - **ニューヨーク重複キルゾーン**: `12:30 – 16:00 UTC`（米系大口資金参入）。
3. **事前判定ゲート (Judas Swing 検知)**:
   - **売りシグナルゲート (`JUDAS_SWEEP_SELL`)**: 価格が `Asian High + sweepBufferPips (15 pips)` を上ヒゲで突き抜けてブレイク買いを誘い、アジアレンジ *内部* に終値で戻る。
   - **買いシグナルゲート (`JUDAS_SWEEP_BUY`)**: 価格が `Asian Low - sweepBufferPips (15 pips)` を下ヒゲで突き抜けてブレイク売りを誘い、アジアレンジ *内部* に終値で戻る。
4. **AI Agent スナイパー意思決定**:
   - オーダーブロック (OB)、フェアバリューギャップ (FVG)、マルチタイムフレーム構造（M15 + H1 + H4）、直近50本のOHLCVローソク足を総合分析。
   - ストップロスをヒゲの極値外側に配置（最低保護床 `200 pips` / ゴールド $2.00 USD）、利確目標をアジアレンジの対向境界に設定。
### エントリールール

```
IF TMS強気 AND ORBブレイクアウトUP AND 決定的:
    → BUY
    
IF TMS弱気 AND ORBブレイクアウトDOWN AND 決定的:
    → SELL
    
ELSE:
    → HOLD
```

### エグジットルール

| 条件 | アクション |
|------|-----------|
| 確認されたTDI反転（Redライン逆クロス / 買われすぎ・売られすぎ反転でのEMA喪失） | CLOSE_ALL |
| バイアスが反転 | 自動クローズ |
| セッション終了（EOD） | 自動全クローズ（EOD Force-Flatten安全ネット） |
| 利益 $\ge 0.8\times$ ATR | SLをブレークイーブンに移動（+0.1x ATRオフセット） |
| 利益 $\ge 1.2\times$ ATR | SLを $0.7\times$ ATR トレーリング |
| ギブバック $\ge 40\%$ MFE または $\ge 0.6\times$ ATR | 自動クローズ（利益確定・ギブバック保護） |
---

## ⚙️ 設定

### cBotパラメータ

#### TMS設定（マルチタイムフレーム）
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| TMS Timeframe (Macro) | Hour (H1) | マクロトレンドバイアス時間軸（H1, H4, M15など） |
| RSI Period | 6 | RSI計算期間 |
| Red Period | 6 | シグナルライン期間 |

#### Stochastic設定
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| %K Period | 6 | 高速ストキャスティクス |
| %D Period | 6 | 低速ストキャスティクス |
| Slowing | 4 | スムージングファクター |

#### エントリーフィルタ
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| Max Bars After Cross | 5 | エントリーウィンドウ |
| Min Angle Delta | 0.0 | 角度フィルタ（0=オフ） |
| Min Decisive Breakout | 10.0 pips | ブレイクアウト強度（XAUUSD向けに最適化） |

#### エグジット管理
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| Flat Threshold | 0.01 | TDI平坦度 |
| Breakeven Trigger | 0.8x ATR | SLを建値に移動する利益 |
| Breakeven Offset | 0.1x ATR | ブレークイーブン確保利益 |
| Trail Trigger | 1.2x ATR | トレーリング開始の利益 |
| Trail Distance | 0.7x ATR | 価格からのSL距離 |
#### セッション
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| Session Start Hour | 13 (UTC) | ニューヨークオープン（冬時間UTC） |
| Session End Hour | 21 (UTC) | ニューヨーククローズ（EOD強制決済） |
| Opening Range | 15 min | OR計算ウィンドウ |
| Min OR Width | 20.0 pips | 最小OR幅 |
| ORB Buffer | 3.0 pips | ダマシ防止バッファ |
| DST Rule | US | 自動サマータイム調整 |

#### リスク管理 (ダイナミックサイジング & ATR)
| パラメータ | デフォルト | 説明 |
|------------|------------|------|
| Use ATR for SL/TP | true | ATRに基づいた動的SL/TPを計算 |
| ATR Period | 14 | ATRの計算期間 |
| ATR SL Multiplier | 1.5 | ATRストップロス距離乗数 |
| ATR TP Multiplier | 2.0 | ATRテイクプロフィット距離乗数 |
| Risk per Trade (%) | 0.2 | 1トレードあたりのリスク許容割合 (%) |

#### ガードレール
| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| Min SL | 0.8x ATR | 最小ストップロス乗数 |
| Max SL | 3.0x ATR | 最大ストップロス乗数 |
| Min TP | 1.0x ATR | 最小テイクプロフィット乗数 |
| Max TP | 6.0x ATR | 最大テイクプロフィット乗数 |
| Max Giveback (ATR) | 0.6x ATR | ATR基準の決済強制ギブバック閾値 |
| Max Giveback (% MFE) | 0.40 (40%) | 最高含み益からの許容最大ギブバック割合 |
| Max Breakout Dist | 2.5x ATR | エントリーを許可する最大ブレイクアウト距離 |
| Max Dollar Risk | $12.00 | 1トレードあたりの最大損失金額制限 |
| Max Loss Streak | 3 | N回連続損失後にブロック |
| Bias Flip Exit | true | バイアス変化時の自動クローズ |
| Trend TP Disabled | true | トレンド相場で固定利確を自動解除 |

### アジアンレンジ・ジューダススイープ パラメータ表

| パラメータ | デフォルト | 説明 |
|:---|:---:|:---|
| `UseDirectAiApi` | `false` | `false` = ローカルサーバーHub (`http://127.0.0.1:8000`), `true` = クラウドAPI直結 |
| `UseAiGateMode` | `true` | 2段階ゲート：Judas Sweep方向判定 → AI Agentエントリー確認 |
| `enableIndicatorCloseInAiMode` | `false` | AI Gate Mode時のEMA 9/21交差による早期手仕舞いを無効化し、TP/SL、建値、トレーリング、AI判断（`CLOSE_ALL` / `ADJUST`）にポジション管理を一任 |
| `AiConfidenceThreshold` | `70.0%` | BUY/SELL注文を実行するために必要な最低AI信頼度 |
| `AiSlMinFloorPips` | `200.0` | 最低SL保護床（ゴールド $2.00）、ノイズによる狩りを防止 |
| `asianStartHour` | `0` | アジアセッション開始時間（UTC） |
| `asianEndHour` | `6` | アジアセッション終了時間（UTC） |
| `minAsianRangePips` | `50.0` | 有効なセットアップとみなす最小アジアレンジ幅 |
| `maxAsianRangePips` | `350.0` | 最大アジアレンジ幅（アジア時間ですでに大相場となった日をスキップ） |
| `londonStartHour` | `7` | ロンドンキルゾーン開始時間（UTC） |
| `londonEndHour` | `10` | ロンドンキルゾーン終了時間（UTC） |
| `nyStartHour` | `12` | ニューヨークキルゾーン開始時間（UTC） |
| `nyEndHour` | `16` | ニューヨークキルゾーン終了時間（UTC） |
| `sweepBufferPips` | `15.0` | アジア高値・安値を突き抜ける最小ヒゲ幅（pips） |
| `enableNewsFilter` | `true` | 通貨自動判定付きForexFactory高影響ニュースシールド（ゴールド/株価指数/暗号資産はUSD、為替はEUR/USD/GBP/JPY） |
| `pauseBeforeNewsMins` | `30` | 赤色（High Impact）ニュース発表前のエントリー停止時間（分） |
| `pauseAfterNewsMins` | `30` | 赤色（High Impact）ニュース発表後のエントリー停止時間（分） |
| `highImpactOnly` | `true` | 赤色ニュース（High Impact）のみをフィルター |
| `closePositionsBeforeNews` | `false` | 重要ニュース発表前に保有ポジションを一括決済 |
| `riskFactor` | `1.0` | 1トレードあたりの口座リスク配分割合 (%) (推奨: 0.5% – 1.0%) |
| `enableBreakEvenPrice` | `true` | 目標到達時にSLを自動で建値に移動 |
| `breakEvenTrigger` | `250.0 pips` | 建値移動を発動する利益距離（ゴールド $2.50） |
### 🏹 アジアンレンジ・ジューダススイープ推奨プリセット表 (Asian Range Judas Sweep)

| パラメータ | XAUUSD | GBPUSD | EURUSD | GBPJPY | EURJPY | BTCUSD | ETHUSD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **推奨時間枠** | `M15` | `M15` | `M15` | `M15` | `M15` | `M15` | `M15` |
| **アジア時間 (UTC)** | `00:00 - 06:00` | `00:00 - 06:00` | `00:00 - 06:00` | `00:00 - 06:00` | `00:00 - 06:00` | `00:00 - 06:00` | `00:00 - 06:00` |
| **キルゾーン (UTC)** | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` | `07-10h & 12:30-16h` |
| **アジアレンジ幅 (Min/Max)** | `200.0 / 8000.0 pips` | `15.0 / 45.0 pips` | `15.0 / 45.0 pips` | `25.0 / 70.0 pips` | `25.0 / 70.0 pips` | `10000.0 / 400000.0 pips` | `800.0 / 35000.0 pips` |
| **スイープヒゲ深さ (Buffer)** | `30.0 pips` | `3.5 pips` | `3.5 pips` | `5.0 pips` | `5.0 pips` | `1500.0 pips` | `150.0 pips` |
| **AI SL 保護床 (Floor)** | `200.0 pips` | `15.0 pips` | `15.0 pips` | `25.0 pips` | `25.0 pips` | `20000.0 pips` | `1500.0 pips` |
| **デフォルト損切り (SL)** | `350.0 pips` | `15.0 pips` | `15.0 pips` | `25.0 pips` | `25.0 pips` | `25000.0 pips` | `2000.0 pips` |
| **デフォルト利確 (TP)** | `700.0 pips` | `35.0 pips` | `35.0 pips` | `50.0 pips` | `50.0 pips` | `60000.0 pips` | `5000.0 pips` |
| **建値移動発動距離 (BE)** | `250.0 pips` | `20.0 pips` | `20.0 pips` | `30.0 pips` | `30.0 pips` | `25000.0 pips` | `2000.0 pips` |
| **最低 AI 信頼度スコア** | `70.0%` | `70.0%` | `70.0%` | `70.0%` | `70.0%` | `70.0%` | `70.0%` |
| **1トレードリスク割合** | `1.0%` | `1.0%` | `1.0%` | `1.0%` | `1.0%` | `1.0%` | `1.0%` |

### 📊 TMS + ORB 推奨プリセット表 (銘柄別)
#### Metals & Indices

| Parameter | XAUUSD | US30 | USTEC | DE40 |
| :--- | :--- | :--- | :--- | :--- |
| **Trading Session** | New York | New York (Index) | New York (Index) | London |
| **DST Rule** | `US` | `US` | `US` | `Europe` |
| **Min Decisive Breakout** | `100.0 pips` | `100.0 pips` | `80.0 pips` | `70.0 pips` |
| **Min OR Width** | `250.0 pips` | `300.0 pips` | `250.0 pips` | `200.0 pips` |
| **ORB Buffer** | `30.0 pips` | `50.0 pips` | `40.0 pips` | `35.0 pips` |
| **Breakeven Trigger** | `1.2x ATR` | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` |
| **Breakeven Offset** | `0.1x ATR` | `0.1x ATR` | `0.1x ATR` | `0.1x ATR` |
| **Trail Trigger** | `2.0x ATR` | `3.0x ATR` | `3.0x ATR` | `3.0x ATR` |
| **Trail Distance** | `1.0x ATR` | `1.5x ATR` | `1.5x ATR` | `1.5x ATR` |
| **Min SL / Max SL** | `0.8x / 3.0x ATR` | `1.5x / 4.5x ATR` | `1.5x / 4.5x ATR` | `1.5x / 4.5x ATR` |
| **Min TP / Max TP** | `1.0x / 6.0x ATR` | `2.0x / 8.0x ATR` | `2.0x / 8.0x ATR` | `2.0x / 8.0x ATR` |
| **Max Giveback** | `1.0x ATR` | `1.5x ATR` | `1.5x ATR` | `1.5x ATR` |
| **Recommended Timeframe** | `M15` | `M15` | `M15` | `M15` |
| **EMA Period** | `5` | `5` | `5` | `5` |
| **Post-TP Gate / Pullback** | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) |
| **TDI Bounce Trade** | `1.5` | `1.5` | `1.5` | `1.5` |
| **Partial Close at BE** | `0.5 (50%)` | `0.5 (50%)` | `0.5 (50%)` | `0.5 (50%)` |
| **Risk per Trade** | `0.2%` | `0.2%` | `0.2%` | `0.2%` |
| **Use ATR for SL/TP** | `true` | `true` | `true` | `true` |
| **ATR Period** | `14` | `14` | `14` | `14` |
| **ATR SL Multiplier** | `1.5x ATR` | `2.5x ATR` | `2.5x ATR` | `2.5x ATR` |
| **ATR TP Multiplier** | `2.0x ATR` | `3.5x ATR` | `3.5x ATR` | `3.5x ATR` |

#### Forex Majors

| Parameter | EURUSD | GBPUSD | USDJPY | USDCAD |
| :--- | :--- | :--- | :--- | :--- |
| **Trading Session** | London | London | Tokyo | New York |
| **DST Rule** | `Europe` | `Europe` | `None` | `US` |
| **Min Decisive Breakout** | `2.5 pips` | `3.5 pips` | `3.0 pips` | `3.5 pips` |
| **Min OR Width** | `5.0 pips` | `7.0 pips` | `5.0 pips` | `7.0 pips` |
| **ORB Buffer** | `1.0 pips` | `1.2 pips` | `1.0 pips` | `1.2 pips` |
| **Breakeven Trigger** | `1.2x ATR` | `1.2x ATR` | `1.2x ATR` | `1.2x ATR` |
| **Breakeven Offset** | `0.1x ATR` | `0.1x ATR` | `0.1x ATR` | `0.1x ATR` |
| **Trail Trigger** | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` |
| **Trail Distance** | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` |
| **Min SL / Max SL** | `0.8x / 3.0x ATR` | `0.8x / 3.0x ATR` | `0.8x / 3.0x ATR` | `0.8x / 3.0x ATR` |
| **Min TP / Max TP** | `1.0x / 6.0x ATR` | `1.0x / 6.0x ATR` | `1.0x / 6.0x ATR` | `1.0x / 6.0x ATR` |
| **Max Giveback** | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` |
| **Recommended Timeframe** | `M15` | `M15` | `M15` | `M15` |
| **EMA Period** | `5` | `5` | `5` | `5` |
| **Post-TP Gate / Pullback** | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) |
| **TDI Bounce Trade** | `1.5` | `1.5` | `1.5` | `1.5` |
| **Partial Close at BE** | `0.5 (50%)` | `0.5 (50%)` | `0.5 (50%)` | `0.5 (50%)` |
| **Risk per Trade** | `0.2%` | `0.2%` | `0.2%` | `0.2%` |
| **Use ATR for SL/TP** | `true` | `true` | `true` | `true` |
| **ATR Period** | `14` | `14` | `14` | `14` |
| **ATR SL Multiplier** | `1.5x ATR` | `1.5x ATR` | `1.5x ATR` | `1.5x ATR` |
| **ATR TP Multiplier** | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` |

#### Forex Crosses

| Parameter | GBPJPY | EURJPY | AUDJPY |
| :--- | :--- | :--- | :--- |
| **Trading Session** | London | London | Tokyo |
| **DST Rule** | `Europe` | `Europe` | `None` |
| **Min Decisive Breakout** | `5.0 pips` | `4.0 pips` | `3.0 pips` |
| **Min OR Width** | `10.0 pips` | `8.0 pips` | `5.0 pips` |
| **ORB Buffer** | `1.5 pips` | `1.2 pips` | `1.0 pips` |
| **Breakeven Trigger** | `1.2x ATR` | `1.2x ATR` | `1.2x ATR` |
| **Breakeven Offset** | `0.1x ATR` | `0.1x ATR` | `0.1x ATR` |
| **Trail Trigger** | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` |
| **Trail Distance** | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` |
| **Min SL / Max SL** | `0.8x / 3.0x ATR` | `0.8x / 3.0x ATR` | `0.8x / 3.0x ATR` |
| **Min TP / Max TP** | `1.0x / 6.0x ATR` | `1.0x / 6.0x ATR` | `1.0x / 6.0x ATR` |
| **Max Giveback** | `1.0x ATR` | `1.0x ATR` | `1.0x ATR` |
| **Recommended Timeframe** | `M15` | `M15` | `M15` |
| **EMA Period** | `5` | `5` | `5` |
| **Post-TP Gate / Pullback** | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) | `true` (`0.5x ATR`) |
| **TDI Bounce Trade** | `1.5` | `1.5` | `1.5` |
| **Partial Close at BE** | `0.5 (50%)` | `0.5 (50%)` | `0.5 (50%)` |
| **Risk per Trade** | `0.2%` | `0.2%` | `0.2%` |
| **Use ATR for SL/TP** | `true` | `true` | `true` |
| **ATR Period** | `14` | `14` | `14` |
| **ATR SL Multiplier** | `1.5x ATR` | `1.5x ATR` | `1.5x ATR` |
| **ATR TP Multiplier** | `2.0x ATR` | `2.0x ATR` | `2.0x ATR` |
### ポートフォリオマネージャー設定

`app/portfolio.py`を編集：

```python
class PortfolioConfig:
    MAX_POSITIONS = 4              # 最大オープンポジション
    MAX_CURRENCY_EXPOSURE = 2      # 通貨あたり最大ポジション
    MAX_CORRELATED_POSITIONS = 2   # 最大相関ポジション
    MAX_DAILY_LOSS = -200.0        # 日次損失制限（USD）
    MAX_MARGIN_USAGE_PCT = 50.0    # 最大マージン使用率
```

---

## 📡 APIドキュメント

### POST /trade

取引判断のメインエンドポイント。

**リクエスト**（cBotから）：
```json
{
  "bot_id": "eurusd_bot",
  "symbol": "EURUSD",
  "timeframe": "M15",
  "tms": {
    "bias": "BULLISH",
    "long_entry": true,
    "green_tf_value": 55.2,
    "green_tf_slope": 0.4
  },
  "orb": {
    "breakout_direction": "up",
    "breakout_distance_pips": 5.0,
    "is_decisive": true
  },
  "position": null,
  "session": {
    "phase": "active",
    "minutes_to_end": 180
  }
}
```

**レスポンス**（AIから）：
```json
{
  "action": "BUY",
  "volume_lots": 0.01,
  "sl_pips": 0,
  "tp_pips": 0,
  "reason": "TMS強気バイアス確認、ORB決定的ブレイクアウトUP（+5.0p）、モメンタム上昇"
}
```

### POST /portfolio/report

ポートフォリオ追跡のためのポジション変更を報告。

### GET /portfolio/status

現在のポートフォリオステータスを取得。

```bash
curl http://127.0.0.1:8000/portfolio/status
```

---

## 🛠️ 開発

### プロジェクト構造

```
AgentFxTrading/
├── app/
│   ├── llm_client.py      # LLM抽象化レイヤー
│   ├── server.py          # FastAPIサーバー
│   └── portfolio.py       # ポートフォリオリスク管理
├── cBot/
│   └── AiAgentBot.cs      # cTrader cBot
├── .env.example           # 環境テンプレート
├── requirements.txt       # Python依存関係
├── README.md              # ドキュメント（6言語）
└── portfolio.db           # SQLiteデータベース（自動作成）
```

### 新しいLLM Providerの追加

1. `app/llm_client.py`に新しいクラスを作成
2. `create_llm_client()`を更新

### プロンプトの改善

`app/server.py`の`SYSTEM_PROMPT`を編集して取引ロジックを調整。

---

## 📊 パフォーマンス

### バックテスト結果

> ⚠️ **免責事項**：過去のパフォーマンスは将来の結果を保証するものではありません。常にデモアカウントで先にテストしてください。

| 指標 | 値 |
|------|-----|
| 勝率 | ~55-65% |
| リスク/リワード | 平均1:2 |
| 最大ドローダウン | ~15% |
| シャープレシオ | ~1.2 |

### ライブ取引のヒント

1. **デモで開始**：常に先に戦略をテスト
2. **小さなポジションサイズ**：0.01ロットから開始
3. **毎日監視**：定期的にポートフォリオステータスを確認
4. **パラメータ調整**：市場状況に基づいて調整
5. **リスク管理**：1取引あたり2%以上リスクを取らない

---

## ☕ サポート＆おすすめブローカー

**AgentFxTrading** がお役に立ちましたら、プロジェクトの継続的な開発をぜひご支援ください：

- ☕ **Buy Me a Coffee**: サーバー費用やAIモデル検証のサポートとして、コーヒー1杯をごちそうしてください：[buymeacoffee.com/kaz126](https://buymeacoffee.com/kaz126)。

  [![Buy Me A Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=kaz126&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://buymeacoffee.com/kaz126)

- 📈 **おすすめ cTrader ブローカー**:  
  cBot による自動売買に最適な低遅延ブローカーをお探しなら、提携リンクより口座開設いただけます：**[IC Markets cTrader 口座開設](https://ic.com/?camp=95400)**。  
  *特徴：Rawスプレッド 0.0 pips〜、豊富な流動性、極小レイテンシー約定、cTrader / cBot 自動売買に完全最適化。*

---
## 🤝 貢献

貢献を歓迎します！以下があなたが助けられる方法です：

### 貢献方法

1. **リポジトリをスター** ⭐ - サポートを示す
2. **バグを報告** 🐛 - イシューをオープン
3. **機能を提案** 💡 - 機能リクエストをオープン
4. **PRを提出** 🔧 - コード貢献
5. **ドキュメントを改善** 📚 - ドキュメント改善
6. **結果を共有** 📈 - バックテスト/ライブ結果を共有

### コミュニティ

- 💬 [ディスカッション](https://github.com/yourusername/AgentFxTrading/discussions)
- 🐛 [イシュー](https://github.com/yourusername/AgentFxTrading/issues)
- 📧 メール：your-email@example.com

---

## 📄 ライセンス

このプロジェクトはMITライセンスの下でライセンスされています - 詳細は[LICENSE](LICENSE)ファイルを参照してください。

---

## 🙏 謝辞

- **TMS戦略**：プロフェッショナルなTMS方法論に基づく
- **cTrader**：優れたAPIの提供
- **オープンソースコミュニティ**：素晴らしいライブラリとツール

---

## 📈 スター履歴

[![Star History Chart](https://api.star-history.com/svg?repos=yourusername/AgentFxTrading&type=Date)](https://star-history.com/#yourusername/AgentFxTrading&Date)

---

<div align="center">

**このプロジェクトが役に立つと思ったら、⭐と[コーヒーのサポート](https://buymeacoffee.com/kaz126)をよろしくお願いします！**

[⬆ トップに戻る](#-agentfxtrading---ai搭載自動取引システム)

</div>
