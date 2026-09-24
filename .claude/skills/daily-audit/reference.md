# daily-audit reference

## FLAG codes

| FLAG | Meaning | Verify with | Usual cause |
|---|---|---|---|
| OPEN_NOT_IN_DB | The container traded `#id`, but no DB row has that `ctrader_id` | Container log around the time; agent log `Close position ignored … ctrader_id=<id>` | The open report failed (see REPORT_FAILED). The trade's P&L is missing from the DB and the daily-loss count |
| REPORT_FAILED | The bot could not POST `/portfolio/report` | Service restart and HOST BOOT times in the digest; other `POST /portfolio/report` responses in the same second in the journal | There is no retry (cBot `ReportPosition*`). Two usual triggers: (1) the report was sent while agentfx was down or restarting; (2) away from restarts, a transient connection error such as a pooled keep-alive socket the server had just closed. The cBot line prints only `ex.Message`, so it does not show which |
| CLOSE_NOT_APPLIED | The bot reported a close, but the DB row is still open | Agent log `Close position ignored`; the `ctrader_id` on the row | ID mismatch, or the report was lost |
| DB_OPEN_NOT_IN_LOGS | A DB row has no open line in its container's log | Look for the id across all containers: `sudo -n cat LogPath \| grep <id>` | The log is truncated or corrupt, or the row's bot_id is wrong |
| NO_TELEMETRY | The DB row is open, but the bot sends no P&L for it | Compare with the bot's `/api/bots` status and its container log | A ghost row (broker already closed it, and the close report was lost), or the bot is down |
| LEDGER_DRIFT | balance − Σ recorded pnl is not a round deposit | The digest line "P&L of trades missing from the DB … ledger drift …". If the two match, the lost reports explain the drift | Lost reports. A wrong pnl. Commission or swap not included. Any backfill SQL must mark estimated prices and point the user to cTrader History for exact ones |
| LIMIT_BYPASS | A position opened after a daily-loss scope had already crossed its limit on closed P&L alone | Agent log `Portfolio risk check` around the entry | The limit's value at that moment (it may have been changed), or a bug in the risk gate `app/portfolio.py` `check_risk` / `app/risk_limits.py` |
| RISK_OVER | Loss at SL is above 1.5× the configured risk % | Bot `run_command` (`cbot_configs`); cBot sizing log `[AI Risk Sizing]` | Minimum-lot clamp. Wrong pip value. Wrong risk param |
| DIRECTION_SKEW, SAME_BAR_CLUSTER, CHURN | 80%+ one side; 4+ entries in one M15 bar; 5+ trades by one bot | The day's price moves; `[SNAPSHOT …]` and `[LLM DECISION]` lines | Strategy or correlated risk. Not a code bug unless a filter should have blocked it |
| TP_WRONG_SIDE, NO_SL, FILL_FAR_FROM_LEVEL, INSTANT_CLOSE, PNL_SIGN | Row values that look impossible | The row, plus the container log for that position | SL/TP computed in the wrong units. Slippage. Wrong exit price |
| LONG_HOLD | A tms position was open longer than 12 h, or a judas one longer than 24 h | Session end / `EOD Force-Flatten` lines | The session flatten did not fire |
| MULTI_OPEN, DUP_CTRADER_ID, NO_CTRADER_ID, CLOSED_INCOMPLETE, VOLUME_NOT_RESTORED, EXIT_BEFORE_ENTRY | Bookkeeping integrity | `app/portfolio.py` `register_position` / `close_position` / `record_partial_close` | A replayed report. A fallback match on bot+symbol |
| TRADE_OP_FAILED | The broker rejected a modify or close (`InvalidStopLossTakeProfit`, `InvalidRequest`, …) | The requested SL/TP against the price at that moment. Is the bot retrying in a loop? | SL on the wrong side of the market after a move. A trailing retry storm |
| BOT_EXCEPTION | An exception inside `On*` handlers of a cBot | The line before it; the cBot source at the VPS HEAD | A cBot code bug. It skips that bar's logic |
| STALE_ALGO | The container started before its `.algo` was rebuilt | `/api/bots` `polling:false`; `logs/restart_when_flat.log` | The restart was skipped because the bot held a position |
| DOCKER_LOG_CORRUPT | The json log contains a NUL block | Already handled by the collector | A hard reset. `docker logs` without `--tail` goes blind after it |
| CONTAINER_DOWN, CONTAINER_OOM, KERNEL_OOM, WATCHDOG_STOPPED, BACKUP_MISSING, JOURNAL_UNAVAILABLE | Ops problems | Journal and `docker inspect` | Memory or swap pressure, a failed cron job, missing sudo |
| TRACEBACK_* | Python exception | The stack in the digest; the code at the VPS HEAD | A server bug |

## Known noise (not a bug unless the pattern breaks)

- These cBot lines are expected when their "near agentfx restart" count is close to their total
  count. That count covers ±3 min around a service restart and 10 min after a HOST BOOT:
  - `[TickStream] WebSocketException …`
  - `[Dashboard] Command poll failed … Connection refused`
  - `[AI Agent Bridge Error] Connection refused`
  If they occur **away from** restarts, that is a real outage. Report it.
- `News Filter … (429) Too Many Requests` and `XML fallback failed`: ForexFactory rate limiting,
  low severity. Only matters if the news filter stayed off during a high-impact event.
- `GET /ws/dashboard 404`: a browser upgrading over HTTP/2, which strips the Upgrade header.
  `HEAD / 405` is a monitor probe.
- `Portfolio risk check failed: Daily loss limit reached` and `[TMS CLOSE GUARD] … rejected`:
  guards doing their job. The finding is the loss that triggered them, not the warning.
- Lines in the cBot parameter table (`| requireRejectionWick | True |`).

## Ad-hoc read-only follow-ups

Pipe a script into the production venv:
`ssh forge@204.168.144.232 'cd /home/forge/AgentFxTrading && .venv/bin/python -' < q.py`.
Start the script with this header:

```python
import os, sys; sys.path.insert(0, ".")
from dotenv import dotenv_values
os.environ["DATABASE_URL"] = dotenv_values(".env").get("DATABASE_URL") or ""  # never print it
from app.db import get_db_connection, PostgresConnectionWrapper
conn = get_db_connection()
assert isinstance(conn, PostgresConnectionWrapper), "fell back to SQLite"
conn._conn.set_session(readonly=True, autocommit=True)
q = lambda sql, p=None: [dict(r) for r in conn.execute(sql, p).fetchall()]
```

Timestamps in `positions` are TEXT UTC, like `2026-09-24 10:10:54.15+00`.

Other reads:
- **Container log, full file:** `sudo -n cat $(docker inspect -f '{{.LogPath}}' <c>) | grep …`
- **Container log, recent end:** `docker logs --tail 500 <c>`
- **Agent log:** `grep … logs/agent_<DAY>.log`. Times are GMT+7.
- **Journal:** `sudo -n journalctl -u agentfx.service --since '<UTC>' --until '<UTC>' --no-pager`
- **Host boots:** `sudo -n journalctl --list-boots`
- **Service unit:** `systemctl cat` is refused. Use `sudo -n grep ExecStart /etc/systemd/system/agentfx.service`
- **git on the VPS:** `git --no-optional-locks …`. A plain `git status` may rewrite `.git/index`
- **API:** `curl -s http://127.0.0.1:8000/api/dashboard/positions` (also `/api/bots`, `/api/watchdog/status`)
- **Container to bot type:**
  - `*-all-flowrsi` → `FlowRsiBot.cs`
  - `*-judas` → `AsianRangeJudasSweepBot.cs`
  - `*-tokyo`, `*-london`, `*-newyork` → `AiAgentBot.cs` (TMS/ORB)
