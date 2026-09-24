---
name: daily-audit
description: Use when the user asks to audit AgentFxTrading, or to look for bugs and problems in it, for a single day. The day is yesterday or today so far ("hôm qua", "hôm nay", "từ đầu ngày đến giờ"). The audit covers open and closed trades, server and cBot logs on the VPS forge@204.168.144.232, and the code running there.
argument-hint: "[yesterday|today|YYYY-MM-DD]"
---

# Daily audit: one day of trades, logs and code

Find what went wrong on **one GMT+7 day**: trades, server and bot logs, and the code that ran.
Everything on the VPS is **read-only**. The output is a Vietnamese report plus a short chat summary.

Mode: `$ARGUMENTS`. Empty means `yesterday`. `today` means 00:00 GMT+7 until now. A `YYYY-MM-DD`
value means that GMT+7 date. Audit that window only. Earlier days are used only as a baseline,
such as the previous report's ledger figure, and never as a second audit.

## Hard rules

- **Read-only on the VPS.**
  - Postgres is queried only in a read-only session.
  - HTTP calls are GETs only.
  - docker, journalctl and git are used only to read.
  - Never restart, deploy or build anything.
  - Never run `scripts/deploy_feature.sh`.
  - Never run pytest on the VPS: it rewrites prod files.
  - Never copy files to the VPS. Scripts are piped in over stdin.
- **Report only.** Don't edit code, don't commit, don't touch the DB. If a finding needs a DB
  fix, write the SQL into the report for the user to run. Fixes start only when the user says so.
- Every claim in the report cites evidence: a DB row id, `ctrader_id`, a log line with its time,
  or `file:line`. When something is only suspected, write "nghi ngờ" and say what would confirm it.

## Workflow

**1. Collect (one SSH call, about 30 s).** Run this from the repo root:

```bash
OUT=<scratchpad>/day_<mode>.txt
ssh forge@204.168.144.232 'cd /home/forge/AgentFxTrading && .venv/bin/python - <mode>' \
  < .claude/skills/daily-audit/scripts/collect_day.py > "$OUT" 2>&1
```

Read the whole digest. If it prints `ABORT`, stop and tell the user why. Lines starting with
`FLAG <CODE>:` are automatic hits; [reference.md](reference.md) says how to verify each code.
The digest is a snapshot at its `collected` time, and other sessions may deploy or restart
while you investigate. Before writing, re-read the VPS `git log -1` and the latest reflog
entry. Report anything after the collected time as a note ("sau thời điểm thu thập"), not as
a finding.

**2. Code state (local).**
- Run `git fetch origin`.
- Compare three commits: local HEAD, the VPS `HEAD` from the digest, and `origin/main`.
- Read code **at the VPS HEAD**. If local HEAD differs from it, use `git show <vps-sha>:<path>`.
- List the commits that landed or were deployed inside the window (digest: "commit in window",
  "HEAD moved"). Review their diffs for bugs.
  - Read fully the diffs that touch `app/portfolio.py`, `app/risk_limits.py`, the `/trade` and
    `/portfolio/report` paths in `app/server.py`, `app/cbot_presets.py` and `cBot/*.cs`.
  - Skim UI-, docs- and test-only commits.
  - If an earlier report already reviewed a commit, cite that report and don't review it again.
- Run `.venv/bin/pytest -q` locally. Other sessions may have uncommitted edits, so note it if
  `git status` is dirty.

**3. Triage every FLAG.**
- Verify each one with a targeted read-only follow-up (see [reference.md](reference.md)).
- Classify it as one of:
  - bug: code
  - bug: ops/config
  - strategy/risk issue
  - noise
- Trace each bug to its root cause at `file:line`.
- Merge FLAGs that share one cause. For example, REPORT_FAILED, OPEN_NOT_IN_DB, LEDGER_DRIFT
  and "Close position ignored" can all be one lost report.

**4. Read past the flags.** Several things raise no FLAG, so look at them in the digest:
- LLM entries (BUY/SELL) compared with `Position registered`, and risk-check refusals.
- WARNING+ groups in the agent log.
- uvicorn errors and HTTP ≥400 in the journal.
- Service restarts (`Portfolio database initialized`) and whether a trade was open during one.
- Risk-limit changes.
- Container problem groups whose "near agentfx restart" count is low.
- Backup, system load and swap.

**5. Compare with earlier reports.**
- Read the report files in `docs/audits/`. Include every format, not only `*-daily-check.md`;
  for example `*-audit.md` is a manual audit. Take the last known ledger figure
  (balance − Σpnl) from them as the baseline.
- Mark each issue **mới** (new), **đã biết** (already known, with the report it came from), or
  **tái phát** (was fixed and is back).
- Don't write up a known issue again at length. Give its count for the day and a reference.

**6. Write the report.** Save it to `docs/audits/<DAY>-daily-check.md`, following
[report-template.md](report-template.md). `docs/` is gitignored, so the file stays local. Overwrite
an earlier run of the same day, and say so. Then give a chat summary of at most 10 lines: the
window, trade counts and P&L, the issues by severity, and the file link.

## Pitfalls this codebase sets

| Trap | Reality |
|---|---|
| `/api/bots` `open_positions` as broker truth | It is a count of DB rows. Broker truth is the live P&L age in `/api/dashboard/positions` (NO_TELEMETRY) and cTrader's `SUCCEEDED, Position PID…` lines in the container log. |
| `docker logs --since` | It reads forward and stops **silently** at a corrupt record (the NUL block from a hard reset). Use the collector, `docker logs --tail N`, or `sudo -n cat $(docker inspect -f '{{.LogPath}}' c)`. |
| `journalctl -u agentfx` | Needs `sudo -n`. Without it the output is empty and looks clean. |
| DB peek without `DATABASE_URL` | `get_db_connection` falls back to the local SQLite without a word. Use the header in reference.md. |
| AGENTS.md paths and container list | Both are stale. The app is at `/home/forge/AgentFxTrading`. There are 45 containers named `cbot-demo-demo-<sym>-<all-flowrsi / KZ-london-ny-judas / tokyo / london / newyork>`. |
| Time zones | Log files and the window use GMT+7. DB, docker and journal timestamps are UTC. Daily loss limits count the **UTC** day. |
| LEDGER_DRIFT in `yesterday` mode | The balance is "now", so the drift may come from today. Attribute it before you report it. |
| STALE_ALGO, bots/watchdog, SYSTEM | These describe now, not the audited day. Label them "(hiện tại)". |
| Limits changed mid-window | Judge each crossing with the value in force at that time, taken from the "Risk limits updated" log lines. |
