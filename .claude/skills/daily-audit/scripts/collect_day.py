"""Collect one GMT+7 day of AgentFxTrading evidence on the VPS. Read-only.

Run from the local repo (the script is piped in, nothing is copied to the server):

    ssh forge@204.168.144.232 'cd /home/forge/AgentFxTrading && .venv/bin/python - yesterday' \
        < .claude/skills/daily-audit/scripts/collect_day.py > "$OUT"

Argument: yesterday (default) | today | YYYY-MM-DD, a date in GMT+7. The window is that
date's 00:00-24:00 GMT+7 (17:00 UTC the day before to 17:00 UTC), cut at "now" for today.
Lines starting with "FLAG <CODE>:" are automatic anomaly hits for the auditor to verify;
everything else is context. Postgres runs in a read-only session, HTTP calls are GETs, and
docker / journalctl / git are only read.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

VN = timezone(timedelta(hours=7))
UTC = timezone.utc
API = "http://127.0.0.1:8000"
CAP = 25  # max full lines printed per list


# ---------- helpers ----------

def window(arg):
    now = datetime.now(UTC)
    today = now.astimezone(VN).date()
    day = {"today": today, "yesterday": today - timedelta(days=1)}.get(arg)
    day = day or date.fromisoformat(arg)
    start = datetime.combine(day, time(0), VN).astimezone(UTC)
    if start >= now:
        sys.exit(f"ABORT: {day} (GMT+7) has not started yet")
    return day, start, min(start + timedelta(days=1), now), now


def sh(cmd, timeout=180):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def api(path):
    try:
        with urllib.request.urlopen(API + path, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"API {path} failed: {e!r}")
        return None


def ts(s):
    """DB TEXT timestamp ('2026-09-24 10:10:54.15+00' or naive SQLite UTC) -> aware UTC."""
    if not s:
        return None
    s = str(s).strip().replace("T", " ")
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    d = datetime.fromisoformat(s)
    return d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC)


def docker_ts(s):
    """Docker's '2026-09-24T06:20:14.924678791Z' (or the zero time of a never-started one)."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,6})?", s or "")
    if not m or m.group(1).startswith("0001"):
        return None
    return datetime.fromisoformat(m.group(1) + (m.group(2) or "")).replace(tzinfo=UTC)


def vn(d):
    return d.astimezone(VN).strftime("%d/%m %H:%M:%S") if d else "-"


def short(bot):
    return re.sub(r"^cbot-(demo|live)-[^-]+-", "", bot or "")


def norm(text):
    return re.sub(r"\d+(\.\d+)?", "N", text)


def section(title):
    print(f"\n===== {title} =====")


def flag(code, msg):
    print(f"FLAG {code}: {msg}")


# ---------- database ----------

def db_conn():
    sys.path.insert(0, ".")
    from dotenv import dotenv_values
    os.environ["DATABASE_URL"] = dotenv_values(".env").get("DATABASE_URL") or ""
    from app.db import get_db_connection, PostgresConnectionWrapper
    conn = get_db_connection()
    if not isinstance(conn, PostgresConnectionWrapper):
        sys.exit("ABORT: connected to SQLite, not the production Postgres (DATABASE_URL in .env?)")
    conn._conn.set_session(readonly=True, autocommit=True)
    return conn


def rows(conn, sql, params=None):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def trades(conn, start, end, now):
    from app.risk_limits import strategy_of
    cut = (start - timedelta(days=2)).strftime("%Y-%m-%d")
    all_rows = rows(conn, "SELECT * FROM positions WHERE status = 'open' OR entry_time >= ? "
                          "OR exit_time >= ? ORDER BY entry_time", (cut, cut))
    for r in all_rows:
        r["_in"], r["_out"] = ts(r["entry_time"]), ts(r["exit_time"])
        r["_side"] = (r["side"] or "").upper()
        r["_strat"] = strategy_of(r["bot_id"]) or "?"
    inside = lambda d: d is not None and start <= d < end
    opened = [r for r in all_rows if inside(r["_in"])]
    closed = [r for r in all_rows if r["status"] == "closed" and inside(r["_out"])]
    open_end = [r for r in all_rows if r["_in"] and r["_in"] < end and (r["_out"] is None or r["_out"] >= end)]
    open_now = [r for r in all_rows if r["status"] == "open"]

    section("TRADES CLOSED IN WINDOW")
    print("id | bot | sym side vol | entry -> exit | sl / tp | in -> out (VN) | dur | pnl | reason | ctrader_id")
    for r in closed:
        dur = (r["_out"] - r["_in"]) if r["_in"] and r["_out"] else None
        print(f"{r['id']} | {short(r['bot_id'])} | {r['symbol']} {r['_side']} {r['volume']} | "
              f"{r['entry_price']} -> {r['exit_price']} | {r['sl_price']} / {r['tp_price']} | "
              f"{vn(r['_in'])} -> {vn(r['_out'])} | {str(dur).split('.')[0]} | {r['pnl']} | "
              f"{(r['close_reason'] or '')[:45]} | {r['ctrader_id']}")
    by = defaultdict(lambda: [0, 0, 0.0])
    for r in closed:
        b = by[r["_strat"]]
        b[0] += 1
        b[1] += (r["pnl"] or 0) > 0
        b[2] += r["pnl"] or 0
    for s, (n, w, p) in sorted(by.items()):
        print(f"strategy {s}: {n} closed, {w} won ({100 * w // max(n, 1)}%), pnl {p:.2f}")
    print(f"total closed pnl: {sum(r['pnl'] or 0 for r in closed):.2f}")
    for reason, n in Counter((r["close_reason"] or "?")[:45] for r in closed).most_common():
        print(f"reason x{n}: {reason}")

    section("TRADES OPENED IN WINDOW")
    for s in sorted({r["_strat"] for r in opened}):
        sides = Counter(r["_side"] for r in opened if r["_strat"] == s)
        n = sum(sides.values())
        print(f"strategy {s}: {n} opened {dict(sides)}")
        if n >= 6 and max(sides.values()) / n >= 0.8:
            flag("DIRECTION_SKEW", f"{s} opened {dict(sides)}: check against the day's market direction")
    buckets = defaultdict(list)
    for r in opened:
        b = r["_in"].replace(minute=r["_in"].minute // 15 * 15, second=0, microsecond=0)
        buckets[b].append(f"{r['symbol']} {r['_side']}")
    for b, items in sorted(buckets.items()):
        if len(items) >= 4:
            flag("SAME_BAR_CLUSTER", f"{len(items)} entries in the M15 bar {vn(b)}: {', '.join(items)}")
    for bot, n in Counter(r["bot_id"] for r in opened).items():
        if n >= 5:
            flag("CHURN", f"{short(bot)} opened {n} trades in the window")

    section("TRADE INTEGRITY")
    seen = {r["id"]: r for r in opened + closed}.values()
    ids = [r["ctrader_id"] for r in seen if r["ctrader_id"] is not None]
    if ids:
        for d in rows(conn, "SELECT ctrader_id, COUNT(*) AS n FROM positions WHERE ctrader_id IN (%s) "
                            "GROUP BY ctrader_id HAVING COUNT(*) > 1" % ",".join("?" * len(ids)), ids):
            flag("DUP_CTRADER_ID", f"ctrader_id {d['ctrader_id']} on {d['n']} rows")
    for r in seen:
        tag = f"id {r['id']} {short(r['bot_id'])} {r['symbol']} {r['_side']}"
        if r["ctrader_id"] is None:
            flag("NO_CTRADER_ID", f"{tag}: close matching fell back to bot+symbol")
        if r["status"] not in ("open", "closed"):
            flag("BAD_STATUS", f"{tag}: status {r['status']!r}")
        if r["tp_price"] and r["entry_price"] and (
                (r["_side"] == "BUY" and r["tp_price"] <= r["entry_price"])
                or (r["_side"] == "SELL" and r["tp_price"] >= r["entry_price"])):
            flag("TP_WRONG_SIDE", f"{tag}: entry {r['entry_price']} tp {r['tp_price']}")
        if r["status"] != "closed":
            continue
        if r["exit_price"] is None or r["pnl"] is None or not r["close_reason"]:
            flag("CLOSED_INCOMPLETE", f"{tag}: exit {r['exit_price']} pnl {r['pnl']} reason {r['close_reason']!r}")
            continue
        if r["_in"] and r["_out"] and r["_out"] < r["_in"]:
            flag("EXIT_BEFORE_ENTRY", f"{tag}: {vn(r['_in'])} -> {vn(r['_out'])}")
        if r["_in"] and r["_out"] and (r["_out"] - r["_in"]).total_seconds() < 60:
            flag("INSTANT_CLOSE", f"{tag}: held {(r['_out'] - r['_in']).total_seconds():.0f}s, {r['close_reason'][:40]}")
        if r["initial_volume"] and abs(r["volume"] - r["initial_volume"]) > 1e-9:
            flag("VOLUME_NOT_RESTORED", f"{tag}: volume {r['volume']} initial {r['initial_volume']}")
        move = (r["exit_price"] - r["entry_price"]) * (1 if r["_side"] == "BUY" else -1)
        if abs(r["pnl"]) >= 1 and abs(move) / r["entry_price"] > 0.0005 and (move > 0) != (r["pnl"] > 0):
            flag("PNL_SIGN", f"{tag}: price move {move:+.5g} in the trade's direction but pnl {r['pnl']}")
        for kind, level in (("stop ?loss", r["sl_price"]), ("take ?profit", r["tp_price"])):
            if level and re.search(kind, r["close_reason"], re.I):
                slip, scale = abs(r["exit_price"] - level), abs(r["entry_price"] - level)
                far = slip / r["entry_price"] > 0.001 if scale < 1e-9 else (
                    slip / scale > 0.3 and slip / r["entry_price"] > 0.0002)
                if far:
                    flag("FILL_FAR_FROM_LEVEL", f"{tag}: {r['close_reason'][:30]} level {level} filled {r['exit_price']}")

    section("OPEN AT WINDOW END")
    live = {p.get("id"): p for p in (api("/api/dashboard/positions") or []) if isinstance(p, dict)}
    configs = {c["name"]: c.get("run_command") or "" for c in rows(conn, "SELECT name, run_command FROM cbot_configs")}
    balances = {a["account_id"]: a["last_balance"] for a in rows(conn, "SELECT * FROM accounts")}
    for bot, n in Counter(r["bot_id"] for r in open_end).items():
        if n > 1:
            flag("MULTI_OPEN", f"{short(bot)} held {n} open rows at window end")
    for r in open_end:
        age_h = (end - r["_in"]).total_seconds() / 3600
        p = live.get(r["id"], {})
        print(f"{r['id']} | {short(r['bot_id'])} | {r['symbol']} {r['_side']} {r['volume']} @ {r['entry_price']} | "
              f"sl {r['sl_price']} tp {r['tp_price']} | opened {vn(r['_in'])} ({age_h:.1f}h) | "
              f"status now {r['status']} | live pnl {p.get('unrealized_pnl')} sl_pnl {p.get('sl_pnl')} "
              f"age {p.get('pnl_age_seconds')}s")
        tag = f"id {r['id']} {short(r['bot_id'])} {r['symbol']}"
        if r["sl_price"] is None:
            flag("NO_SL", f"{tag}: open without a stop-loss price")
        limit_h = {"tms": 12, "judas": 24}.get(r["_strat"])
        if limit_h and age_h > limit_h:
            flag("LONG_HOLD", f"{tag}: {r['_strat']} position open {age_h:.1f}h; check its session end / EOD flatten")
        if r["status"] == "open":
            age = p.get("pnl_age_seconds")
            if age is None or age > 600:
                flag("NO_TELEMETRY", f"{tag}: DB says open but the bot sends no P&L for it (age {age}); "
                                     f"closed at the broker with a lost close report, or the bot is down")
            sl_pnl, bal = p.get("sl_pnl"), balances.get(r["account_id"])
            if sl_pnl is not None and sl_pnl < 0 and bal:
                risk = -sl_pnl / bal * 100
                m = re.search(r"--(?:RiskPerTradePercent|RiskPercentage|RiskPercent)=([\d.]+)", configs.get(r["bot_id"], ""))
                allowed = float(m.group(1)) if m else None
                if risk > (allowed * 1.5 if allowed else 1.0):
                    flag("RISK_OVER", f"{tag}: loss at SL {sl_pnl} = {risk:.2f}% of balance {bal} "
                                      f"(configured {allowed if allowed else 'n/a (default)'}%)")
    print(f"open now (DB): {len(open_now)}")

    section("ACCOUNTS AND LEDGER (balance now vs every recorded pnl, so it spans all days up to now)")
    drifts = {}
    for a in rows(conn, "SELECT * FROM accounts WHERE last_seen IS NOT NULL"):
        total = rows(conn, "SELECT COALESCE(SUM(pnl), 0) AS s FROM positions WHERE account_id = ?", (a["account_id"],))[0]["s"]
        if a["last_balance"] is None:
            continue
        implied = a["last_balance"] - total
        print(f"{a['account_id']}: balance {a['last_balance']} equity {a['last_equity']} last_seen {vn(ts(a['last_seen']))} "
              f"| sum of recorded pnl {total:.2f} | implied deposit {implied:.2f}")
        drifts[a["account_id"]] = round(implied - round(implied, -2), 2)
        if abs(implied - round(implied, -2)) > 1.0:
            flag("LEDGER_DRIFT", f"{a['account_id']}: balance - recorded pnl = {implied:.2f}, not a round deposit; "
                                 f"a close/partial report is missing or a pnl is wrong (compare with the last report)")

    section("DAILY LOSS LIMITS (UTC days, closed P&L only)")
    limits = {(l["scope"], l["target"]): l for l in rows(conn, "SELECT * FROM risk_limits")}
    for l in limits.values():
        changed = ts(l["updated_at"])
        print(f"limit {l['scope']}/{l['target']}: {l['max_daily_loss']} enabled={l['enabled']} updated {vn(changed)}"
              + ("  <- changed inside the window" if changed and start <= changed < end else ""))
    changed = [ts(l["updated_at"]) for l in limits.values() if l["updated_at"]]
    if changed and max(changed) > start:
        print(f"NOTE: limits were changed at {vn(max(changed))} VN, after the window started; the crossings "
              f"below use today's values. The values in force earlier are in the 'Risk limits updated' log lines.")
    for utc_day in sorted({start.date(), (end - timedelta(microseconds=1)).date()}):
        day_closed = sorted((r for r in all_rows if r["status"] == "closed" and r["_out"] and r["_out"].date() == utc_day),
                            key=lambda r: r["_out"])
        scopes = defaultdict(list)
        for r in day_closed:
            scopes[("account", "*", r["account_id"])].append(r)
            scopes[("strategy", r["_strat"], r["account_id"])].append(r)
            scopes[("container", "*", r["bot_id"])].append(r)
        for (scope, target, who), rs in sorted(scopes.items()):
            lim = limits.get((scope, target))
            total, crossed = 0.0, None
            for r in rs:
                total += r["pnl"] or 0
                if lim and lim["enabled"] and crossed is None and total <= -lim["max_daily_loss"]:
                    crossed = r["_out"]
            if scope != "container" or crossed:
                print(f"{utc_day} {scope}/{target} {short(who)}: closed pnl {total:.2f}"
                      + (f" / limit -{lim['max_daily_loss']}" if lim else "")
                      + (f", crossed at {vn(crossed)} VN" if crossed else ""))
            if crossed:
                later = [r for r in all_rows if r["_in"] and crossed < r["_in"] and r["_in"].date() == utc_day
                         and (r["account_id"] if scope != "container" else r["bot_id"]) == who
                         and (scope != "strategy" or r["_strat"] == target)]
                for r in later:
                    flag("LIMIT_BYPASS", f"{scope}/{target} limit crossed {vn(crossed)} but id {r['id']} "
                                         f"{short(r['bot_id'])} {r['symbol']} opened {vn(r['_in'])}")

    section("BOT CONTROLS (paused)")
    for r in rows(conn, "SELECT * FROM bot_controls"):
        print(r)
    return all_rows, drifts


# ---------- server logs ----------

def agent_log(day):
    section(f"AGENT LOG logs/agent_{day}.log (GMT+7)")
    path = f"logs/agent_{day}.log"
    if not os.path.exists(path):
        print(f"MISSING {path} (the handler keeps only 14 files)")
        return
    line_re = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(\w+)\s*\] ([\w.]+): (.*)$")
    levels, warn, events, llm, tb = Counter(), {}, Counter(), Counter(), []
    must = defaultdict(list)
    must_re = re.compile(r"Close position ignored|Partial close ignored|Failed to|falling back to SQLite|"
                         r"\[MANUAL\]|\[CBOT WATCHDOG\]|Portfolio database initialized|Risk limits updated")
    event_re = re.compile(r"Position registered|Position closed|Partial close recorded|Portfolio risk check failed|"
                          r"\[LLM DECISION\]|\[CBOT EVENT\]|\[PORTFOLIO EVENT\]")
    from app.risk_limits import strategy_of
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    for i, line in enumerate(lines):
        m = line_re.match(line)
        if not m:
            if line.startswith("Traceback") and len(tb) < 5:
                tb.append("\n".join(lines[i:i + 12]))
            continue
        t, level, name, msg = m.groups()
        levels[level] += 1
        if level in ("WARNING", "ERROR", "CRITICAL"):
            key = norm(f"{level} {name}: " + re.sub(r"demo-[0-9]+/cbot-[\w-]+", "BOT", msg))[:170]
            w = warn.setdefault(key, [0, t, t])
            w[0] += 1
            w[2] = t
        mm = must_re.search(msg)
        if mm and len(must[mm.group(0)]) < CAP:
            must[mm.group(0)].append(f"{t} {msg[:260]}")
        me = event_re.search(msg)
        if me:
            events[me.group(0)] += 1
        ml = re.search(r"\[LLM DECISION\] \S+?/(\S+) -> Action: (\w+).*?Conf: ([\d.]+)", msg)
        if ml:
            conf = float(ml.group(3))
            llm[(strategy_of(ml.group(1)), ml.group(2), "conf>=75" if conf >= 75 else "conf<75")] += 1
    print("levels:", dict(levels))
    print("events:", dict(events))
    print("LLM decisions (strategy, action, confidence):")
    for k, n in sorted(llm.items()):
        print(f"  {k}: {n}")
    print("WARNING+ grouped (count | first | last | pattern):")
    for key, (n, first, last) in sorted(warn.items(), key=lambda kv: -kv[1][0])[:40]:
        print(f"  {n} | {first[11:]} | {last[11:]} | {key}")
    for k, items in must.items():
        print(f"-- {k} ({len(items)} shown):")
        for s in items:
            print("  " + s)
    for t in tb:
        flag("TRACEBACK_AGENT_LOG", "\n" + t)


def journal(start, end):
    section("agentfx.service JOURNAL (window, UTC)")
    rc, out = sh(f"sudo -n journalctl -u agentfx.service --since @{int(start.timestamp())} "
                 f"--until @{int(end.timestamp())} --no-pager -o short-iso", timeout=300)
    if rc != 0 or out.startswith("sudo:"):
        flag("JOURNAL_UNAVAILABLE", out[:200])
        return []
    restarts, http, errs, tb = [], Counter(), Counter(), []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        stamp = line[:25]
        if "systemd[" in line and re.search(r"Started|Stopping|Stopped|Failed|Main process exited|Killing|code=killed", line):
            print("  " + line[:200])
            try:
                restarts.append((datetime.fromisoformat(stamp).astimezone(UTC), 180))
            except ValueError:
                pass
        m = re.search(r'"(\w+) ([^ ?"]+)[^"]*" (\d{3})', line)
        if m and int(m.group(3)) >= 400:
            http[f"{m.group(1)} {m.group(2)} {m.group(3)}"] += 1
        if re.search(r"uvicorn\[\d+\]: (ERROR|WARNING|CRITICAL):", line):
            errs[norm(line.split(": ", 1)[-1])[:170]] += 1
        if "Traceback" in line and len(tb) < 5:
            tb.append("\n".join(l[l.find(": ") + 2:] for l in lines[i:i + 14]))
    print(f"lines: {len(lines)}, service start/stop events: {len(restarts)}")
    rc, out = sh("sudo -n journalctl --list-boots --no-pager")
    for line in out.splitlines():
        m = re.search(r"\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+)\s+\w{3} \d{4}", line)
        if m:
            boot = datetime.fromisoformat(m.group(1) + ("+00:00" if m.group(2) == "UTC" else "")).astimezone(UTC)
            if start <= boot < end:
                print(f"  HOST BOOT {vn(boot)} VN: expect ~10 min of timeouts/reconnects after it")
                restarts.append((boot, 600))
    for k, n in http.most_common(20):
        print(f"  HTTP>=400 x{n}: {k}")
    for k, n in errs.most_common(20):
        print(f"  uvicorn x{n}: {k}")
    for t in tb:
        flag("TRACEBACK_JOURNAL", "\n" + t)
    rc, out = sh(f"sudo -n journalctl -k --since @{int(start.timestamp())} --until @{int(end.timestamp())} "
                 f"--no-pager | grep -iE 'out of memory|oom-kill|killed process' | head -20")
    for line in out.splitlines():
        flag("KERNEL_OOM", line[:200])
    return restarts


# ---------- containers ----------

def container_lines(log_path, name, start, end):
    """One container's log lines inside the window, read from its json-file log.

    `docker logs` (without --tail) reads the file forward and stops, silently, at the first
    corrupt record, e.g. the NUL block a hard reset leaves behind; everything after it is
    invisible. Reading the file directly and skipping bad records avoids that.
    """
    since, until = f"{start:%Y-%m-%dT%H:%M:%S}", f"{end:%Y-%m-%dT%H:%M:%S}"
    rc, out = sh(f"sudo -n cat {log_path}", timeout=120)
    if rc != 0 or not log_path:
        flag("CONTAINER_LOG_UNREADABLE", f"{name}: {out[:120]!r}; fell back to docker logs (may be truncated)")
        return sh(f"docker logs --since {since}Z --until {until}Z {name} 2>&1")[1].splitlines(), 0
    lines, bad = [], 0
    for raw in out.split("\n"):
        bad += raw.startswith("\x00")  # the reset's NUL block, often glued to a valid record
        if not raw.strip("\x00 \r"):
            continue
        try:
            rec = json.loads(raw.lstrip("\x00"))
        except ValueError:
            bad += not raw.startswith("\x00")
            continue
        if since <= rec.get("time", "")[:19] < until:
            lines.append(rec.get("log", "").rstrip("\n"))
    return lines, bad

def containers(start, end, all_rows, restarts, drifts):
    section("CONTAINERS")
    rc, out = sh("docker inspect -f '{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|"
                 "{{.State.OOMKilled}}|{{.Created}}|{{.LogPath}}|{{json .Args}}' $(docker ps -aq)")
    info = {}
    for line in out.splitlines():
        parts = line.split("|", 7)
        if len(parts) < 8:
            continue
        name, status, started, rcount, oom, created, log_path, args = parts
        name = name.lstrip("/")
        algo = next((a.replace("/workspace/", "") for a in json.loads(args) if a.endswith(".algo")), None)
        info[name] = dict(status=status, started=docker_ts(started), created=docker_ts(created),
                          restarts=int(rcount), oom=oom == "true", algo=algo, log_path=log_path)
    print(f"{len(info)} containers; status {dict(Counter(i['status'] for i in info.values()))}")
    recreated = sorted((i["created"], name) for name, i in info.items() if i["created"] and i["created"] > start)
    if recreated:
        print(f"{len(recreated)} containers (re)created inside the window, {vn(recreated[0][0])} -> "
              f"{vn(recreated[-1][0])} VN; their logs before that are gone: "
              + (", ".join(short(n) for _, n in recreated) if len(recreated) <= 6 else "(many)"))
    for name, i in sorted(info.items()):
        if i["status"] != "running":
            flag("CONTAINER_DOWN", f"{name} is {i['status']}")
        if i["oom"]:
            flag("CONTAINER_OOM", name)
        if i["algo"] and os.path.exists(i["algo"]):
            built = datetime.fromtimestamp(os.path.getmtime(i["algo"]), UTC)
            if i["started"] and i["started"] < built:
                flag("STALE_ALGO", f"(current state) {name} started {vn(i['started'])} VN, before {i['algo']} "
                                   f"was rebuilt {vn(built)} VN: still runs the previous build")
    rc, out = sh(f"docker events --since {int(start.timestamp())} --until {int(end.timestamp())} "
                 "--filter type=container --format '{{.Action}} {{.Actor.Attributes.name}}'", timeout=60)
    ev = Counter(l.split(" ")[0] for l in out.splitlines() if l.strip())
    print(f"docker events in window (dockerd keeps a limited buffer): {dict(ev)}")

    line_re = re.compile(r"^(\d\d/\d\d/\d{4} \d\d:\d\d:\d\d)\.\d+ \| (\w+) \| (.*)$")
    problem_re = re.compile(r"error|exception|fail|refused|timeout|timed out|invalid|not found|denied|crash|"
                            r"not enough|insufficient", re.I)
    decision_re = re.compile(r"^(Agent: |\[AI Decision\]|\[AI Agent HOLD\]|\[Judas Sweep Filter\]|\[Order Executed\]|"
                             r"\[Portfolio( Hub)?\] Reported|\[Partial Close\])")
    groups, bot_flags, missing_pnl = {}, {}, []
    db_by_bot = defaultdict(list)
    for r in all_rows:
        db_by_bot[r["bot_id"]].append(r)
    inside = lambda d: d is not None and start <= d < end
    corrupt = []
    for name in sorted(info):
        lines, bad = container_lines(info[name]["log_path"], name, start, end)
        if bad:
            corrupt.append(f"{short(name)} ({bad})")
        ev = defaultdict(list)
        opened_at, close_pnl, generic_closes = {}, {}, []
        for line in lines:
            m = line_re.match(line)
            if not m:
                continue
            t = datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=UTC)
            msg = m.group(3)
            # cTrader's own Trade log names the position id for every bot type; SL/TP hits are
            # broker-side and leave no line, so closes are only visible via the bots' reports.
            mo = re.search(r"to (?:Buy|Sell) [\d.]+ (\w+) .*SUCCEEDED, Position PID(\d+)", msg)
            if mo:
                opened_at[int(mo.group(2))] = (t, mo.group(1))
            mc = re.search(r"Reported position closed: #(\d+) \w+ PnL: (-?[\d.]+)", msg)
            if mc:
                close_pnl[int(mc.group(1))] = float(mc.group(2))
            mg = re.search(r"\[Portfolio\] Reported position closed: \w+ (\w+), PnL: (-?[\d.]+)", msg)
            if mg:
                generic_closes.append((t, mg.group(1), float(mg.group(2))))
            for key, rx in (("open", r"SUCCEEDED, Position PID(\d+)"),
                            ("open", r"\[Order Executed\] (?:Buy|Sell) [\d.]+ lots #(\d+)"),
                            ("open", r"Reported position open: #(\d+)"),
                            ("closing", r"Closing position PID(\d+) .*SUCCEEDED"),
                            ("rep_close", r"Reported position closed: #(\d+)")):
                mm = re.search(rx, msg)
                if mm:
                    ev[key].append(int(mm.group(1)))
            for code, rx in (("REPORT_FAILED", r"Failed to report"), ("TRADE_OP_FAILED", r"FAILED with error"),
                             ("BOT_EXCEPTION", r"\[Error in On\w+\]|Unhandled exception|crashed")):
                if re.search(rx, msg):
                    f = bot_flags.setdefault((code, name, norm(msg)[:120]), {"n": 0, "first": t, "last": t, "msg": msg})
                    f["n"] += 1
                    f["first"], f["last"] = min(f["first"], t), max(f["last"], t)
                    break
            if problem_re.search(msg) and not decision_re.search(msg):
                key = norm(msg)[:150]
                g = groups.setdefault(key, {"n": 0, "bots": set(), "first": t, "last": t, "near_restart": 0})
                g["n"] += 1
                g["bots"].add(short(name))
                g["first"], g["last"] = min(g["first"], t), max(g["last"], t)
                g["near_restart"] += any(abs((t - r).total_seconds()) <= radius for r, radius in restarts)
        # reconcile the container's own trade log with its DB rows (by ctrader_id)
        db = db_by_bot.get(name, [])
        by_id = {r["ctrader_id"]: r for r in db if r["ctrader_id"] is not None}
        for pid in sorted(set(ev["open"] + ev["closing"] + ev["rep_close"])):
            if pid not in by_id:
                pnl, how = close_pnl.get(pid), "close report"
                if pnl is None and pid in opened_at:
                    t0, sym = opened_at[pid]
                    nxt = next((c for c in generic_closes if c[0] >= t0 and c[1] == sym), None)
                    pnl, how = (nxt[2], "first close report for the symbol after the open") if nxt else (None, "")
                if pnl is not None:
                    missing_pnl.append(pnl)
                flag("OPEN_NOT_IN_DB", f"{name}: position #{pid} traded in the window (container log) but has no "
                                       f"DB row: its open report was lost, so its P&L is missing from the ledger; "
                                       + (f"close PnL {pnl} ({how})" if pnl is not None else "no close seen in the window"))
        for pid in sorted(set(ev["rep_close"])):
            row = by_id.get(pid)
            if row and row["status"] != "closed":
                flag("CLOSE_NOT_APPLIED", f"{name}: close of #{pid} reported but DB row {row['id']} is still open")
        created = info[name]["created"]
        for r in db:
            if (inside(r["_in"]) and r["ctrader_id"] and r["ctrader_id"] not in ev["open"]
                    and created and created < r["_in"] - timedelta(minutes=1)):
                flag("DB_OPEN_NOT_IN_LOGS", f"{name}: DB row {r['id']} (#{r['ctrader_id']}) opened {vn(r['_in'])} VN "
                                            f"but the container log has no open for it")
    if missing_pnl:
        total = round(sum(missing_pnl), 2)
        print(f"P&L of trades missing from the DB: {total} over {len(missing_pnl)} trades; ledger drift now "
              f"{drifts} (a match means the lost reports explain the drift)")
    if corrupt:
        flag("DOCKER_LOG_CORRUPT", f"{len(corrupt)} containers have unreadable records in their json log (NUL block "
                                   f"from a hard reset): plain `docker logs`/--since stops there silently; this "
                                   f"script skips them. {', '.join(corrupt[:6])}{'...' if len(corrupt) > 6 else ''}")
    for (code, name, _), f in sorted(bot_flags.items(), key=lambda kv: kv[1]["first"]):
        when = vn(f["first"]) + (f" -> {vn(f['last'])} (x{f['n']})" if f["n"] > 1 else "")
        flag(code, f"{name} {when} VN: {f['msg'][:200]}")
    print("container problem lines grouped (count | bots | first-last VN | near agentfx restart | pattern):")
    for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"])[:40]:
        bots = sorted(g["bots"])
        print(f"  {g['n']} | {len(bots)} bots ({', '.join(bots[:3])}{'...' if len(bots) > 3 else ''}) | "
              f"{vn(g['first'])[6:]}-{vn(g['last'])[6:]} | {g['near_restart']} | {key}")


# ---------- deploy, health, system ----------

def deploy_state(start, end):
    section("DEPLOY STATE (VPS checkout)")
    git = "git --no-optional-locks"
    for cmd in (f"{git} log -1 --format='HEAD %h %cI %s'",
                f"GIT_TERMINAL_PROMPT=0 timeout 20 {git} ls-remote origin refs/heads/main | cut -c1-12 | sed 's/^/origin\\/main /'",
                f"{git} status --porcelain --untracked-files=no",
                f"{git} log --since={start:%Y-%m-%dT%H:%M:%SZ} --until={end:%Y-%m-%dT%H:%M:%SZ} --format='commit in window: %h %cI %an %s'",
                "ls -l --time-style=+%FT%T cBot/*.algo | awk '{print $6, $7}'",
                "ls -d backups/algo_* 2>/dev/null | tail -5"):
        print(sh(cmd)[1].rstrip())
    for line in sh(f"{git} reflog --date=iso-strict --format='%gd|%h|%gs' | head -200")[1].splitlines():
        m = re.match(r"HEAD@\{([^}]+)\}\|(.*)", line)
        when = m and docker_ts(m.group(1)[:19])
        if when and start <= when < end:
            print(f"HEAD moved {vn(when)} VN: {m.group(2)[:150]}")
    section("BACKUP")
    rc, out = sh("cat backups/backup.log 2>/dev/null")
    hits = [l for l in out.splitlines() if l[1:20] >= f"{start:%Y-%m-%d %H:%M:%S}" and l[1:20] < f"{end:%Y-%m-%d %H:%M:%S}"]
    print("\n".join(hits) or "no backup.log lines in the window")
    due = datetime.combine(end.date(), time(3), UTC)
    due = due if due < end else due - timedelta(days=1)
    if due >= start and not any("SUCCESS" in l for l in hits):
        flag("BACKUP_MISSING", f"no successful backup around {vn(due)} VN (cron 03:00 UTC)")


def health():
    section("BOTS / WATCHDOG (current state)")
    bots = (api("/api/bots") or {}).get("bots", [])
    for b in bots:
        bad = [k for k, v in (("not running", b.get("status") != "running"), ("unhealthy", not b.get("healthy")),
                              ("stuck", b.get("stuck")), ("not polling", not b.get("polling")),
                              ("paused", b.get("paused"))) if v]
        if bad:
            print(f"  {b['name']}: {', '.join(bad)} | {b.get('health_reason')} | open {b.get('open_positions')}")
    print(f"{len(bots)} bots listed")
    w = api("/api/watchdog/status") or {}
    stale = {k: v for k, v in (w.get("feed_telemetry") or {}).items() if v and v > w.get("stale_feed_seconds", 2400)}
    print(f"watchdog running={w.get('is_running')} stale feeds={stale}")
    if w and not w.get("is_running"):
        flag("WATCHDOG_STOPPED", "the cBot watchdog loop is not running")


def system(mode_today):
    section("SYSTEM (snapshot now" + ("" if mode_today else "; not the audited day") + ")")
    print(sh("nproc; uptime; free -m; cat /proc/pressure/cpu /proc/pressure/memory; df -h / | tail -1")[1].rstrip())


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "yesterday"
    day, start, end, now = window(arg)
    print(f"WINDOW {day} GMT+7 = {start:%Y-%m-%d %H:%M} UTC -> {end:%Y-%m-%d %H:%M} UTC"
          f"{' (cut at now)' if end == now else ''}; collected {now:%Y-%m-%d %H:%M} UTC")
    conn = db_conn()
    try:
        all_rows, drifts = trades(conn, start, end, now)
    finally:
        conn.close()
    agent_log(day)
    restarts = journal(start, end)
    containers(start, end, all_rows, restarts, drifts)
    deploy_state(start, end)
    health()
    system(arg == "today" or day == now.astimezone(VN).date())
    print("\nEND")


main()
