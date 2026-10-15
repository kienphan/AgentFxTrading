# 🧪 MTF forward test — `MtfChartBot` (demo, XAUUSD)

A deliberate **exception to the 45-cell grid**: one discretionary multi-timeframe AI bot on a demo
account, trading nothing but its own journal until the evidence says otherwise. It is *not* a preset in
[`app/cbot_presets.py`](../app/cbot_presets.py) and it must never be installed on a live account — the
cBot stops itself and the server gate refuses every non-`WAIT` action when `Account.IsLive` is true.

| Piece | Where |
| :--- | :--- |
| cBot (M15 chart, D1/H4/H1/M15 closed bars) | [`cBot/MtfChartBot.cs`](../cBot/MtfChartBot.cs) |
| Analyst prompt + gate + journal | [`app/mtf_agent.py`](../app/mtf_agent.py) |
| `python -m app.mtf_review` | [`app/mtf_review.py`](../app/mtf_review.py) |
| Routes | `POST /trade/mtf`, `POST /trade/mtf/event` (registered in [`app/server.py`](../app/server.py)) |
| Tests | `tests/test_mtf_agent.py`, `tests/test_mtf_chart_bot.py`, `tests/test_mtf_journal.py`, `tests/test_mtf_review.py` |

**How a decision flows.** Every closed M15 bar the cBot posts closed D1/H4/H1/M15 candles, spread, the
open position/pending order and the day's closed PnL to `/trade/mtf`. The LLM analyses D1 → H4 → H1 →
M15 and returns structured JSON (bias per timeframe, action, entry, stop, target, confidence). The
server then **authorises or refuses** it: live account, spread ceiling, daily-loss ceiling,
`confidence >= 65`, `risk_reward >= MinRr`, order geometry (a limit must sit below the bid, a stop above
the ask, a market order may not chase) and one-order-at-a-time. Only an authorised reply reaches the
cBot, which sizes by risk % and places the order. Every decision — `WAIT` included — is appended to
`trades/mtf_journal.jsonl` **before** the cBot can send anything, and the `client_order_id` travels in
the order comment so the fill and the close land on the same journal record.

---

## 1. Server side

`app/server.py` changed, so the routes only exist after a restart:

```bash
systemctl restart agentfx.service
systemctl status agentfx.service --no-pager
curl -s http://127.0.0.1:8000/api/watchdog/status

# The two new routes are live:
curl -s http://127.0.0.1:8000/openapi.json | grep -o '/trade/mtf[a-z/]*' | sort -u
```

The journal is written to `MTF_JOURNAL_PATH`, else `trades/mtf_journal.jsonl` **relative to the service
working directory** (`WorkingDirectory=${REPO_DIR}` in [`scripts/install.sh`](../scripts/install.sh)),
i.e. `/root/AgentFxTrading/trades/mtf_journal.jsonl`. `trades/` is git-ignored: it is evidence, not code.

## 2. Build the `.algo`

Once per source change, on the host that holds `/root/cAlgo` (Docker with the cTrader console image —
the same flow as the README's quick start):

```bash
cd /root/AgentFxTrading

docker run --rm -v $(pwd):/workspace -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest create cbot MtfChartBot
cp cBot/MtfChartBot.cs /root/cAlgo/Sources/Robots/MtfChartBot/MtfChartBot/MtfChartBot.cs
docker run --rm -v $(pwd):/workspace -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest build /root/cAlgo/Sources/Robots/MtfChartBot/MtfChartBot/MtfChartBot.csproj
cp /root/cAlgo/Sources/Robots/MtfChartBot.algo cBot/MtfChartBot.algo
```

A build error is the only thing standing between the source and a runnable instance: `MtfChartBot.cs`
has never been compiled in CI, so compile it once by hand before wiring the container.

## 3. Run the container — XAUUSD demo

Replace the three placeholders (`--ctid`, `--account`, `--pwd-file`) with yours, exactly as in the
catalog.

```bash
docker run -d \
  --name cbot-demo-xauusd-mtf \
  --restart unless-stopped \
  --network host \
  -v /root/AgentFxTrading:/workspace \
  -v /root:/root \
  ghcr.io/spotware/ctrader-console:latest \
  run /workspace/cBot/MtfChartBot.algo \
  --ctid=your_email@example.com \
  --pwd-file=/root/ctrader_data/ctid_pwd \
  --account=YOUR_ACCOUNT_ID \
  --symbol=XAUUSD \
  --period=m15 \
  --full-access \
  --BotId="cbot-demo-xauusd-mtf" \
  --ApiUrl="http://127.0.0.1:8000/trade/mtf" \
  --AiTimeoutSeconds=120 \
  --RiskPercent=0.2 \
  --MinRr=2.0 \
  --MaxEntryDistancePips=0 \
  --MaxSpreadPips=40.0 \
  --MaxDailyLoss=500.0 \
  --PendingMaxM15Bars=8
```

Flag notes:

- `--period=m15` is mandatory: the cBot stops itself on any other chart timeframe (the M15 series is
  the entry timeframe; D1/H4/H1 are fetched through `MarketData.GetBars`).
- `--ApiUrl` must end in `/trade/mtf`; `/portfolio/report` and `/trade/mtf/event` are derived from it.
- `MtfChartBot` has **no** `--AccountLabel`/session parameters. Its `/portfolio/report` carries
  `account_number` and `account_type`, so the registry still attributes trades to the account and keeps
  the label already configured for it.
- `--RiskPercent=0.2` matches this repo's risk convention (the PR default was `0.5`); the cBot refuses
  to round size up and refuses a trade whose rounded risk would exceed the budget.
- `--AiTimeoutSeconds=120`: a full MTF reply with thinking was measured at ~69 s. Keep the server's
  `LLM_MTF_DEADLINE` (default 105 s) **below** this value, or the server keeps working on a reply the
  bot has already given up on.
- `--MaxEntryDistancePips=0` disables the cap. Set it (gold: 1 pip = $0.01, so 2000 pips = $20) to
  refuse an entry the analyst placed far away from the market; the gate applies the same limit
  through the `max_entry_distance_pips` field.
- `--MaxDailyLoss` now counts **only this bot's** own closed trades (`trade.Label == BotId`), not the
  other XAUUSD bots sharing the demo account.

Check it came up and logged in cleanly:

```bash
docker logs --tail 30 cbot-demo-xauusd-mtf
```

## 4. Register it for the dashboard and the watchdog

The watchdog only inspects containers that have a `cbot_configs` row, and the dashboard lists the same
rows. Install it through the API used by **Docker Bot Management** (one-line `run_command`, no
backslashes):

```bash
# same command as step 3, folded onto one line
export RUN_CMD='docker run -d --name cbot-demo-xauusd-mtf --restart unless-stopped --network host -v /root/AgentFxTrading:/workspace -v /root:/root ghcr.io/spotware/ctrader-console:latest run /workspace/cBot/MtfChartBot.algo --ctid=your_email@example.com --pwd-file=/root/ctrader_data/ctid_pwd --account=YOUR_ACCOUNT_ID --symbol=XAUUSD --period=m15 --full-access --BotId="cbot-demo-xauusd-mtf" --ApiUrl="http://127.0.0.1:8000/trade/mtf" --AiTimeoutSeconds=120 --RiskPercent=0.2 --MinRr=2.0 --MaxEntryDistancePips=0 --MaxSpreadPips=40.0 --MaxDailyLoss=500.0 --PendingMaxM15Bars=8'

/root/AgentFxTrading/.venv/bin/python - <<'PY'
import json, os, urllib.request
body = json.dumps({
    "name": "cbot-demo-xauusd-mtf",
    "description": "MTF AI forward test XAUUSD m15 — demo",
    "run_command": os.environ["RUN_CMD"],
}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/api/bots", data=body,
                             headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
```

The dashboard's **Add bot** form does the same thing with `run_command` pasted as one line.

The watchdog then restarts the container if it stops or its login gets stuck. It does **not** judge this
bot's bar feed: the stale-feed and bar-heartbeat checks key off `/trade` snapshots and `/ws/cbot` ticks,
and this bot speaks `/trade/mtf` instead. Silence on `/trade/mtf` is therefore invisible to the
watchdog — read the journal (below) rather than the health panel to know it is working.

## 5. Read the evidence

```bash
/root/AgentFxTrading/.venv/bin/python -m app.mtf_review
# or an explicit journal
/root/AgentFxTrading/.venv/bin/python -m app.mtf_review trades/mtf_journal.jsonl

tail -f /root/AgentFxTrading/trades/mtf_journal.jsonl | python3 -m json.tool --json-lines
```

`mtf_review` reports trade count, win rate, expectancy in R (costs included: R is net money over the
money the fill was sized to risk), total R, max drawdown in R, worst loss streak, exit reasons, manual
exit rate, entry slippage, actual-vs-intended risk, open trades, orphan closes, how many decisions were
approved vs `WAIT`, the top `WAIT` reasons, and `r_denominator`: how many trades were scored on the
journalled `risk_money` versus the older balance-percentage fallback. It also states its own sample
verdict: **under 30 trades is noise, under 100 is weak, 100+ is usable.**

### Sanitising a journal a backtest wrote into

Before the `running_mode` fix a cTrader backtest reached this endpoint, so `trades/mtf_journal.jsonl`
may hold simulated fills mixed with real ones. Nothing is deleted: the tool rewrites the journal with
the RealTime lines and moves the rest to `trades/mtf_journal.quarantine-<stamp>.jsonl`.

```bash
cd /root/AgentFxTrading
.venv/bin/python -m app.mtf_cleanup                # report only, writes nothing
.venv/bin/python -m app.mtf_cleanup --db           # + the accounts/open positions the DB holds
.venv/bin/python -m app.mtf_cleanup --apply        # move explicit backtest/optimisation lines out
.venv/bin/python -m app.mtf_cleanup --apply --include-unknown
```

A line written before the cBot sent `running_mode` is **unknown**, not proof of a backtest, so it only
moves with `--include-unknown`: if this bot has never been backtested, leave it out and keep the
evidence. Backtest decisions now go to `trades/mtf_backtest_journal.jsonl`
(`MTF_BACKTEST_JOURNAL_PATH`), which `mtf_review` never reads.

> **Rebuild `MtfChartBot.algo` before backtesting.** An old binary sends no `running_mode` and reports
> its simulated fills like live ones, so it still pollutes both the journal and the account. The
> separation only exists in a build that contains the fix.

`--db` is read-only and exists to show the other half of the damage: a backtest posted its simulated
fills to `/portfolio/report`, so `accounts.last_balance` / `last_equity` may hold simulated values and
stray positions may still be open. **Restore the balance and equity from cTrader**, do not hand-edit
them, and clear the leftover rows through the dashboard.

## 6. Limits to keep in mind before trusting it

Fixed after the 2026-09-29 review, so they are no longer open risks: backtest contamination
(`running_mode` + RealTime-only reporting), market orders approved with the price already past the stop
(judged at the fill price in both the gate and the cBot), the missing LLM deadline (`LLM_MTF_DEADLINE`),
`MODIFY_PENDING_ORDER` (now re-prices the pending order), the dropped just-closed bar (`ClosedIndexFor`
by time), the daily loss limit counting other bots (`trade.Label == BotId`), and the R denominator
(journalled `risk_money`).

What is still true:

1. **Cost and latency scale with symbols.** The payload is raw OHLC for D1(100)+H4(150)+H1(200)+M15(250)
   bars on *every* M15 bar, and a full reply took ~69 s with thinking. One symbol on M15 is ~96 calls a
   day; a second and third symbol multiply prompt tokens and provider rate-limit pressure. Higher
   timeframes only move every 16 (H4) / 96 (D1) M15 bars, so caching them is the obvious saving.
2. **No calendar is fed to the model.** The snapshot has a `news` field that the cBot never fills, so the
   prompt always reads "No economic calendar was supplied". The server-side blackout check that protects
   `/trade` ([`app/news_service.py`](../app/news_service.py)) is not applied to `/trade/mtf`.
3. **Position management is thin.** The analyst may only `HOLD_POSITION` or `CLOSE_POSITION`; there is no
   break-even, trailing stop, giveback floor or post-TP gate. Do not let an MTF decision override the
   deterministic SL/TP engine that the other bots rely on.
4. **A modified pending order keeps its original submission time**, so the bar-based expiry still retires
   it; an analyst that keeps nudging a stale order cannot keep it alive for ever.
5. **`client_order_id` rides in the order comment.** If the broker truncates comments, the fill no
   longer joins its decision and `mtf_review` counts it as an orphan. Check the journal's orphan count
   after the first few fills.
6. **`MIN_CONFIDENCE = 65` is a module constant**, not a per-symbol parameter, and `daily_pnl` is the
   day's *closed* PnL only.
7. **Live accounts stay refused.** That is a design decision in both the cBot and the gate; promoting
   this beyond a forward test is a separate change with its own review.

## 7. Tests

```bash
/root/AgentFxTrading/.venv/bin/pytest tests/test_mtf_agent.py tests/test_mtf_chart_bot.py \
  tests/test_mtf_journal.py tests/test_mtf_review.py tests/test_mtf_cleanup.py \
  tests/test_closed_bar_index.py
```

Server-side knobs (all optional, defaults in brackets):
`LLM_MTF_TIMEOUT` [40 s], `LLM_MTF_MAX_RETRIES` [1], `LLM_MTF_DEADLINE` [105 s],
`LLM_MTF_ENABLE_THINKING` [false], `MTF_JOURNAL_PATH`, `MTF_BACKTEST_JOURNAL_PATH`.
