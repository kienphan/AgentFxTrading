# 🐳 Docker Instance Catalog — all 45 presets

Every instance AgentFxTrading ships: **15 symbols × 3 strategies = 45 `docker run` commands**, written out in full.

This file is the long form of the dashboard's **Docker Bot Management → Setup Instances** grid. Ticking a cell there
runs exactly the command printed here. Both come from [`app/cbot_presets.py`](../app/cbot_presets.py), which is the
single source of truth for strategy flags — `tests/test_cbot_presets.py` fails the build if this file and that module
drift apart.

> **Prerequisites live in the README.** Install the server, write the cTID password file and build the three `.algo`
> packages first: [Quick Start → Setup & Run cBot](../README.md#4-setup--run-cbot). Come back here once
> `cBot/AiAgentBot.algo`, `cBot/AsianRangeJudasSweepBot.algo` and `cBot/FlowRsiBot.algo` exist.

---

## 📋 Contents

- [Adapting a block](#-adapting-a-block)
- [Container naming](#-container-naming)
- [The 45-cell grid](#-the-45-cell-grid)
- [TMS + ORB — `AiAgentBot.algo`](#-tms--orb--aiagentbotalgo)
- [Asian Range Judas Sweep — `AsianRangeJudasSweepBot.algo`](#-asian-range-judas-sweep--asianrangejudassweepbotalgo)
- [FlowRSI — `FlowRsiBot.algo`](#-flowrsi--flowrsibotalgo)
- [Running demo and live side by side](#-running-demo-and-live-side-by-side)

---

## ✏️ Adapting a block

Every command below is written for a demo account. Five placeholders are yours to replace; the strategy flags
underneath them are the preset and should be copied as-is.

| Placeholder | Replace with |
| :--- | :--- |
| `--ctid=your_email@example.com` | The email of your cTID login. |
| `--account=YOUR_ACCOUNT_ID` | Your cTrader account number, e.g. `10101649`. |
| `--pwd-file=/root/ctrader_data/ctid_pwd` | Path to the `chmod 600` file holding that cTID's password. |
| `--AccountLabel="demo"` | `demo` or `live` — the cBot tags every trade with it and routes live data to `/real/dashboard`. |
| `--name` / `--BotId` `cbot-demo-…` | Swap the `demo` slug for your own account slug so a demo and a live bot on the same symbol never collide. |

Two flags are worth a second look before you go live:

- **`--RiskPerTradePercent`** (TMS+ORB) / **`--riskFactor`** (Judas) / **`--RiskPercentage`** + **`--MaxRiskPerTradeMoney`**
  (FlowRSI) — the presets are sized for a demo balance. Cut them to `0.1`–`0.2` on real capital.
- **`--network host`** assumes the API server runs on the same machine as the container. On a split setup, drop it and
  point `--ApiUrl` / `--DashboardServerUrl` at the server's reachable address instead.

---

## 🏷️ Container naming

The dashboard names containers `cbot-<account slug>-<symbol>-<session>[-<strategy>]`, and the blocks below follow the
same scheme with `demo` as the slug:

| Strategy | Pattern | Example |
| :--- | :--- | :--- |
| TMS + ORB | `cbot-<slug>-<symbol>-<session>` | `cbot-demo-xauusd-newyork` |
| Judas Sweep | `cbot-<slug>-<symbol>-KZ-london-ny-judas` | `cbot-demo-xauusd-KZ-london-ny-judas` |
| FlowRSI | `cbot-<slug>-<symbol>-all-flowrsi` | `cbot-demo-xauusd-all-flowrsi` |

The name doubles as `--BotId`, so `docker ps` tells you the account, symbol, session and strategy of every bot without
opening its flags — and the dashboard can match a running container back to its config row.

---

## 🗺️ The 45-cell grid

`period` · session for each cell. ✅ = a block the READMEs spelled out verbatim; 🧮 = derived from the nearest block,
scaled by pip size (indices `0.1`, XAU / BTC / ETH / JPY `0.01`, forex `0.0001`).

| Symbol | TMS + ORB | Judas Sweep | FlowRSI |
| :--- | :--- | :--- | :--- |
| **XAUUSD** | ✅ `m15` · New York | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **EURUSD** | ✅ `m15` · London | ✅ `m15` · London + NY killzones | ✅ `m15` · All sessions |
| **GBPUSD** | ✅ `m15` · London | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **USDJPY** | ✅ `m15` · Tokyo | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **GBPJPY** | ✅ `m15` · London | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **EURJPY** | ✅ `m15` · London | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **USDCAD** | ✅ `m15` · New York | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **AUDUSD** | ✅ `m15` · Tokyo | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **AUDJPY** | ✅ `m15` · Tokyo | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **US30** | ✅ `m15` · New York | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **USTEC** | ✅ `m5` · New York | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **DE40** | ✅ `m15` · London | 🧮 `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **UK100** | ✅ `m15` · London | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **BTCUSD** | 🧮 `m15` · New York | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |
| **ETHUSD** | 🧮 `m15` · New York | ✅ `m15` · London + NY killzones | 🧮 `m15` · All sessions |

---

## 📈 TMS + ORB — `AiAgentBot.algo`

Trend Momentum Signal plus an Opening Range Breakout, gated by the AI server. One bot per symbol per
session; ATR drives every stop, target and trail.

### XAUUSD — TMS+ORB

`m15` · New York

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-xauusd-newyork \
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
  --BotId="cbot-demo-xauusd-newyork" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
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

### EURUSD — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-eurusd-london \
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
  --BotId="cbot-demo-eurusd-london" \
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

### GBPUSD — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-gbpusd-london \
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
  --BotId="cbot-demo-gbpusd-london" \
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

### USDJPY — TMS+ORB

`m15` · Tokyo

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-usdjpy-tokyo \
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
  --BotId="cbot-demo-usdjpy-tokyo" \
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

### GBPJPY — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-gbpjpy-london \
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
  --BotId="cbot-demo-gbpjpy-london" \
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

### EURJPY — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-eurjpy-london \
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
  --BotId="cbot-demo-eurjpy-london" \
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

### USDCAD — TMS+ORB

`m15` · New York

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-usdcad-newyork \
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
  --BotId="cbot-demo-usdcad-newyork" \
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

### AUDUSD — TMS+ORB

`m15` · Tokyo

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-audusd-tokyo \
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
  --BotId="cbot-demo-audusd-tokyo" \
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

### AUDJPY — TMS+ORB

`m15` · Tokyo

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-audjpy-tokyo \
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
  --BotId="cbot-demo-audjpy-tokyo" \
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

### US30 — TMS+ORB

`m15` · New York

Documented block — the flags the README shipped, unchanged.

*On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-us30-newyork \
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
  --BotId="cbot-demo-us30-newyork" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --TmsTimeFrame="Hour" \
  --EmaPeriod=5 \
  --SessionName="newyork_index" \
  --OrbStartHour=14 \
  --OrbStartMinute=30 \
  --SessionEndHour=21 \
  --SessionDstRule="US" \
  --MinDecisiveBreakoutPips=30.0 \
  --MinOrWidthPips=80.0 \
  --OrbBufferPips=15.0 \
  --BreakevenTriggerAtr=1.6 \
  --BreakevenOffsetAtr=0.2 \
  --TrailTriggerAtr=2.2 \
  --TrailDistanceAtr=1.3 \
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

### USTEC / NAS100 — TMS+ORB

`m5` · New York

Documented block — the flags the README shipped, unchanged.

*Broker alias: some brokers name this symbol `NAS100`.*

```bash
docker run -d \
  --name cbot-demo-ustec-newyork \
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
  --BotId="cbot-demo-ustec-newyork" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --TmsTimeFrame="Hour" \
  --EmaPeriod=5 \
  --SessionName="newyork_index" \
  --OrbStartHour=14 \
  --OrbStartMinute=30 \
  --SessionEndHour=21 \
  --SessionDstRule="US" \
  --MinDecisiveBreakoutPips=25.0 \
  --MinOrWidthPips=70.0 \
  --OrbBufferPips=12.0 \
  --BreakevenTriggerAtr=1.6 \
  --BreakevenOffsetAtr=0.2 \
  --TrailTriggerAtr=2.2 \
  --TrailDistanceAtr=1.3 \
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

### DE40 / GER40 — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

*Broker alias: some brokers name this symbol `GER40`.*

```bash
docker run -d \
  --name cbot-demo-de40-london \
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
  --BotId="cbot-demo-de40-london" \
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
  --BreakevenTriggerAtr=1.6 \
  --BreakevenOffsetAtr=0.2 \
  --TrailTriggerAtr=2.2 \
  --TrailDistanceAtr=1.3 \
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

### UK100 / GB100 — TMS+ORB

`m15` · London

Documented block — the flags the README shipped, unchanged.

*Broker alias: some brokers name this symbol `GB100`. On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-uk100-london \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AiAgentBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=UK100 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-uk100-london" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --TmsTimeFrame="Hour" \
  --EmaPeriod=5 \
  --SessionName="london" \
  --OrbStartHour=8 \
  --SessionEndHour=16 \
  --SessionDstRule="Europe" \
  --MinDecisiveBreakoutPips=25.0 \
  --MinOrWidthPips=120.0 \
  --OrbBufferPips=15.0 \
  --BreakevenTriggerAtr=1.6 \
  --BreakevenOffsetAtr=0.2 \
  --TrailTriggerAtr=2.2 \
  --TrailDistanceAtr=1.3 \
  --PartialCloseRatio=0.5 \
  --MinSlAtr=1.5 \
  --MaxSlAtr=4.5 \
  --MinTpAtr=2.0 \
  --MaxTpAtr=8.0 \
  --MaxGivebackAtr=0.6 \
  --EnablePostTpGate=true \
  --PostTpPullbackAtr=0.5 \
  --BounceTradeEnabled=true \
  --BounceDistanceThreshold=1.5 \
  --RiskPerTradePercent=0.2 \
  --TrendTpDisabled=true
```

### BTCUSD — TMS+ORB

`m15` · New York

Derived from **XAUUSD TMS+ORB** ×50 (dollar volatility): `$100 / $200 / $25` breakout / OR width / buffer.

```bash
docker run -d \
  --name cbot-demo-btcusd-newyork \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AiAgentBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=BTCUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-btcusd-newyork" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --TmsTimeFrame="Hour" \
  --EmaPeriod=5 \
  --SessionName="newyork" \
  --OrbStartHour=13 \
  --SessionEndHour=21 \
  --SessionDstRule="US" \
  --MinDecisiveBreakoutPips=10000.0 \
  --MinOrWidthPips=20000.0 \
  --OrbBufferPips=2500.0 \
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

### ETHUSD — TMS+ORB

`m15` · New York

Derived from **XAUUSD TMS+ORB** ×4: `$8 / $16 / $2` breakout / OR width / buffer.

```bash
docker run -d \
  --name cbot-demo-ethusd-newyork \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AiAgentBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=ETHUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-ethusd-newyork" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --TmsTimeFrame="Hour" \
  --EmaPeriod=5 \
  --SessionName="newyork" \
  --OrbStartHour=13 \
  --SessionEndHour=21 \
  --SessionDstRule="US" \
  --MinDecisiveBreakoutPips=800.0 \
  --MinOrWidthPips=1600.0 \
  --OrbBufferPips=200.0 \
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

---

## 🏹 Asian Range Judas Sweep — `AsianRangeJudasSweepBot.algo`

ICT Smart Money Concepts: build the Asian range (00:00–06:00 UTC), wait for the London or New York killzone to
sweep it, then trade the reversal. All 15 cells run `m15` with `--UseAiGateMode=true`.

`breakEvenTrigger` is deliberately not shipped: `breakEvenMode` defaults to `Risk_Reward_Ratio` and the bot reads
that pip value only in its `Fixed_Pips` branch, so the per-symbol numbers the README listed never did anything —
`breakEvenRrTrigger` is what gates the move. Break-even stays on via `--enableBreakEvenPrice=true`.

### XAUUSD — Judas Sweep

`m15` · London + NY killzones

Documented block — the README flags, minus `breakEvenTrigger`, with the sweep buffer raised 30 → 500 pips ($5.00) to match the auto-scale the bot itself wants for gold.

```bash
docker run -d \
  --name cbot-demo-xauusd-KZ-london-ny-judas \
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
  --BotId="cbot-demo-xauusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-xauusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=200.0 \
  --maxAsianRangePips=8000.0 \
  --sweepBufferPips=500.0 \
  --AiSlMinFloorPips=200.0 \
  --stoplossPip=200.0 \
  --takeprofitPip=450.0 \
  --enableBreakEvenPrice=true
```

### EURUSD — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-eurusd-KZ-london-ny-judas \
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
  --BotId="cbot-demo-eurusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-eurusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=15.0 \
  --maxAsianRangePips=45.0 \
  --sweepBufferPips=3.5 \
  --AiSlMinFloorPips=15.0 \
  --stoplossPip=15.0 \
  --takeprofitPip=35.0 \
  --enableBreakEvenPrice=true
```

### GBPUSD — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-gbpusd-KZ-london-ny-judas \
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
  --BotId="cbot-demo-gbpusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-gbpusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=15.0 \
  --maxAsianRangePips=45.0 \
  --sweepBufferPips=3.5 \
  --AiSlMinFloorPips=15.0 \
  --stoplossPip=15.0 \
  --takeprofitPip=35.0 \
  --enableBreakEvenPrice=true
```

### USDJPY — Judas Sweep

`m15` · London + NY killzones

Derived — reuses the **EURUSD / GBPUSD** major block unchanged.

```bash
docker run -d \
  --name cbot-demo-usdjpy-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USDJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-usdjpy-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-usdjpy-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=15.0 \
  --maxAsianRangePips=45.0 \
  --sweepBufferPips=3.5 \
  --AiSlMinFloorPips=15.0 \
  --stoplossPip=15.0 \
  --takeprofitPip=35.0 \
  --enableBreakEvenPrice=true
```

### GBPJPY — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-gbpjpy-KZ-london-ny-judas \
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
  --BotId="cbot-demo-gbpjpy-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-gbpjpy-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=25.0 \
  --maxAsianRangePips=70.0 \
  --sweepBufferPips=5.0 \
  --AiSlMinFloorPips=25.0 \
  --stoplossPip=25.0 \
  --takeprofitPip=50.0 \
  --enableBreakEvenPrice=true
```

### EURJPY — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-eurjpy-KZ-london-ny-judas \
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
  --BotId="cbot-demo-eurjpy-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-eurjpy-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=25.0 \
  --maxAsianRangePips=70.0 \
  --sweepBufferPips=5.0 \
  --AiSlMinFloorPips=25.0 \
  --stoplossPip=25.0 \
  --takeprofitPip=50.0 \
  --enableBreakEvenPrice=true
```

### USDCAD — Judas Sweep

`m15` · London + NY killzones

Derived — reuses the **EURUSD / GBPUSD** major block unchanged.

```bash
docker run -d \
  --name cbot-demo-usdcad-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USDCAD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-usdcad-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-usdcad-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=15.0 \
  --maxAsianRangePips=45.0 \
  --sweepBufferPips=3.5 \
  --AiSlMinFloorPips=15.0 \
  --stoplossPip=15.0 \
  --takeprofitPip=35.0 \
  --enableBreakEvenPrice=true
```

### AUDUSD — Judas Sweep

`m15` · London + NY killzones

Derived — reuses the **EURUSD / GBPUSD** major block unchanged.

```bash
docker run -d \
  --name cbot-demo-audusd-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=AUDUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-audusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-audusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=15.0 \
  --maxAsianRangePips=45.0 \
  --sweepBufferPips=3.5 \
  --AiSlMinFloorPips=15.0 \
  --stoplossPip=15.0 \
  --takeprofitPip=35.0 \
  --enableBreakEvenPrice=true
```

### AUDJPY — Judas Sweep

`m15` · London + NY killzones

Derived — reuses the **GBPJPY / EURJPY** cross block unchanged.

```bash
docker run -d \
  --name cbot-demo-audjpy-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=AUDJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-audjpy-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-audjpy-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=25.0 \
  --maxAsianRangePips=70.0 \
  --sweepBufferPips=5.0 \
  --AiSlMinFloorPips=25.0 \
  --stoplossPip=25.0 \
  --takeprofitPip=50.0 \
  --enableBreakEvenPrice=true
```

### US30 — Judas Sweep

`m15` · London + NY killzones

Derived from **UK100 Judas** ×5 by daily range (60 / 400 index points).

*On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-us30-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=US30 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-us30-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-us30-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=600.0 \
  --maxAsianRangePips=4000.0 \
  --sweepBufferPips=150.0 \
  --AiSlMinFloorPips=750.0 \
  --stoplossPip=750.0 \
  --takeprofitPip=1750.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

### USTEC / NAS100 — Judas Sweep

`m15` · London + NY killzones

Derived from **UK100 Judas** ×4 by daily range (50 / 300 index points).

*Broker alias: some brokers name this symbol `NAS100`.*

```bash
docker run -d \
  --name cbot-demo-ustec-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USTEC \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-ustec-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-ustec-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=500.0 \
  --maxAsianRangePips=3000.0 \
  --sweepBufferPips=120.0 \
  --AiSlMinFloorPips=600.0 \
  --stoplossPip=600.0 \
  --takeprofitPip=1400.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

### DE40 / GER40 — Judas Sweep

`m15` · London + NY killzones

Derived from **UK100 Judas** ×3 by daily range (35 / 250 index points).

*Broker alias: some brokers name this symbol `GER40`.*

```bash
docker run -d \
  --name cbot-demo-de40-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=DE40 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-de40-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-de40-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=350.0 \
  --maxAsianRangePips=2500.0 \
  --sweepBufferPips=90.0 \
  --AiSlMinFloorPips=450.0 \
  --stoplossPip=450.0 \
  --takeprofitPip=1000.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

### UK100 / GB100 — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

*Broker alias: some brokers name this symbol `GB100`. On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-uk100-KZ-london-ny-judas \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/AsianRangeJudasSweepBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=UK100 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-uk100-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-uk100-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=120.0 \
  --maxAsianRangePips=800.0 \
  --sweepBufferPips=30.0 \
  --AiSlMinFloorPips=150.0 \
  --stoplossPip=150.0 \
  --takeprofitPip=350.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

### BTCUSD — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-btcusd-KZ-london-ny-judas \
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
  --BotId="cbot-demo-btcusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-btcusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=10000.0 \
  --maxAsianRangePips=400000.0 \
  --sweepBufferPips=1500.0 \
  --AiSlMinFloorPips=20000.0 \
  --stoplossPip=25000.0 \
  --takeprofitPip=60000.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

### ETHUSD — Judas Sweep

`m15` · London + NY killzones

Documented block — the flags the README shipped, minus `breakEvenTrigger`.

```bash
docker run -d \
  --name cbot-demo-ethusd-KZ-london-ny-judas \
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
  --BotId="cbot-demo-ethusd-KZ-london-ny-judas" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --label="cbot-demo-ethusd-KZ-london-ny-judas" \
  --DashboardServerUrl="http://127.0.0.1:8000" \
  --UseDirectAiApi=false \
  --UseAiGateMode=true \
  --minAsianRangePips=800.0 \
  --maxAsianRangePips=35000.0 \
  --sweepBufferPips=150.0 \
  --AiSlMinFloorPips=1500.0 \
  --stoplossPip=2000.0 \
  --takeprofitPip=5000.0 \
  --enableBreakEvenPrice=true \
  --riskFactor=0.2
```

---

## 🌊 FlowRSI — `FlowRsiBot.algo`

Nested fast/slow RSI with SMC structure, fair-value-gap detection and a premium/discount filter. The RSI and
risk flags never change per symbol — only the four pip-sized filters do.

### XAUUSD — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; only the four pip filters are resized for gold (`$0.5 / $0.5 / $3 / $0.2`).

```bash
docker run -d \
  --name cbot-demo-xauusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=XAUUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-xauusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=50.0 \
  --MaxSpreadPips=50.0 \
  --TrailingStopDistancePips=300.0 \
  --BreakEvenExtraPips=20.0
```

### EURUSD — FlowRSI

`m15` · All sessions

Documented block — the flags the README shipped, unchanged.

```bash
docker run -d \
  --name cbot-demo-eurusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=EURUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-eurusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### GBPUSD — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-gbpusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=GBPUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-gbpusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### USDJPY — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-usdjpy-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USDJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-usdjpy-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### GBPJPY — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-gbpjpy-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=GBPJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-gbpjpy-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### EURJPY — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-eurjpy-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=EURJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-eurjpy-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### USDCAD — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-usdcad-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USDCAD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-usdcad-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### AUDUSD — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-audusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=AUDUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-audusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### AUDJPY — FlowRSI

`m15` · All sessions

Derived — RSI, SMC and risk flags are symbol-agnostic, so this is the **EURUSD FlowRSI** block unchanged; the cBot's own pip defaults apply (`FvgMinPips=2`, `MaxSpreadPips=30`, `TrailingStopDistancePips=15`, `BreakEvenExtraPips=0.5`).

```bash
docker run -d \
  --name cbot-demo-audjpy-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=AUDJPY \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-audjpy-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true
```

### US30 — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for a 0.1-pip index (`10 / 6 / 30 / 1` points).

*On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-us30-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=US30 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-us30-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=100.0 \
  --MaxSpreadPips=60.0 \
  --TrailingStopDistancePips=300.0 \
  --BreakEvenExtraPips=10.0
```

### USTEC — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for a 0.1-pip index (`8 / 5 / 25 / 1` points).

*Broker alias: some brokers name this symbol `NAS100`.*

```bash
docker run -d \
  --name cbot-demo-ustec-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=USTEC \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-ustec-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=80.0 \
  --MaxSpreadPips=50.0 \
  --TrailingStopDistancePips=250.0 \
  --BreakEvenExtraPips=10.0
```

### DE40 — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for a 0.1-pip index (`5 / 4 / 20 / 1` points).

*Broker alias: some brokers name this symbol `GER40`.*

```bash
docker run -d \
  --name cbot-demo-de40-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=DE40 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-de40-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=50.0 \
  --MaxSpreadPips=40.0 \
  --TrailingStopDistancePips=200.0 \
  --BreakEvenExtraPips=10.0
```

### UK100 — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for a 0.1-pip index (`3 / 3 / 10 / 0.5` points).

*Broker alias: some brokers name this symbol `GB100`. On cTrader 1 pip = 0.1 index point.*

```bash
docker run -d \
  --name cbot-demo-uk100-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=UK100 \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-uk100-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=30.0 \
  --MaxSpreadPips=30.0 \
  --TrailingStopDistancePips=100.0 \
  --BreakEvenExtraPips=5.0
```

### BTCUSD — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for crypto (`$50 / $50 / $300 / $10`).

```bash
docker run -d \
  --name cbot-demo-btcusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=BTCUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-btcusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=5000.0 \
  --MaxSpreadPips=5000.0 \
  --TrailingStopDistancePips=30000.0 \
  --BreakEvenExtraPips=1000.0
```

### ETHUSD — FlowRSI

`m15` · All sessions

Derived from **EURUSD FlowRSI**; pip filters resized for crypto (`$3 / $5 / $20 / $1`).

```bash
docker run -d \
  --name cbot-demo-ethusd-all-flowrsi \
  --restart unless-stopped \
  --network host \
  -v $(pwd):/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/FlowRsiBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=ETHUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-ethusd-all-flowrsi" \
  --ApiUrl="http://127.0.0.1:8000/trade" \
  --AccountLabel="demo" \
  --FastRsiPeriod=7 \
  --SlowRsiPeriod=14 \
  --EnableSmcFilter=true \
  --EnableFvgDetection=true \
  --EnablePremiumDiscountFilter=true \
  --RiskPercentage=0.5 \
  --MaxRiskPerTradeMoney=50.0 \
  --TargetRiskReward=1.5 \
  --UseAiGateMode=true \
  --FvgMinPips=300.0 \
  --MaxSpreadPips=500.0 \
  --TrailingStopDistancePips=2000.0 \
  --BreakEvenExtraPips=100.0
```

---

## 👯 Running demo and live side by side

Nothing stops the same symbol × strategy cell from running on two accounts — the presets are account-agnostic. Four
things must differ per account:

1. **`--name` and `--BotId`** — swap the `demo` slug (`cbot-live-xauusd-newyork`). Container names are unique per host.
2. **`--account`** — the live account number.
3. **`--AccountLabel`** — `live` (or `live-main`), so trades are tagged and routed to `/real/dashboard`.
4. **`--pwd-file`** — a dedicated password file if the live account sits under a different cTID.

Then register both accounts in `.env` so the dashboard renders a tab for each:

```bash
DASHBOARD_ACCOUNTS=demo-10101649|10101649|demo|Demo Account;live-88888888|88888888|live|Live Main
```

And lower the risk flags on the live side — see [Adapting a block](#-adapting-a-block).

> 💡 **Prefer clicking?** The dashboard's **Docker Bot Management → Setup Instances** screen does all of this for you:
> pick (or create) a cTrader account, tick the symbol × strategy cells you want, and press **Create instances**. It
> generates exactly the commands above, saves them as bot configs and starts the containers. The cTID password is
> written to `$CTRADER_HOME/ctrader_data/ctid_<slug>_pwd` (mode 0600) and never stored in the database.
