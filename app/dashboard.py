"""
Dashboard for monitoring AgentFxTrading system.
Provides real-time visualization of positions, P&L, and bot status.
"""

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import sqlite3
from app.db import get_db_connection
import json
import asyncio
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional, Any
from app.accounts import get_account_registry
from app.leaderboard import compute_bot_leaderboard, compute_profit_factor
import logging
from app import news_service


VN_TZ = timezone(timedelta(hours=7))  # Vietnam has no DST, so a fixed offset is exact


def format_vn_time(ts: Optional[str]) -> Optional[str]:
    """Render a UTC timestamp from the positions table as 'HH:MM:SS DD/MM/YYYY' in Vietnam time.

    The DB writes datetime('now') ('YYYY-MM-DD HH:MM:SS', UTC, no tz marker); anything that does
    not parse is returned untouched so a stray value never blanks a table cell.
    """
    if not ts:
        return ts
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ).strftime("%H:%M:%S %d/%m/%Y")


def format_duration(entry_ts: Optional[str], exit_ts: Optional[str]) -> Optional[str]:
    """How long a trade stayed open, as its two largest units ('4m 12s', '2h 28m', '2d 4h').

    Takes the raw UTC timestamps from the positions table; returns None when either side is
    missing, unparseable, or the exit precedes the entry.
    """
    if not entry_ts or not exit_ts:
        return None
    try:
        secs = int((datetime.fromisoformat(str(exit_ts)) - datetime.fromisoformat(str(entry_ts))).total_seconds())
    except (ValueError, TypeError):
        return None
    if secs < 0:
        return None
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _age_seconds(ts: Optional[float]) -> Optional[float]:
    """Seconds elapsed since `ts` (epoch), for surfacing how stale a bot figure is."""
    if ts is None:
        return None
    try:
        return round(datetime.now(timezone.utc).timestamp() - float(ts), 1)
    except (TypeError, ValueError):
        return None


def _attach_live_metrics(pos: Dict, bot_report: Optional[Dict], price_info: Optional[Dict]) -> None:
    """Add the live-ish fields of an open position, in place.

    `bot_report` is the last position payload that bot sent (None until it reports again after
    a restart) and `price_info` the last cached quote for the symbol. Both arrive once per bar
    close, so each field carries its age instead of posing as current.

    Unrealized P&L is passed through exactly as the broker reported it and is NEVER
    reconstructed here: converting a price move into account currency needs the broker's pip
    and contract sizes plus an FX rate for non-USD instruments, and the previous per-symbol
    table got UK100's pip size 10x too large (cTrader reports pipSize 0.1, the table used 1.0).
    Missing reads as None so the UI can say so rather than print a guess.
    """
    side = (pos.get("side") or "BUY").upper()
    entry_price = float(pos.get("entry_price") or 0.0)

    current_price = None
    if price_info:
        current_price = price_info.get("bid") if side == "BUY" else price_info.get("ask")

    pos["current_price"] = current_price if current_price is not None else entry_price
    pos["price_age_seconds"] = _age_seconds(price_info.get("ts")) if price_info else None

    # The bot's report carries the stop as it stands now (break-even and trailing moves); the
    # DB row only has the level recorded at entry. The P&L at each level exists only in reports.
    # A P&L figure is shown only next to the level it was computed for.
    report = bot_report or {}
    for price_key, pnl_key in (("sl_price", "sl_pnl"), ("tp_price", "tp_pnl")):
        if report.get(price_key):
            pos[price_key] = report[price_key]
            pos[pnl_key] = report.get(pnl_key)
        else:
            pos[pnl_key] = None
    # With the TP gone at the broker, the entry-time one in the row is not where the trade exits.
    pos["no_tp"] = _no_live_tp(report)
    if pos["no_tp"]:
        pos["tp_price"] = None

    if bot_report and bot_report.get("unrealized_pnl") is not None:
        pos["unrealized_pnl"] = round(bot_report["unrealized_pnl"], 2)
        pos["unrealized_pnl_pips"] = round(bot_report.get("unrealized_pnl_pips", 0.0), 1)
        pos["pnl_age_seconds"] = _age_seconds(bot_report.get("_reported_at"))
    else:
        pos["unrealized_pnl"] = None
        pos["unrealized_pnl_pips"] = None
        pos["pnl_age_seconds"] = None

def tick_levels(payload: Any) -> Optional[Dict]:
    """SL/TP prices and the bot's P&L estimate at each, from a tick or a tick's position entry.

    Returns None when the payload carries none of these keys (a cBot build that predates them),
    so the caller leaves the cached levels alone. A present-but-null key means the position has
    no stop / no target. The P&L-at-level figures are computed by the bot from cTrader's own pip
    value and volume - see the note in _attach_live_metrics on why the server never does that.
    """
    if not isinstance(payload, dict):
        return None
    if not any(k in payload for k in ("sl", "tp", "sl_price", "tp_price", "sl_pnl", "tp_pnl")):
        return None

    def number(*keys: str, positive: bool = False) -> Optional[float]:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if positive and num <= 0:
                return None  # older bots send 0 for "no level"
            return round(num, 2) if not positive else num
        return None

    levels = {
        "sl_price": number("sl_price", "sl", positive=True),
        "tp_price": number("tp_price", "tp", positive=True),
        "sl_pnl": number("sl_pnl"),
        "tp_pnl": number("tp_pnl"),
    }
    levels["no_tp"] = _no_live_tp(levels)
    return levels


def _no_live_tp(levels: Dict) -> bool:
    """A bot's live levels hold a stop but no target: the broker has no TP on the position.

    FlowRSI takes the TP off when it starts trailing the runner left after its partial close,
    so the trade then exits only at its stop. Bots send SL and TP as a pair, so a report without
    a stop says nothing about the TP.
    """
    return bool(levels.get("sl_price")) and not levels.get("tp_price")


logger = logging.getLogger(__name__)

router = APIRouter()

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def get_db():
    """Get database connection."""
    return get_db_connection()

def get_portfolio_summary(account_id: str = "all") -> Dict:
    """Get portfolio summary statistics."""
    conn = get_db()
    try:
        params = []
        account_filter = ""
        if account_id and account_id != "all":
            if account_id in ("demo", "live"):
                account_filter = " AND account_id IN (SELECT account_id FROM accounts WHERE account_type = ? AND is_configured = 1)"
                params.append(account_id)
            else:
                account_filter = " AND account_id = ?"
                params.append(account_id)
        # Open positions count
        cursor = conn.execute(f"SELECT COUNT(*) FROM positions WHERE status = 'open'{account_filter}", tuple(params))
        open_positions = cursor.fetchone()[0]
        
        # Today's stats directly from positions
        today = date.today().isoformat()
        cursor = conn.execute(
            f"""
            SELECT 
                COALESCE(SUM(pnl), 0),
                COUNT(*),
                COALESCE(SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END), 0)
            FROM positions 
            WHERE status = 'closed' AND DATE(COALESCE(exit_time, entry_time)) = ?{account_filter}
            """,
            (today, *params)
        )
        row = cursor.fetchone()
        daily_pnl = row[0] if row and row[0] is not None else 0
        trades_today = row[1] if row and row[1] is not None else 0
        loss_streak = 0
        # Total P&L (all time)
        cursor = conn.execute(
            f"SELECT SUM(pnl) FROM positions WHERE status = 'closed'{account_filter}", tuple(params)
        )
        row_pnl = cursor.fetchone()
        total_pnl = row_pnl[0] if row_pnl and row_pnl[0] is not None else 0
        
        # Win rate
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM positions WHERE status = 'closed' AND pnl > 0{account_filter}", tuple(params)
        )
        wins = cursor.fetchone()[0]
        
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM positions WHERE status = 'closed'{account_filter}", tuple(params)
        )
        total_trades = cursor.fetchone()[0]
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        # Profit factor calculation
        cursor = conn.execute(
            f"SELECT COALESCE(SUM(pnl), 0.0) FROM positions WHERE status = 'closed' AND pnl > 0{account_filter}", tuple(params)
        )
        gross_profit = cursor.fetchone()[0] or 0.0

        cursor = conn.execute(
            f"SELECT COALESCE(ABS(SUM(pnl)), 0.0) FROM positions WHERE status = 'closed' AND pnl < 0{account_filter}", tuple(params)
        )
        gross_loss = cursor.fetchone()[0] or 0.0

        # None: no closed trade yet, or profit with no loss yet (an unbounded PF)
        profit_factor = compute_profit_factor(gross_profit, gross_loss, total_trades) if total_trades else None
        # Fetch account balance/equity
        account_balance = None
        account_equity = None
        if account_id in ("demo", "live"):
            cursor = conn.execute("SELECT SUM(last_balance), SUM(last_equity) FROM accounts WHERE account_type = ? AND is_configured = 1 AND last_balance > 0", (account_id,))
            sum_row = cursor.fetchone()
            if sum_row and sum_row[0] is not None:
                account_balance = sum_row[0]
                account_equity = sum_row[1]
        elif account_id and account_id != "all":
            cursor = conn.execute("SELECT last_balance, last_equity FROM accounts WHERE account_id = ?", (account_id,))
            acc_row = cursor.fetchone()
            if acc_row:
                account_balance = acc_row[0]
                account_equity = acc_row[1]
        else:
            cursor = conn.execute("SELECT SUM(last_balance), SUM(last_equity) FROM accounts WHERE is_configured = 1 AND last_balance > 0")
            sum_row = cursor.fetchone()
            if sum_row and sum_row[0] is not None:
                account_balance = sum_row[0]
                account_equity = sum_row[1]
        
        return {
            "open_positions": open_positions,
            "daily_pnl": round(daily_pnl, 2),
            "trades_today": trades_today,
            "loss_streak": loss_streak,
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": profit_factor,
            "total_trades": total_trades,
            "account_id": account_id,
            "account_balance": account_balance,
            "account_equity": account_equity
        }
    finally:
        conn.close()


def get_active_positions(account_id: str = "all") -> List[Dict]:
    """Get all active positions."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    positions = []
    try:
        query = """
            SELECT p.id, p.ctrader_id, p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.sl_pips, p.tp_pips, p.entry_time,
                   p.sl_price, p.tp_price, p.account_id, a.account_type, a.label as account_label
            FROM positions p
            LEFT JOIN accounts a ON p.account_id = a.account_id
            WHERE p.status = 'open'
        """
        params = []
        if account_id in ("demo", "live"):
            query += " AND a.account_type = ? AND a.is_configured = 1"
            params.append(account_id)
        elif account_id and account_id != "all":
            query += " AND p.account_id = ?"
            params.append(account_id)
            
        query += " ORDER BY p.entry_time DESC"
        
        cursor = conn.execute(query, tuple(params))
        positions = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    for pos in positions:
        pos["entry_time"] = format_vn_time(pos.get("entry_time"))
    pm = get_portfolio_manager()
    cache = getattr(pm, "_bot_positions_cache", {}) if hasattr(pm, "_bot_positions_cache") else {}
    for pos in positions:
        price_info = pm.get_latest_price(pos.get("symbol", "")) if hasattr(pm, "get_latest_price") else None
        bot_report = cache.get(f"{pos.get('account_id')}:{pos.get('bot_id')}") or cache.get(pos.get("bot_id"))
        _attach_live_metrics(pos, bot_report, price_info)

    return positions


def get_trade_history(account_id: str = "all", page: int = 1, page_size: int = 10) -> Dict:
    """Get one page of closed trades, newest exit first, plus paging metadata."""
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    conn = get_db()
    conn.row_factory = sqlite3.Row
    items = []
    total = 0
    try:
        where = " WHERE p.status = 'closed'"
        params = []
        if account_id in ("demo", "live"):
            where += " AND a.account_type = ? AND a.is_configured = 1"
            params.append(account_id)
        elif account_id and account_id != "all":
            where += " AND p.account_id = ?"
            params.append(account_id)

        cursor = conn.execute(
            f"SELECT COUNT(*) FROM positions p LEFT JOIN accounts a ON p.account_id = a.account_id{where}",
            tuple(params),
        )
        total = cursor.fetchone()[0] or 0

        cursor = conn.execute(
            f"""
            SELECT p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.exit_price, p.pnl, p.entry_time, p.exit_time,
                   p.sl_pips, p.tp_pips, p.sl_price, p.tp_price, p.close_reason,
                   p.account_id, a.account_type, a.label as account_label
            FROM positions p
            LEFT JOIN accounts a ON p.account_id = a.account_id{where}
            ORDER BY p.exit_time DESC LIMIT ? OFFSET ?
            """,
            (*params, page_size, (page - 1) * page_size),
        )
        for row in cursor.fetchall():
            d = dict(row)
            d["pnl"] = round(d["pnl"], 2) if d["pnl"] is not None else 0
            d["duration"] = format_duration(d["entry_time"], d["exit_time"])
            d["entry_time"] = format_vn_time(d["entry_time"])
            d["exit_time"] = format_vn_time(d["exit_time"])
            items.append(d)
    finally:
        conn.close()
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


def get_daily_pnl_history(days: int = 30, account_id: str = "all") -> List[Dict]:
    """Get daily P&L for the last N days directly from positions table (single source of truth)."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    history = []
    try:
        query = """
            SELECT 
                DATE(COALESCE(exit_time, entry_time)) as date,
                SUM(pnl) as pnl,
                COUNT(*) as trades
            FROM positions
            WHERE status = 'closed'
        """
        params = []
        if account_id in ("demo", "live"):
            query += " AND account_id IN (SELECT account_id FROM accounts WHERE account_type = ? AND is_configured = 1)"
            params.append(account_id)
        elif account_id and account_id != "all":
            query += " AND account_id = ?"
            params.append(account_id)
            
        query += " GROUP BY DATE(COALESCE(exit_time, entry_time)) ORDER BY date DESC LIMIT ?"
        params.append(days)
        
        cursor = conn.execute(query, tuple(params))
        for row in cursor.fetchall():
            history.append({
                "date": row["date"],
                "pnl": round(row["pnl"], 2) if row["pnl"] is not None else 0,
                "trades": row["trades"]
            })
    finally:
        conn.close()
    return list(reversed(history))  # Reverse to chronological order


# --- In-Memory Store for AI Decisions (Mini-Feed) ---
_latest_decisions: List[Dict] = []

def record_ai_decision(decision: Dict):
    """Store the AI decision in memory (FIFO up to 50)."""
    global _latest_decisions
    # Ensure timestamp exists
    if "timestamp" not in decision or not decision["timestamp"]:
        decision["timestamp"] = datetime.now(timezone.utc).isoformat()
    _latest_decisions.insert(0, decision)
    if len(_latest_decisions) > 50:
        _latest_decisions = _latest_decisions[:50]

def get_latest_ai_decisions(limit: int = 5, symbol: Optional[str] = None) -> List[Dict]:
    """Get latest AI decisions, optionally filtered by symbol."""
    if symbol and symbol.upper() != "ALL":
        sym_clean = symbol.strip().upper()
        filtered = [d for d in _latest_decisions if (d.get("symbol") or "").upper() == sym_clean]
        return filtered[:limit]
    return _latest_decisions[:limit]

def get_market_sessions_info() -> Dict:
    """Calculate current market sessions, Judas killzones, and weekend status."""
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # 0=Monday ... 4=Friday, 5=Saturday, 6=Sunday
    hour = now_utc.hour
    minute = now_utc.minute
    current_time_dec = hour + (minute / 60.0)

    # Forex market schedule: Closes Friday 21:00 UTC, Opens Sunday 21:00 UTC
    is_forex_weekend = (weekday == 4 and current_time_dec >= 21.0) or (weekday == 5) or (weekday == 6 and current_time_dec < 21.0)

    # Trading Sessions (UTC)
    # Sydney: 21:00 - 06:00 UTC
    sydney_active = (current_time_dec >= 21.0 or current_time_dec < 6.0) and not (weekday == 5 or (weekday == 4 and current_time_dec >= 21.0))
    # Tokyo: 00:00 - 09:00 UTC
    tokyo_active = (0.0 <= current_time_dec < 9.0) and not is_forex_weekend
    # London: 07:00 - 16:00 UTC
    london_active = (7.0 <= current_time_dec < 16.0) and not is_forex_weekend
    # New York: 12:00 - 21:00 UTC
    ny_active = (12.0 <= current_time_dec < 21.0) and not is_forex_weekend

    # Judas Killzones (UTC)
    # London Open Killzone: 07:00 - 10:00 UTC
    london_kz = (7.0 <= current_time_dec < 10.0) and not is_forex_weekend
    # New York Overlap Killzone: 12:30 - 16:00 UTC
    ny_kz = (12.5 <= current_time_dec < 16.0) and not is_forex_weekend

    active_kz_name = "Outside Killzones"
    if london_kz:
        active_kz_name = "London Open Killzone (07:00-10:00 UTC)"
    elif ny_kz:
        active_kz_name = "NY Overlap Killzone (12:30-16:00 UTC)"

    # Next Killzone Countdown
    next_kz_text = ""
    if london_kz or ny_kz:
        next_kz_text = "Active Now"
    elif not is_forex_weekend:
        if current_time_dec < 7.0:
            diff_h = 7.0 - current_time_dec
            next_kz_text = f"London KZ in {int(diff_h)}h {int((diff_h%1)*60)}m"
        elif current_time_dec < 12.5:
            diff_h = 12.5 - current_time_dec
            next_kz_text = f"NY KZ in {int(diff_h)}h {int((diff_h%1)*60)}m"
        else:
            diff_h = (24.0 - current_time_dec) + 7.0
            next_kz_text = f"London KZ in {int(diff_h)}h {int((diff_h%1)*60)}m"
    else:
        next_kz_text = "Market opens Mon 07:00 UTC"

    return {
        "utc_time": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "gmt7_time": (now_utc + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S (GMT+7)"),
        "is_forex_weekend": is_forex_weekend,
        "forex_status": "CLOSED (Weekend)" if is_forex_weekend else "OPEN",
        "crypto_status": "OPEN 24/7",
        "sessions": {
            "sydney": {"name": "Sydney", "active": sydney_active, "hours": "21:00-06:00 UTC"},
            "tokyo": {"name": "Tokyo", "active": tokyo_active, "hours": "00:00-09:00 UTC"},
            "london": {"name": "London", "active": london_active, "hours": "07:00-16:00 UTC"},
            "new_york": {"name": "New York", "active": ny_active, "hours": "12:00-21:00 UTC"}
        },
        "killzone": {
            "active": (london_kz or ny_kz),
            "name": active_kz_name,
            "next_in": next_kz_text
        }
    }

def get_asset_exposure(account_id: str = "all") -> Dict:
    """Compute asset exposure distribution by volume and count for active positions."""
    positions = get_active_positions(account_id)
    total_volume = sum(float(p.get("volume", 0) or 0) for p in positions)
    by_symbol: Dict[str, float] = {}
    by_asset_class: Dict[str, float] = {"Forex": 0.0, "Gold/Metals": 0.0, "Crypto": 0.0, "Indices": 0.0}

    for p in positions:
        sym = (p.get("symbol") or "UNKNOWN").upper()
        vol = float(p.get("volume", 0) or 0)
        by_symbol[sym] = round(by_symbol.get(sym, 0.0) + vol, 2)

        if "BTC" in sym or "ETH" in sym or "SOL" in sym or "XRP" in sym:
            by_asset_class["Crypto"] += vol
        elif "XAU" in sym or "GOLD" in sym:
            by_asset_class["Gold/Metals"] += vol
        elif any(idx in sym for idx in ["US30", "USTEC", "DE40", "NAS", "UK100", "JP225", "NIKKEI", "HK50", "US500"]):
            by_asset_class["Indices"] += vol
        else:
            by_asset_class["Forex"] += vol

    by_asset_class = {k: round(v, 2) for k, v in by_asset_class.items() if v > 0}
    return {
        "total_volume": round(total_volume, 2),
        "total_positions": len(positions),
        "by_symbol": by_symbol,
        "by_asset_class": by_asset_class
    }

def get_cumulative_equity_curve(days: int = 30, account_id: str = "all") -> List[Dict]:
    """Compute cumulative PnL curve for high-precision growth visualization."""
    daily = get_daily_pnl_history(days, account_id)
    cumulative = 0.0
    curve = []
    for d in daily:
        cumulative += float(d.get("pnl", 0.0) or 0.0)
        curve.append({
            "date": d["date"],
            "pnl": d["pnl"],
            "cum_pnl": round(cumulative, 2),
            "trades": d.get("trades", 0)
        })
    return curve
TRADE_MODE_FILE = PROJECT_ROOT / "webui_data" / "trade_mode.json"
DEFAULT_TRADE_MODE = "demo"

def get_persisted_trade_mode(request: Optional[Request] = None) -> str:
    """Read persisted trade mode from cookie, file, or default to demo."""
    if request:
        cookie_mode = request.cookies.get("agentfx_trade_mode")
        if cookie_mode in ("demo", "real", "live"):
            return "real" if cookie_mode in ("real", "live") else "demo"
            
    if not TRADE_MODE_FILE.is_file():
        return DEFAULT_TRADE_MODE
    try:
        with open(TRADE_MODE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        mode = str(data.get("mode", DEFAULT_TRADE_MODE)).strip().lower()
        return "real" if mode in ("real", "live") else "demo"
    except Exception:
        return DEFAULT_TRADE_MODE

def set_persisted_trade_mode(mode: str) -> str:
    """Persist trade mode to file."""
    m = "real" if mode in ("real", "live") else "demo"
    try:
        TRADE_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_MODE_FILE, "w", encoding="utf-8") as f:
            json.dump({"mode": m}, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist trade mode: {e}")
    return m

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def root_redirect(request: Request):
    """Redirect to the last visited trade mode (/real/dashboard or /demo/dashboard)."""
    mode = get_persisted_trade_mode(request)
    dest = "/real/dashboard" if mode == "real" else "/demo/dashboard"
    return RedirectResponse(url=dest)

@router.get("/demo", response_class=HTMLResponse)
async def demo_redirect(request: Request):
    return RedirectResponse(url="/demo/dashboard")

@router.get("/real", response_class=HTMLResponse)
@router.get("/live", response_class=HTMLResponse)
async def real_redirect(request: Request):
    return RedirectResponse(url="/real/dashboard")

@router.get("/demo/dashboard", response_class=HTMLResponse)
@router.get("/real/dashboard", response_class=HTMLResponse)
@router.get("/live/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render dashboard HTML page tailored to URL route (strictly demo or real)."""
    path = request.url.path
    if path.startswith("/real") or path.startswith("/live"):
        mode = "real"
        filter_acc = "live"
    else:
        mode = "demo"
        filter_acc = "demo"
        
    # Persist last used mode
    set_persisted_trade_mode(mode)
        
    summary = get_portfolio_summary(filter_acc)
    positions = get_active_positions(filter_acc)
    history = get_trade_history(filter_acc)
    pnl_history = get_daily_pnl_history(30, filter_acc)
    leaderboard = compute_bot_leaderboard(filter_acc)
    
    registry = get_account_registry()
    all_accounts = registry.list_accounts(include_unconfigured=False)
    accounts = [acc for acc in all_accounts if acc.get("account_type") == ("live" if mode == "real" else "demo")]
    
    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": summary,
            "positions": positions,
            "history": history,
            "pnl_history": pnl_history,
            "accounts": accounts,
            "current_mode": mode,
            "leaderboard": leaderboard,
            "market_sessions": get_market_sessions_info(),
            "exposure": get_asset_exposure(filter_acc),
            "latest_decisions": get_latest_ai_decisions(3),
        }
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@router.get("/api/dashboard/summary")
async def api_dashboard_summary(account_id: str = "all"):
    """API endpoint for portfolio summary."""
    return get_portfolio_summary(account_id)


@router.get("/api/dashboard/positions")
async def api_dashboard_positions(account_id: str = "all"):
    """API endpoint for active positions."""
    return get_active_positions(account_id)


@router.get("/api/dashboard/history")
async def api_dashboard_history(account_id: str = "all", page: int = 1, page_size: int = 10):
    """API endpoint for paginated trade history."""
    return get_trade_history(account_id, page, page_size)


@router.get("/api/dashboard/pnl-history")
async def api_dashboard_pnl_history(days: int = 30, account_id: str = "all"):
    """API endpoint for daily P&L history."""
    return get_daily_pnl_history(days, account_id)


@router.get("/api/leaderboard")
@router.get("/api/dashboard/leaderboard")
async def api_dashboard_leaderboard(account_id: str = "all", account_type: Optional[str] = None,
                                    period: str = "all"):
    """API endpoint for bot performance leaderboard and quant tier ranking.

    ``account_type`` (live|demo) keeps the ranking inside the active trading mode.
    ``period`` (1d|1w|1m|6m|1y|all) keeps the trades closed inside that rolling window.
    """
    return compute_bot_leaderboard(account_id, account_type=account_type, period=period)
@router.get("/api/dashboard/sessions")
async def api_dashboard_sessions():
    """API endpoint for trading sessions, killzones, and market status."""
    return get_market_sessions_info()

@router.get("/api/dashboard/exposure")
async def api_dashboard_exposure(account_id: str = "all"):
    """API endpoint for active asset volume exposure distribution."""
    return get_asset_exposure(account_id)

@router.get("/api/dashboard/cumulative-pnl")
async def api_dashboard_cumulative_pnl(days: int = 30, account_id: str = "all"):
    """API endpoint for cumulative equity curve."""
    return get_cumulative_equity_curve(days, account_id)

@router.get("/api/dashboard/latest-decisions")
async def api_dashboard_latest_decisions(limit: int = 20, symbol: Optional[str] = None):
    """API endpoint for latest AI decisions with structured reasoning and symbol filter."""
    return get_latest_ai_decisions(limit=limit, symbol=symbol)


@router.get("/api/dashboard/logs")
async def api_dashboard_logs(
    lines: int = 150, 
    date_str: Optional[str] = None,
    account_id: Optional[str] = None,
    mode: Optional[str] = None
):
    logs_dir = PROJECT_ROOT / "logs"
    if not logs_dir.exists():
        return {"lines": [], "date": date_str or date.today().isoformat(), "available_dates": []}
    
    today = date_str or date.today().isoformat()
    log_file = logs_dir / f"agent_{today}.log"
    
    available_dates = [f.stem.replace("agent_", "") for f in sorted(logs_dir.glob("agent_*.log"), reverse=True)]
    
    if not log_file.exists():
        if available_dates:
            today = available_dates[0]
            log_file = logs_dir / f"agent_{today}.log"
        else:
            return {"lines": [], "date": today, "available_dates": []}
    
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = [line.rstrip("\r\n") for line in f]
            
        filter_mode = (mode or "").lower()
        acc = (account_id or "").lower()
        
        target_mode = "all"
        if filter_mode in ("live", "real") or acc in ("live", "real"):
            target_mode = "live"
        elif filter_mode == "demo" or acc == "demo":
            target_mode = "demo"
        elif acc and acc != "all":
            target_mode = acc
            
        registry = get_account_registry()
        accounts = registry.list_accounts()
        
        if target_mode == "live":
            live_accounts = [a for a in accounts if a.get("account_type") == "live"]
            live_keys = ["live-"] + [a["account_id"].lower() for a in live_accounts] + [a["account_number"] for a in live_accounts]
            filtered = [
                l for l in all_lines 
                if any(k in l.lower() for k in live_keys) and "demo-" not in l.lower()
            ]
        elif target_mode == "demo":
            demo_accounts = [a for a in accounts if a.get("account_type") == "demo"]
            demo_keys = ["demo-"] + [a["account_id"].lower() for a in demo_accounts] + [a["account_number"] for a in demo_accounts]
            filtered = [
                l for l in all_lines 
                if any(k in l.lower() for k in demo_keys) and "live-" not in l.lower()
            ]
        elif target_mode != "all":
            filtered = [l for l in all_lines if target_mode in l.lower()]
        else:
            filtered = all_lines
            
        tail_lines = filtered[-lines:] if lines > 0 else filtered
            
        return {
            "lines": tail_lines,
            "date": today,
            "total_lines": len(filtered),
            "available_dates": available_dates,
            "mode": target_mode
        }
    except Exception as e:
        logger.error(f"Error reading log file {log_file}: {e}")
        return {"lines": [f"Error reading log: {e}"], "date": today, "available_dates": available_dates}

# WebSocket for real-time updates and log streaming
class ConnectionManager:
    """Manage WebSocket connections with channel filtering."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.connection_meta: Dict[WebSocket, Dict] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_meta[websocket] = {"account_id": "all", "mode": "all"}
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_meta.pop(websocket, None)

    def update_meta(self, websocket: WebSocket, account_id: str, mode: str = "all"):
        if websocket in self.connection_meta:
            self.connection_meta[websocket]["account_id"] = account_id
            self.connection_meta[websocket]["mode"] = mode
    
    async def broadcast(self, message: dict):
        """Fan a message out to every client without letting one slow client stall the rest.

        With a continuous tick stream a backgrounded browser tab or a dead link would otherwise
        hold up every other client's sends - and, through the acknowledgement path, the send
        cadence of the cBots themselves. Each send therefore carries a deadline; a client that
        misses it is dropped and its own reconnect logic re-syncs it.
        """
        connections = list(self.active_connections)
        if not connections:
            return

        results = await asyncio.gather(
            *(self._send_with_deadline(conn, message) for conn in connections),
            return_exceptions=True,
        )
        for conn, result in zip(connections, results):
            if result is not True:
                self.disconnect(conn)

    async def _send_with_deadline(self, connection: WebSocket, message: dict,
                                  timeout_seconds: float = 2.0) -> bool:
        """Send one message to one client, reporting failure instead of blocking the fan-out."""
        try:
            await asyncio.wait_for(connection.send_json(message), timeout=timeout_seconds)
            return True
        except Exception:
            return False

    async def broadcast_log(self, raw_line: str):
        """Broadcast formatted log line to all connected clients."""
        if not self.active_connections:
            return
        await self.broadcast({
            "type": "log",
            "line": raw_line,
            "timestamp": datetime.now().isoformat()
        })

    def broadcast_log_threadsafe(self, raw_line: str):
        """Thread-safe log broadcaster called by logging handlers."""
        if not self.active_connections:
            return
        try:
            loop = self._loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast_log(raw_line), loop)
        except Exception:
            pass


class WebSocketLogHandler(logging.Handler):
    """Custom logging handler that streams logs to active WebSocket connections."""
    def __init__(self, manager: ConnectionManager):
        super().__init__()
        self.manager = manager

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.manager.broadcast_log_threadsafe(msg)
        except Exception:
            self.handleError(record)


manager = ConnectionManager()


@router.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates, ticks, and logs."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            account_id = "all"
            mode = "all"
            try:
                if data.startswith("{"):
                    msg = json.loads(data)
                    msg_type = msg.get("type")
                    account_id = msg.get("account_id", "all")
                    mode = msg.get("mode", "all")
                    
                    manager.update_meta(websocket, account_id, mode)
                    
                    if msg_type in ("ping", "subscribe"):
                        summary = get_portfolio_summary(account_id)
                        positions = get_active_positions(account_id)
                        history = get_trade_history(account_id)["items"]
                        pnl_history = get_daily_pnl_history(30, account_id)
                        await websocket.send_json({
                            "type": "update",
                            "account_id": account_id,
                            "summary": summary,
                            "positions": positions,
                            "history": history,
                            "pnl_history": pnl_history
                        })
                elif data == "ping":
                    summary = get_portfolio_summary("all")
                    positions = get_active_positions("all")
                    history = get_trade_history("all")["items"]
                    pnl_history = get_daily_pnl_history(30, "all")
                    await websocket.send_json({
                        "type": "update",
                        "account_id": "all",
                        "summary": summary,
                        "positions": positions,
                        "history": history,
                        "pnl_history": pnl_history
                    })
            except Exception as parse_err:
                logger.debug(f"WS message error: {parse_err}")
                continue
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.websocket("/ws/cbot")
async def cbot_websocket_endpoint(websocket: WebSocket):
    """
    Bidirectional low-latency WebSocket endpoint for cBots.
    Handles fast tick telemetry streaming and real-time push orders/adjustments.
    """
    from app.cbot_watchdog import record_bot_tick

    await websocket.accept()
    bot_id = "unknown"
    try:
        while True:
            text = await websocket.receive_text()
            try:
                payload = json.loads(text)
                msg_type = payload.get("type", "tick")
                bot_id = payload.get("bot_id", bot_id)
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "time": datetime.now().isoformat()})
                elif msg_type == "tick":
                    symbol = payload.get("symbol")
                    bid = float(payload.get("bid", 0.0) or 0.0)
                    ask = float(payload.get("ask", 0.0) or 0.0)
                    account_id = payload.get("account_id")
                    if symbol and (bid > 0 or ask > 0):
                        pm = get_portfolio_manager()
                        pm.update_market_price(symbol, bid, ask, bot_id=bot_id)
                        record_bot_tick(bot_id, payload.get("last_bar"))

                        # A bot with a position open also sends its own broker P&L sample, which
                        # keeps the dashboard live between bar snapshots without the server
                        # reconstructing anything (it cannot: no pip size / FX rate server-side).
                        try:
                            pnl = float(payload["pnl"]) if payload.get("pnl") is not None else None
                            pips = float(payload["pips"]) if payload.get("pips") is not None else None
                        except (TypeError, ValueError):
                            pnl = pips = None
                        levels = tick_levels(payload)
                        if pnl is not None:
                            pm.update_position_metrics(bot_id, pnl, pips or 0.0, account_id=account_id,
                                                       levels=levels)

                        # Acknowledge before fanning out: the bot paces its next tick on this reply,
                        # so its cadence must not depend on the health of dashboard clients.
                        await websocket.send_json({"type": "ack", "status": "ok"})
                        try:
                            await broadcast_tick(symbol, bid, ask, account_id,
                                                 bot_id=bot_id, pnl=pnl, pips=pips,
                                                 levels=levels if pnl is not None else None)
                        except Exception:
                            pass
                    else:
                        await websocket.send_json({"type": "ack", "status": "ok"})
                else:
                    await websocket.send_json({"type": "ack", "status": "received"})
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    except WebSocketDisconnect:
        logger.info(f"[cBot WS] Bot {bot_id} disconnected")

async def broadcast_update(account_id: Optional[str] = None):
    """Broadcast dashboard summary, active positions, recent trades, and daily P&L to all connected clients."""
    targets = ["demo", "live", "all"]
    if account_id and account_id not in targets:
        targets.append(account_id)
    for target in targets:
        summary = get_portfolio_summary(target)
        positions = get_active_positions(target)
        history = get_trade_history(target)["items"]
        pnl_history = get_daily_pnl_history(30, target)
        await manager.broadcast({
            "type": "update",
            "account_id": target,
            "summary": summary,
            "positions": positions,
            "history": history,
            "pnl_history": pnl_history
        })

async def broadcast_tick(symbol: str, bid: float, ask: float, account_id: Optional[str] = None,
                         bot_id: Optional[str] = None, pnl: Optional[float] = None,
                         pips: Optional[float] = None, levels: Optional[Dict] = None):
    """Broadcast a live tick price update.

    When a bot has a position open it also reports its own P&L sample (broker net profit and
    pips), which rides along here so the dashboard can refresh prices *and* P&L from a single
    small message - no positions payload rebuild, no DB access.
    """
    await manager.broadcast({
        "type": "tick",
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "account_id": account_id,
        "bot_id": bot_id,
        "pnl": pnl,
        "pips": pips,
        # SL/TP prices and P&L at each (tick_levels); None when this bot sends no levels
        "levels": levels,
        "timestamp": datetime.now().isoformat()
    })


async def broadcast_event(event_type: str, message: str, bot_id: str = "", account_id: str = ""):
    """Broadcast cBot and system trading events."""
    await manager.broadcast({
        "type": "event",
        "event_type": event_type,
        "message": message,
        "bot_id": bot_id,
        "account_id": account_id,
        "timestamp": datetime.now().isoformat()
    })

async def broadcast_decision(decision: dict):
    """Broadcast AI decision to connected clients."""
    await manager.broadcast({
        "type": "ai_decision",
        "decision": decision,
        "timestamp": datetime.now().isoformat()
    })

# --- Docker Management Routes ---
from pydantic import BaseModel
from app.docker_manager import docker_manager
from app.portfolio import get_portfolio_manager

class BotConfigRequest(BaseModel):
    name: str
    description: str = ""
    run_command: str

class BotUpdateConfigRequest(BaseModel):
    description: Optional[str] = ""
    run_command: str
    restart: bool = True

# The bot control handlers below are plain `def` on purpose: FastAPI runs them in a threadpool.
# Docker calls block (stop/restart wait up to 10 s; inspect+logs took seconds under load), and
# inside `async def` that pinned the event loop, stalling /trade and /ws/cbot for every cBot.

@router.get("/api/bots")
def api_get_bots():
    from app.cbot_watchdog import cbot_watchdog
    pm = get_portfolio_manager()
    configs = pm.get_cbot_configs()
    open_counts = pm.open_position_counts()
    # enrich with status; health comes from the watchdog's last cycle (one docker inspect per
    # bot here instead of inspect+logs per bot per 10 s poll)
    for cfg in configs:
        cfg["bot_id"] = bot_id_for_config(cfg)
        cfg["paused"] = pm.is_bot_paused(cfg["bot_id"])
        cfg["open_positions"] = open_counts.get(cfg["bot_id"], 0)
        # A running bot polls /api/cbot/commands every 2 s; without that, Close and Close & Stop
        # can't reach it (NOT POLLING badge in the Bots tab).
        poll_age = command_queue.seconds_since_poll(cfg["bot_id"])
        cfg["polling"] = poll_age is not None and poll_age <= bot_commands.POLL_STALE_S
        cfg["last_poll_age_s"] = None if poll_age is None else round(poll_age)
        status_info = docker_manager.get_container_status(cfg["name"])
        status = status_info.get("status", "unknown")
        cfg["status"] = status
        cfg["container_id"] = status_info.get("id", "")
        if status != "running":
            health_info = {"healthy": False, "stuck": False, "reason": f"Container is {status}"}
        else:
            health_info = cbot_watchdog.last_health(cfg["name"]) or {
                "healthy": True, "stuck": False, "reason": "Awaiting first watchdog check"}
        cfg["healthy"] = health_info.get("healthy", True)
        cfg["stuck"] = health_info.get("stuck", False)
        cfg["health_reason"] = health_info.get("reason", "")
        cmd_lower = (cfg.get("run_command") or "").lower()
        name_lower = (cfg.get("name") or "").lower()
        if 'accountlabel="live"' in cmd_lower or "accountlabel='live'" in cmd_lower or "accountlabel=live" in cmd_lower or "-live" in name_lower or "live-" in name_lower or "_live" in name_lower or "--account=6094347" in cmd_lower:
            cfg["account_type"] = "live"
        else:
            cfg["account_type"] = "demo"
    return {"bots": configs, "docker_available": docker_manager.is_available}

@router.post("/api/bots")
async def api_add_bot(req: BotConfigRequest):
    pm = get_portfolio_manager()
    success = pm.add_cbot_config(req.name, req.description, req.run_command)
    if success:
        return {"success": True, "message": "Bot config added"}
    return {"success": False, "message": "Bot name already exists"}

@router.put("/api/bots/{name}")
@router.post("/api/bots/{name}/update")
def api_update_bot(name: str, req: BotUpdateConfigRequest):
    pm = get_portfolio_manager()
    success = pm.update_cbot_config(name, req.description or "", req.run_command)
    if not success:
        return {"success": False, "message": f"Bot {name} not found"}
    
    if req.restart:
        docker_manager.stop_container(name)
        docker_manager.remove_container(name)
        start_result = docker_manager.start_container(name, req.run_command)
        return {
            "success": True, 
            "message": f"Bot {name} updated and restarted with new parameters.",
            "start_result": start_result
        }
    
    return {"success": True, "message": f"Bot {name} configuration updated."}
@router.delete("/api/bots/{name}")
def api_delete_bot(name: str):
    """Stop and remove the container, then drop the config row.

    Dropping only the row used to leave the container running and trading, invisible to the
    dashboard and the watchdog. The row is kept when the container cannot be removed so the bot
    stays visible and the user can retry.
    """
    pm = get_portfolio_manager()
    if not pm.get_cbot_config(name):
        return {"success": False, "message": "Bot config not found"}
    docker_manager.stop_container(name)            # graceful first (cBot OnStop); "not found" is fine
    removed = docker_manager.remove_container(name)  # force=True; NotFound counts as removed
    if not removed.get("success"):
        return {"success": False, "message": f"Container not removed: {removed.get('message', 'docker error')}"}
    pm.delete_cbot_config(name)
    return {"success": True, "message": "Bot config and container deleted"}

@router.post("/api/bots/{name}/start")
def api_start_bot(name: str):
    pm = get_portfolio_manager()
    config = pm.get_cbot_config(name)
    if not config:
        return {"success": False, "message": "Bot config not found"}
    result = docker_manager.start_container(name, config["run_command"])
    return result

@router.post("/api/bots/{name}/stop")
def api_stop_bot(name: str):
    result = docker_manager.stop_container(name)
    return result

@router.post("/api/bots/{name}/remove")
def api_remove_bot(name: str):
    result = docker_manager.remove_container(name)
    return result

@router.post("/api/bots/{name}/restart")
def api_restart_bot(name: str):
    result = docker_manager.restart_container(name)
    return result


# --- Dashboard -> cBot commands: manual close, pause, Close & Stop (spec 2026-09-24) ---
# The server cannot reach a cBot: each bot polls GET /api/cbot/commands every 2 s and reports on
# POST /api/cbot/commands/{id}/result. See app/bot_commands.py and app/bot_controls.py.
from app import bot_commands
from app.bot_commands import command_queue, CommandNotFound, CommandNotOwned
from app.bot_controls import bot_id_for_config, sanitize_bot_id


class CommandResultRequest(BaseModel):
    bot_id: str
    status: str
    message: str = ""
    closed: int = 0
    failed: int = 0


@router.get("/api/cbot/commands")
async def api_cbot_poll_commands(bot_id: str):
    """Polled by every cBot every 2 s: its pause flag and its pending commands.

    `async def` and memory-only on purpose: 45 containers make ~22 calls a second, and a DB read
    here (the old HTTP /api/tick cost ~86 ms a call) would saturate the single uvicorn worker.
    """
    bot_id = sanitize_bot_id(bot_id)
    return {
        "paused": get_portfolio_manager().is_bot_paused(bot_id),
        "commands": [cmd.for_bot() for cmd in command_queue.take_pending(bot_id)],
    }


@router.post("/api/cbot/commands/{command_id}/result")
async def api_cbot_command_result(command_id: str, req: CommandResultRequest):
    """A cBot reports what came of a command it polled."""
    if req.status not in ("done", "failed"):
        return _error(422, "status must be 'done' or 'failed'")
    try:
        cmd = command_queue.record_result(command_id, sanitize_bot_id(req.bot_id), req.status,
                                          req.message, req.closed, req.failed)
    except CommandNotFound:
        return _error(404, "Unknown command")
    except CommandNotOwned:
        return _error(409, "The command belongs to another bot")
    log = logger.warning if cmd.status == "failed" else logger.info
    log(f"[MANUAL] {cmd.bot_id} {cmd.action} {cmd.id[:8]}: {cmd.status} ({cmd.message})")
    return {"ok": True}


@router.get("/api/bot-commands/{command_id}")
async def api_get_bot_command(command_id: str):
    """Status of a command, polled by the dashboard after a Close click."""
    cmd = command_queue.get(command_id)
    if cmd is None:
        return _error(404, "Unknown command")
    return cmd.for_dashboard()


def _config_for_bot(pm, bot_id: str) -> Optional[Dict]:
    """The cbot_configs row whose container reports `bot_id`, or None."""
    return next((cfg for cfg in pm.get_cbot_configs() if bot_id_for_config(cfg) == bot_id), None)


@router.post("/api/positions/{position_id}/close")
def api_close_position(position_id: int):
    """Ask the position's bot to close it at market (Close button in Active Positions)."""
    pm = get_portfolio_manager()
    pos = pm.get_open_position(position_id)
    if not pos:
        return _error(404, "Position not found or already closed")
    if pos.get("ctrader_id") is None:
        return _error(409, "This position has no cTrader id recorded; close it in cTrader")
    cfg = _config_for_bot(pm, pos["bot_id"])
    if cfg:
        status = docker_manager.get_container_status(cfg["name"]).get("status")
        if status != "running":
            return _error(409, f"Container {cfg['name']} is {status}; close the position in cTrader")
    cmd = command_queue.enqueue(pos["bot_id"], "close_position", int(pos["ctrader_id"]))
    logger.warning(f"[MANUAL] Close requested from the dashboard: {pos['side']} {pos['symbol']} "
                   f"{pos['volume']}L #{pos['ctrader_id']} of {pos['bot_id']} (command {cmd.id[:8]})")
    return {"success": True, "command_id": cmd.id, "status": cmd.status}


def _set_paused(name: str, paused: bool):
    pm = get_portfolio_manager()
    cfg = pm.get_cbot_config(name)
    if not cfg:
        return _error(404, "Bot config not found")
    bot_id = bot_id_for_config(cfg)
    pm.set_bot_paused(bot_id, paused)
    verb = "Paused" if paused else "Resumed"
    logger.warning(f"[MANUAL] {verb} new entries for {name} (bot_id {bot_id})")
    return {"success": True, "bot_id": bot_id, "paused": paused, "message": f"{verb} new entries for {name}"}


@router.post("/api/bots/{name}/pause")
def api_pause_bot(name: str):
    """Block the container's new entries; it keeps running and managing its open positions."""
    return _set_paused(name, True)


@router.post("/api/bots/{name}/resume")
def api_resume_bot(name: str):
    return _set_paused(name, False)


@router.post("/api/bots/{name}/close-and-stop")
async def api_close_and_stop_bot(name: str):
    """Pause the bot, have it close every position it holds, then stop its container.

    The container is stopped only once the bot reports every close done: stopping it with
    positions still open would leave them at the broker with nobody managing their stops. On any
    failure it keeps running, paused, and the message says what is left to close in cTrader.
    Docker and DB calls go through asyncio.to_thread so the wait never blocks the event loop.
    """
    pm = get_portfolio_manager()
    cfg = await asyncio.to_thread(pm.get_cbot_config, name)
    if not cfg:
        return _error(404, "Bot config not found")
    status = (await asyncio.to_thread(docker_manager.get_container_status, name)).get("status")
    if status != "running":
        return _error(409, f"Container {name} is {status}; its positions can't be closed from here, use cTrader")

    bot_id = bot_id_for_config(cfg)
    await asyncio.to_thread(pm.set_bot_paused, bot_id, True)
    cmd = command_queue.enqueue(bot_id, "close_all")
    logger.warning(f"[MANUAL] Close & Stop requested for {name}: paused, close_all command {cmd.id[:8]}")

    final = await bot_commands.wait_for_final(command_queue, cmd.id, bot_commands.CLOSE_AND_STOP_WAIT_S)
    if final is None or final.status != "done" or final.failed:
        why = (final.message or final.status) if final else "command lost"
        message = (f"{name} was NOT stopped: its positions could not all be closed ({why}). "
                   f"It keeps running, paused, and still manages its stops. Close what is left in cTrader.")
        logger.warning(f"[MANUAL] {message}")
        return {"success": False, "stage": "close", "paused": True, "message": message}

    stopped = await asyncio.to_thread(docker_manager.stop_container, name)
    if not stopped.get("success"):
        message = (f"Closed {final.closed} position(s) of {name}, but the container did not stop: "
                   f"{stopped.get('message', 'docker error')}")
        logger.warning(f"[MANUAL] {message}")
        return {"success": False, "stage": "stop", "paused": True, "closed": final.closed, "message": message}

    message = (f"Closed {final.closed} position(s) and stopped {name}. "
               f"It stays paused: after Start, click Resume to trade again.")
    logger.warning(f"[MANUAL] {message}")
    return {"success": True, "closed": final.closed, "paused": True, "message": message}

# --- cTrader accounts & preset-based instance setup (Setup Instances screen) ---
from fastapi.responses import JSONResponse
from app import ctrader_accounts as ctrader_accounts_service


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "message": message})


class CtraderAccountRequest(BaseModel):
    label: str = ""
    ctid_email: str = ""
    password: str = ""
    account_number: str = ""
    account_type: str = "demo"


@router.get("/api/ctrader-accounts")
async def api_list_ctrader_accounts():
    return {"accounts": ctrader_accounts_service.list_ctrader_accounts(get_account_registry())}


@router.post("/api/ctrader-accounts", status_code=201)
async def api_create_ctrader_account(req: CtraderAccountRequest):
    try:
        account = ctrader_accounts_service.create_ctrader_account(
            get_account_registry(), label=req.label, ctid_email=req.ctid_email, password=req.password,
            account_number=req.account_number, account_type=req.account_type,
        )
    except ctrader_accounts_service.AccountValidationError as e:
        return _error(422, str(e))
    except ctrader_accounts_service.DuplicateSlugError as e:
        return _error(409, str(e))
    except OSError as e:
        return _error(500, f"Could not write password file: {e}")
    return {"success": True, "account": account}


@router.delete("/api/ctrader-accounts/{account_id}")
async def api_delete_ctrader_account(account_id: int):
    if not ctrader_accounts_service.delete_ctrader_account(get_account_registry(), account_id):
        return _error(404, "Account not found")
    return {"success": True}


from app.cbot_presets import PRESETS, build_run_command, container_name, describe_cell, installed_cells, presets_payload


class InstanceSelection(BaseModel):
    symbol: str
    strategy: str


class SetupInstancesRequest(BaseModel):
    account_id: int
    selections: List[InstanceSelection]
    start: bool = True


@router.get("/api/setup/presets")
async def api_setup_presets():
    return presets_payload()


@router.get("/api/setup/installed")
async def api_setup_installed():
    """Preset cells that already have a bot config, with the account each was created for (no Docker calls)."""
    config_names = {cfg["name"] for cfg in get_portfolio_manager().get_cbot_configs()}
    return {"installed": installed_cells(get_account_registry().list_ctrader_accounts(), config_names)}


@router.post("/api/setup/instances")
# sync on purpose: up to 22 serial docker starts must not block the event loop (/trade, websockets)
def api_setup_instances(req: SetupInstancesRequest):
    """Save (and optionally start) one cbot_configs row per selected preset cell. Never aborts the batch."""
    account = get_account_registry().get_ctrader_account(req.account_id)
    if not account:
        return _error(404, "Account not found")
    pm = get_portfolio_manager()
    ctrader_home = ctrader_accounts_service.ctrader_home()
    results = []
    for sel in req.selections:
        symbol, strategy = sel.symbol.strip().upper(), sel.strategy.strip()
        entry = {"symbol": symbol, "strategy": strategy, "name": "", "status": "error", "message": ""}
        results.append(entry)
        if (strategy, symbol) not in PRESETS:
            entry["message"] = f"No preset for {strategy} × {symbol}"
            continue
        name = container_name(account["slug"], strategy, symbol)
        entry["name"] = name
        cmd = build_run_command(account, strategy, symbol, str(PROJECT_ROOT), ctrader_home)
        # get_cbot_config first, so an existing cell is reported without attempting the INSERT
        if pm.get_cbot_config(name) or not pm.add_cbot_config(name, describe_cell(strategy, symbol, account["label"]), cmd):
            entry.update(status="exists", message="Bot config already exists")
            continue
        if not req.start:
            entry.update(status="saved", message="Config saved")
            continue
        result = docker_manager.start_container(name, cmd)
        if result.get("success"):
            entry.update(status="started", message=result.get("message", "Container started"))
        else:
            entry["message"] = result.get("message", "Docker error")   # config row kept so the user can retry
    return {"results": results}


@router.get("/api/watchdog/status")
async def api_watchdog_status():
    from app.cbot_watchdog import cbot_watchdog
    return cbot_watchdog.get_status()


# ── Daily loss limits (account / strategy / container) ─────────────────────

class RiskLimitUpdate(BaseModel):
    scope: str
    target: str
    max_daily_loss: Optional[float] = None
    enabled: Optional[bool] = None


class RiskLimitsRequest(BaseModel):
    limits: List[RiskLimitUpdate]


RISK_CONTAINERS_SHOWN = 8


@router.get("/api/risk-limits")
def api_get_risk_limits():
    """The limits and today's loss against them, per account: the account, each strategy,
    and the containers that have lost the most."""
    pm = get_portfolio_manager()
    accounts = []
    for account_id in pm.get_account_ids():
        usage = pm.daily_loss_usage(account_id)
        containers = sorted(
            ({"bot_id": bot_id, **used} for bot_id, used in usage["container"].items()),
            key=lambda c: c["total"],
        )[:RISK_CONTAINERS_SHOWN]
        accounts.append({
            "account_id": account_id,
            "account": usage["account"],
            "strategy": usage["strategy"],
            "containers": containers,
        })
    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "limits": pm.get_risk_limits(),
        "accounts": accounts,
    }


@router.put("/api/risk-limits")
def api_put_risk_limits(req: RiskLimitsRequest):
    pm = get_portfolio_manager()
    try:
        limits = pm.update_risk_limits([u.model_dump() for u in req.limits])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Risk limits updated: {[u.model_dump() for u in req.limits]}")
    return {"limits": limits}

# ── News Service & Macro Assessment API Endpoints ─────────────────────────

class NewsAssessRequest(BaseModel):
    cluster_id: str
    symbol: str = "XAUUSD"
    range: str = "thisweek"
    notes: str = ""
    cluster_data: Optional[Dict[str, Any]] = None
@router.get("/api/news/raw")
async def api_news_raw(range: str = "thisweek", refresh: bool = False):
    """
    Returns raw ForexFactory events JSON for cBots and downstream consumers.
    Leverages backend caching (15-min TTL) to prevent 429 rate-limiting.
    """
    try:
        events = await news_service.fetch_forexfactory_raw_events(range, force_refresh=refresh)
        return events
    except Exception as ex:
        logger.error(f"[News API] Error fetching raw calendar: {ex}")
        return []

@router.get("/api/news/calendar")
async def api_news_calendar(range: str = "thisweek", refresh: bool = False):
    try:
        events = await news_service.fetch_forexfactory_raw_events(range, force_refresh=refresh)
        clusters = news_service.cluster_red_news(events)
        for c in clusters:
            latest = news_service.get_latest_assessment_for_cluster(c["id"])
            if latest:
                c["is_assessed"] = True
                c["latest_assessment"] = latest
        return {
            "success": True,
            "range": range,
            "total_clusters": len(clusters),
            "clusters": clusters,
            "raw_events_count": len(events)
        }
    except Exception as ex:
        logger.error(f"[News API] Error fetching calendar: {ex}")
        return {"success": False, "error": str(ex), "clusters": []}

@router.post("/api/news/assess")
async def api_news_assess(req: NewsAssessRequest):
    try:
        result = await news_service.assess_news_cluster(
            cluster_id=req.cluster_id,
            symbol=req.symbol,
            week_range=req.range,
            user_notes=req.notes,
            cluster_data=req.cluster_data
        )
        return {"success": True, "assessment": result}
    except Exception as ex:
        logger.error(f"[News API] Error assessing cluster {req.cluster_id}: {ex}")
        return {"success": False, "error": str(ex)}

@router.get("/api/news/assessments")
async def api_news_assessments(limit: int = 50):
    try:
        items = news_service.get_recent_news_assessments(limit=limit)
        return {"success": True, "assessments": items, "count": len(items)}
    except Exception as ex:
        logger.error(f"[News API] Error retrieving assessments: {ex}")
        return {"success": False, "error": str(ex), "assessments": []}

@router.get("/api/news/assessments/{assessment_id}")
async def api_news_assessment_detail(assessment_id: int):
    item = news_service.get_news_assessment_by_id(assessment_id)
    if not item:
        return {"success": False, "error": "Assessment not found"}
    return {"success": True, "assessment": item}

@router.get("/api/news/shield-status")
async def api_news_shield_status(symbol: str = "XAUUSD", pause_before: int = 30, pause_after: int = 30):
    try:
        is_active, title, remaining_mins = await news_service.is_news_blackout_active(
            symbol=symbol,
            pause_before_mins=pause_before,
            pause_after_mins=pause_after
        )
        return {
            "success": True,
            "symbol": symbol.upper(),
            "is_blackout": is_active,
            "event_title": title,
            "remaining_minutes": remaining_mins
        }
    except Exception as ex:
        return {"success": False, "error": str(ex), "is_blackout": False}

