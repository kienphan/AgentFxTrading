"""
Dashboard for monitoring AgentFxTrading system.
Provides real-time visualization of positions, P&L, and bot status.
"""

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
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
from app.leaderboard import compute_bot_leaderboard
import logging
from app import news_service

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

        if total_trades == 0:
            profit_factor = None
        elif gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = round(gross_profit, 2)
        else:
            profit_factor = 0.0
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
            SELECT p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.sl_pips, p.tp_pips, p.entry_time,
                   p.account_id, a.account_type, a.label as account_label
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
    pm = get_portfolio_manager()
    for pos in positions:
        symbol = pos.get("symbol", "")
        side = (pos.get("side") or "BUY").upper()
        entry_price = float(pos.get("entry_price") or 0.0)
        volume = float(pos.get("volume") or 0.01)
        bot_id = pos.get("bot_id")

        acc_id = pos.get("account_id")
        cache = getattr(pm, "_bot_positions_cache", {}) if hasattr(pm, "_bot_positions_cache") else {}
        bot_pos = cache.get(f"{acc_id}:{bot_id}") or cache.get(bot_id)
        price_info = pm.get_latest_price(symbol) if hasattr(pm, "get_latest_price") else None
        
        current_price = None
        if price_info:
            current_price = price_info.get("bid") if side == "BUY" else price_info.get("ask")
            
        pos["current_price"] = current_price if current_price is not None else entry_price
        
        if bot_pos and bot_pos.get("unrealized_pnl") is not None:
            pos["unrealized_pnl"] = round(bot_pos["unrealized_pnl"], 2)
            pos["unrealized_pnl_pips"] = round(bot_pos.get("unrealized_pnl_pips", 0.0), 1)
        elif current_price and entry_price:
            diff = (current_price - entry_price) if side == "BUY" else (entry_price - current_price)
            if "JPY" in symbol:
                pip_size = 0.01
                multiplier = 6.3
            elif "XAU" in symbol or "GOLD" in symbol:
                pip_size = 0.1
                multiplier = 10.0
            elif any(k in symbol for k in ("US30", "USTEC", "DE40", "GER40", "NAS100", "UK100", "GB100")):
                pip_size = 1.0
                multiplier = 1.0
            else:
                pip_size = 0.0001
                multiplier = 10.0
                
            pnl_pips = diff / pip_size
            unrealized_pnl = round(pnl_pips * volume * multiplier, 2)
            pos["unrealized_pnl"] = unrealized_pnl
            pos["unrealized_pnl_pips"] = round(pnl_pips, 1)
        else:
            pos["unrealized_pnl"] = 0.0
            pos["unrealized_pnl_pips"] = 0.0

    return positions


def get_trade_history(limit: int = 50, account_id: str = "all") -> List[Dict]:
    """Get recent trade history."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    trades = []
    try:
        query = """
            SELECT p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.exit_price, p.pnl, p.entry_time, p.exit_time,
                   p.account_id, a.account_type, a.label as account_label
            FROM positions p
            LEFT JOIN accounts a ON p.account_id = a.account_id
            WHERE p.status = 'closed'
        """
        params = []
        if account_id in ("demo", "live"):
            query += " AND a.account_type = ? AND a.is_configured = 1"
            params.append(account_id)
        elif account_id and account_id != "all":
            query += " AND p.account_id = ?"
            params.append(account_id)
            
        query += " ORDER BY p.exit_time DESC LIMIT ?"
        params.append(limit)
        
        cursor = conn.execute(query, tuple(params))
        for row in cursor.fetchall():
            d = dict(row)
            d["pnl"] = round(d["pnl"], 2) if d["pnl"] is not None else 0
            trades.append(d)
    finally:
        conn.close()
    return trades


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
        elif "US30" in sym or "USTEC" in sym or "DE40" in sym or "NAS" in sym or "UK100" in sym:
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
    history = get_trade_history(20, filter_acc)
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
async def api_dashboard_history(limit: int = 50, account_id: str = "all"):
    """API endpoint for trade history."""
    return get_trade_history(limit, account_id)


@router.get("/api/dashboard/pnl-history")
async def api_dashboard_pnl_history(days: int = 30, account_id: str = "all"):
    """API endpoint for daily P&L history."""
    return get_daily_pnl_history(days, account_id)


@router.get("/api/leaderboard")
@router.get("/api/dashboard/leaderboard")
async def api_dashboard_leaderboard(account_id: str = "all"):
    """API endpoint for bot performance leaderboard and quant tier ranking."""
    return compute_bot_leaderboard(account_id)
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
        """Broadcast message to all connected clients."""
        dead_connections = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dc in dead_connections:
            self.disconnect(dc)

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
                        await websocket.send_json({
                            "type": "update",
                            "account_id": account_id,
                            "summary": summary,
                            "positions": positions
                        })
                elif data == "ping":
                    summary = get_portfolio_summary("all")
                    positions = get_active_positions("all")
                    await websocket.send_json({
                        "type": "update",
                        "account_id": "all",
                        "summary": summary,
                        "positions": positions
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
                        await broadcast_tick(symbol, bid, ask, account_id)
                    await websocket.send_json({"type": "ack", "status": "ok"})
                else:
                    await websocket.send_json({"type": "ack", "status": "received"})
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
    except WebSocketDisconnect:
        logger.info(f"[cBot WS] Bot {bot_id} disconnected")

async def broadcast_update():
    """Broadcast dashboard summary and positions to all connected clients."""
    for target in ["demo", "live", "all"]:
        summary = get_portfolio_summary(target)
        positions = get_active_positions(target)
        await manager.broadcast({
            "type": "update",
            "account_id": target,
            "summary": summary,
            "positions": positions
        })


async def broadcast_tick(symbol: str, bid: float, ask: float, account_id: Optional[str] = None):
    """Broadcast live tick price update."""
    await manager.broadcast({
        "type": "tick",
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "account_id": account_id,
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

@router.get("/api/bots")
async def api_get_bots():
    pm = get_portfolio_manager()
    configs = pm.get_cbot_configs()
    # enrich with status
    for cfg in configs:
        status_info = docker_manager.get_container_status(cfg["name"])
        cfg["status"] = status_info.get("status", "unknown")
        cfg["container_id"] = status_info.get("id", "")
        health_info = docker_manager.check_cbot_health(cfg["name"])
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
async def api_update_bot(name: str, req: BotUpdateConfigRequest):
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
async def api_delete_bot(name: str):
    pm = get_portfolio_manager()
    success = pm.delete_cbot_config(name)
    if success:
        return {"success": True, "message": "Bot config deleted"}
    return {"success": False, "message": "Bot config not found"}

@router.post("/api/bots/{name}/start")
async def api_start_bot(name: str):
    pm = get_portfolio_manager()
    config = pm.get_cbot_config(name)
    if not config:
        return {"success": False, "message": "Bot config not found"}
    result = docker_manager.start_container(name, config["run_command"])
    return result

@router.post("/api/bots/{name}/stop")
async def api_stop_bot(name: str):
    result = docker_manager.stop_container(name)
    return result

@router.post("/api/bots/{name}/remove")
async def api_remove_bot(name: str):
    result = docker_manager.remove_container(name)
    return result

@router.post("/api/bots/{name}/restart")
async def api_restart_bot(name: str):
    result = docker_manager.restart_container(name)
    return result

@router.get("/api/watchdog/status")
async def api_watchdog_status():
    from app.cbot_watchdog import cbot_watchdog
    return cbot_watchdog.get_status()

# ── News Service & Macro Assessment API Endpoints ─────────────────────────

class NewsAssessRequest(BaseModel):
    cluster_id: str
    symbol: str = "XAUUSD"
    range: str = "thisweek"
    notes: str = ""
    cluster_data: Optional[Dict[str, Any]] = None
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

