"""Per-bot entry pause set from the dashboard (Bots tab), and bot identity helpers.

A paused bot takes no new entries until Resume is clicked. It keeps running and keeps managing
the positions it already has (trailing, break-even, SL/TP). The pause is stored in the
bot_controls table so it survives service and container restarts, and it is enforced twice:
PortfolioManager.check_risk refuses entries for a paused bot_id (every /trade entry), and the
bots read the flag from GET /api/cbot/commands and skip their own entry paths, which covers the
ones that never ask the server (UseAiGateMode=false, Judas UseDirectAiApi, Judas DCA adds).
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Set

PAUSED_REASON = "Paused from dashboard"


def sanitize_bot_id(bot_id: Optional[str]) -> str:
    """The bot_id a cBot sent, without the quotes or trailing flags the cTrader CLI can leave on it."""
    if not bot_id:
        return "default"
    cleaned = str(bot_id).strip().strip("\"'“”‘’`")
    if " --" in cleaned:
        cleaned = cleaned.split(" --")[0].strip()
    return cleaned.strip("\"'“”‘’`") or "default"


def bot_id_for_config(cfg: Dict) -> str:
    """The bot_id a container reports: its --BotId flag, else the container name (presets use the name)."""
    # Imported here: cbot_watchdog imports app.portfolio, which imports this module.
    from app.cbot_watchdog import parse_bot_id
    return sanitize_bot_id(parse_bot_id(cfg.get("run_command")) or cfg["name"])


def init_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_controls (
            bot_id TEXT PRIMARY KEY,
            paused INTEGER NOT NULL DEFAULT 0,
            paused_at TEXT,
            updated_at TEXT
        )
    """)


def load_paused(conn) -> Set[str]:
    return {row[0] for row in conn.execute("SELECT bot_id FROM bot_controls WHERE paused = 1").fetchall()}


def is_paused(conn, bot_id: str) -> bool:
    row = conn.execute("SELECT paused FROM bot_controls WHERE bot_id = ?", (bot_id,)).fetchone()
    return bool(row and row[0])


def save_paused(conn, bot_id: str, paused: bool) -> None:
    """Upsert the bot's pause flag (SQLite >= 3.24 and PostgreSQL). The caller commits."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO bot_controls (bot_id, paused, paused_at, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(bot_id) DO UPDATE SET
            paused = excluded.paused, paused_at = excluded.paused_at, updated_at = excluded.updated_at
    """, (bot_id, 1 if paused else 0, now if paused else None, now))
