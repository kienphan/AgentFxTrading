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
import json
from datetime import datetime, date
from typing import Dict, List, Optional
from app.accounts import get_account_registry
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def get_db():
    """Get database connection."""
    return sqlite3.connect(DB_PATH)


def get_portfolio_summary(account_id: str = "all") -> Dict:
    """Get portfolio summary statistics."""
    conn = get_db()
    
    params = []
    account_filter = ""
    if account_id and account_id != "all":
        account_filter = " AND account_id = ?"
        params.append(account_id)

    # Open positions count
    cursor = conn.execute(f"SELECT COUNT(*) FROM positions WHERE status = 'open'{account_filter}", tuple(params))
    open_positions = cursor.fetchone()[0]
    
    # Today's stats
    today = date.today().isoformat()
    cursor = conn.execute(
        f"SELECT SUM(total_pnl), SUM(trades_count), MAX(loss_streak) FROM daily_stats WHERE date = ?{account_filter}",
        (today, *params)
    )
    row = cursor.fetchone()
    daily_pnl = row[0] if row and row[0] is not None else 0
    trades_today = row[1] if row and row[1] is not None else 0
    loss_streak = row[2] if row and row[2] is not None else 0
    
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
    
    # Fetch account balance/equity if specific account
    # Fetch account balance/equity
    account_balance = None
    account_equity = None
    if account_id and account_id != "all":
        cursor = conn.execute("SELECT last_balance, last_equity FROM accounts WHERE account_id = ?", (account_id,))
        acc_row = cursor.fetchone()
        if acc_row:
            account_balance = acc_row[0]
            account_equity = acc_row[1]
    else:
        cursor = conn.execute("SELECT SUM(last_balance), SUM(last_equity) FROM accounts WHERE last_balance > 0")
        sum_row = cursor.fetchone()
        if sum_row and sum_row[0] is not None:
            account_balance = sum_row[0]
            account_equity = sum_row[1]
    conn.close()
    
    return {
        "open_positions": open_positions,
        "daily_pnl": round(daily_pnl, 2),
        "trades_today": trades_today,
        "loss_streak": loss_streak,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "account_id": account_id,
        "account_balance": account_balance,
        "account_equity": account_equity
    }


def get_active_positions(account_id: str = "all") -> List[Dict]:
    """Get all active positions."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    query = """
        SELECT p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.sl_pips, p.tp_pips, p.entry_time,
               p.account_id, a.account_type, a.label as account_label
        FROM positions p
        LEFT JOIN accounts a ON p.account_id = a.account_id
        WHERE p.status = 'open'
    """
    params = []
    if account_id and account_id != "all":
        query += " AND p.account_id = ?"
        params.append(account_id)
        
    query += " ORDER BY p.entry_time DESC"
    
    cursor = conn.execute(query, tuple(params))
    positions = [dict(row) for row in cursor.fetchall()]
    conn.close()

    pm = get_portfolio_manager()
    for pos in positions:
        symbol = pos.get("symbol", "")
        side = (pos.get("side") or "BUY").upper()
        entry_price = float(pos.get("entry_price") or 0.0)
        volume = float(pos.get("volume") or 0.01)
        bot_id = pos.get("bot_id")

        bot_pos = getattr(pm, "_bot_positions_cache", {}).get(bot_id) if hasattr(pm, "_bot_positions_cache") else None
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
            elif any(k in symbol for k in ("US30", "USTEC", "DE40", "GER40", "NAS100")):
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
    
    query = """
        SELECT p.bot_id, p.symbol, UPPER(p.side) as side, p.volume, p.entry_price, p.exit_price, p.pnl, p.entry_time, p.exit_time,
               p.account_id, a.account_type, a.label as account_label
        FROM positions p
        LEFT JOIN accounts a ON p.account_id = a.account_id
        WHERE p.status = 'closed'
    """
    params = []
    if account_id and account_id != "all":
        query += " AND p.account_id = ?"
        params.append(account_id)
        
    query += " ORDER BY p.exit_time DESC LIMIT ?"
    params.append(limit)
    
    cursor = conn.execute(query, tuple(params))
    
    trades = []
    for row in cursor.fetchall():
        d = dict(row)
        d["pnl"] = round(d["pnl"], 2) if d["pnl"] is not None else 0
        trades.append(d)
    
    conn.close()
    return trades


def get_daily_pnl_history(days: int = 30, account_id: str = "all") -> List[Dict]:
    """Get daily P&L for the last N days."""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    
    query = """
        SELECT date, SUM(total_pnl) as pnl, SUM(trades_count) as trades
        FROM daily_stats
    """
    params = []
    if account_id and account_id != "all":
        query += " WHERE account_id = ?"
        params.append(account_id)
        
    query += " GROUP BY date ORDER BY date DESC LIMIT ?"
    params.append(days)
    
    cursor = conn.execute(query, tuple(params))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            "date": row["date"],
            "pnl": round(row["pnl"], 2) if row["pnl"] is not None else 0,
            "trades": row["trades"]
        })
    
    conn.close()
    return list(reversed(history))  # Reverse to chronological order

@router.get("/", response_class=HTMLResponse)
async def root_redirect(request: Request):
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render dashboard HTML page."""
    
    summary = get_portfolio_summary()
    positions = get_active_positions()
    history = get_trade_history(20)
    pnl_history = get_daily_pnl_history(30)
    
    registry = get_account_registry()
    accounts = registry.list_accounts()
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": summary,
            "positions": positions,
            "history": history,
            "pnl_history": pnl_history,
            "accounts": accounts
        }
    )


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


@router.get("/api/dashboard/logs")
async def api_dashboard_logs(lines: int = 100, date_str: Optional[str] = None):
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
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:] if lines > 0 else all_lines
            
        return {
            "lines": [line.rstrip("\r\n") for line in tail_lines],
            "date": today,
            "total_lines": len(all_lines),
            "available_dates": available_dates
        }
    except Exception as e:
        logger.error(f"Error reading log file {log_file}: {e}")
        return {"lines": [f"Error reading log: {e}"], "date": today, "available_dates": available_dates}


# WebSocket for real-time updates
class ConnectionManager:
    """Manage WebSocket connections."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


@router.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and send updates every 5 seconds
            data = await websocket.receive_text()
            
            account_id = "all"
            try:
                if data.startswith("{"):
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        account_id = msg.get("account_id", "all")
                elif data == "ping":
                    account_id = "all"
                else:
                    continue
            except:
                if data == "ping":
                    account_id = "all"
                else:
                    continue
                    
            summary = get_portfolio_summary(account_id)
            positions = get_active_positions(account_id)
            await websocket.send_json({
                "type": "update",
                "account_id": account_id,
                "summary": summary,
                "positions": positions
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast_update():
    """Broadcast dashboard update to all connected clients."""
    # With per-account views, broadcast sends the "all" view. 
    # Clients on specific accounts will refetch or wait for their next ping.
    summary = get_portfolio_summary("all")
    positions = get_active_positions("all")
    await manager.broadcast({
        "type": "update",
        "account_id": "all",
        "summary": summary,
        "positions": positions
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
