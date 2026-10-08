#!/usr/bin/env python3
"""
Sync manual trades from cTrader into AgentFxTrading database.
Queries cTrader history via probe algo, filters manual trades (label is empty),
and inserts any missing trades into positions table with bot_id = 'manual'.
"""
import os
import re
import sys
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_db_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SyncManualTrades")

PROBE_ALGO_PATH = Path("/root/cAlgo/Sources/Robots/probe.algo")
CTID_PWD_PATH = Path("/root/ctrader_data/ctid_pwd")
CTID_EMAIL = "senior1206@gmail.com"
ACCOUNT_NUMBER = "6094347"
ACCOUNT_ID = "live-6094347"


def ensure_probe_algo():
    if PROBE_ALGO_PATH.exists():
        return True
    logger.info("Probe algo not found, attempting to build...")
    csproj = Path("/root/orb_bt/probe/Probe/Probe.csproj")
    if not csproj.exists():
        logger.error(f"Probe csproj not found at {csproj}")
        return False
    res = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{PROJECT_ROOT}:/workspace",
        "-v", "/root:/root",
        "ghcr.io/spotware/ctrader-console:latest",
        "build", str(csproj)
    ], capture_output=True, text=True)
    return PROBE_ALGO_PATH.exists()


def run_probe_and_collect_trades():
    if not ensure_probe_algo():
        raise RuntimeError("probe.algo could not be found or built")

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_ROOT}:/workspace",
        "-v", "/root:/root",
        "ghcr.io/spotware/ctrader-console:latest",
        "run", str(PROBE_ALGO_PATH),
        f"--ctid={CTID_EMAIL}",
        f"--pwd-file={CTID_PWD_PATH}",
        f"--account={ACCOUNT_NUMBER}",
        "--symbol=USDJPY",
        "--period=m15",
        "--full-access",
        "--exit-on-stop"
    ]
    logger.info("Running cTrader probe to extract history...")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = res.stdout + res.stderr

    trades = []
    for line in output.splitlines():
        if "MANUAL_TRADE|" in line or "EXACT_TRADE|" in line:
            parts_str = line.split("|", 1)[1] if line.startswith("Info |") or line.startswith("0") else line
            idx = parts_str.find("pid=")
            if idx == -1:
                continue
            raw_kv = parts_str[idx:].split("|")
            trade_data = {}
            for kv in raw_kv:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    trade_data[k.strip()] = v.strip()
            if trade_data.get("pid"):
                trades.append(trade_data)
    logger.info(f"Collected {len(trades)} trades from cTrader History")
    return trades


def sync_trades_to_db(trades):
    if not trades:
        return 0, []

    conn = get_db_connection()
    try:
        pids = [int(t["pid"]) for t in trades if t.get("pid")]
        cur = conn.execute("SELECT ctrader_id FROM positions WHERE ctrader_id = ANY(%s)", (pids,))
        existing_pids = set(r[0] for r in cur.fetchall())

        inserted_trades = []
        for t in trades:
            pid = int(t["pid"])
            if pid in existing_pids:
                continue

            vol_lots = float(t.get("vol", 1)) / 100.0 if float(t.get("vol", 1)) >= 100 else (float(t.get("vol", 1)) * 0.01 if float(t.get("vol", 1)) == 1 else float(t.get("vol", 0.01)))
            # Standard lot conversion: 1 unit Gold = 0.01 lots
            if t.get("sym") == "XAUUSD" and float(t.get("vol", 1)) == 1:
                vol_lots = 0.01
            elif t.get("sym") == "GBPJPY" and float(t.get("vol", 5000)) == 5000:
                vol_lots = 0.05

            record = {
                "bot_id": "manual",
                "symbol": t.get("sym", "XAUUSD"),
                "side": t.get("type", "Buy"),
                "volume": vol_lots,
                "entry_price": float(t.get("entryPrice", 0)),
                "sl_pips": None,
                "tp_pips": None,
                "entry_time": t.get("entryTime") + "+00",
                "exit_time": t.get("closeTime") + "+00",
                "exit_price": float(t.get("closePrice", 0)),
                "pnl": float(t.get("net", 0)),
                "status": "closed",
                "account_id": ACCOUNT_ID,
                "created_at": t.get("entryTime") + "+00",
                "ctrader_id": pid,
                "sl_price": None,
                "tp_price": None,
                "close_reason": "Manual close on cTrader",
                "initial_volume": vol_lots,
            }

            cur = conn.execute("""
                INSERT INTO positions (
                    bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips,
                    entry_time, exit_time, exit_price, pnl, status, account_id,
                    created_at, ctrader_id, sl_price, tp_price, close_reason, initial_volume
                ) VALUES (
                    %(bot_id)s, %(symbol)s, %(side)s, %(volume)s, %(entry_price)s, %(sl_pips)s, %(tp_pips)s,
                    %(entry_time)s, %(exit_time)s, %(exit_price)s, %(pnl)s, %(status)s, %(account_id)s,
                    %(created_at)s, %(ctrader_id)s, %(sl_price)s, %(tp_price)s, %(close_reason)s, %(initial_volume)s
                ) RETURNING id
            """, record)
            row_id = cur.fetchone()[0]
            existing_pids.add(pid)
            inserted_trades.append({**record, "id": row_id})
            logger.info(f"Inserted manual trade row #{row_id} | PID {pid} | {record['symbol']} {record['side']} PnL: ${record['pnl']}")

        conn.commit()
        return len(inserted_trades), inserted_trades
    finally:
        conn.close()


def main():
    logger.info("Starting manual trades synchronization...")
    trades = run_probe_and_collect_trades()
    count, inserted = sync_trades_to_db(trades)
    logger.info(f"Sync complete. New trades inserted: {count}")
    return count


if __name__ == "__main__":
    main()
