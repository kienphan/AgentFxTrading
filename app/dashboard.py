"""
Dashboard for monitoring AgentFxTrading system.
Provides real-time visualization of positions, P&L, and bot status.
"""

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import sqlite3
import json
from datetime import datetime, date
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Database path
DB_PATH = Path("portfolio.db")


def get_db():
    """Get database connection."""
    return sqlite3.connect(DB_PATH)


def get_portfolio_summary() -> Dict:
    """Get portfolio summary statistics."""
    conn = get_db()
    
    # Open positions count
    cursor = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
    open_positions = cursor.fetchone()[0]
    
    # Today's stats
    today = date.today().isoformat()
    cursor = conn.execute(
        "SELECT total_pnl, trades_count, loss_streak FROM daily_stats WHERE date = ?",
        (today,)
    )
    row = cursor.fetchone()
    daily_pnl = row[0] if row else 0
    trades_today = row[1] if row else 0
    loss_streak = row[2] if row else 0
    
    # Total P&L (all time)
    cursor = conn.execute(
        "SELECT SUM(pnl) FROM positions WHERE status = 'closed'"
    )
    total_pnl = cursor.fetchone()[0] or 0
    
    # Win rate
    cursor = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND pnl > 0"
    )
    wins = cursor.fetchone()[0]
    
    cursor = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE status = 'closed'"
    )
    total_trades = cursor.fetchone()[0]
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    conn.close()
    
    return {
        "open_positions": open_positions,
        "daily_pnl": round(daily_pnl, 2),
        "trades_today": trades_today,
        "loss_streak": loss_streak,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1)
    }


def get_active_positions() -> List[Dict]:
    """Get all active positions."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, entry_time
        FROM positions 
        WHERE status = 'open'
        ORDER BY entry_time DESC
    """)
    
    positions = []
    for row in cursor.fetchall():
        positions.append({
            "bot_id": row[0],
            "symbol": row[1],
            "side": row[2],
            "volume": row[3],
            "entry_price": row[4],
            "sl_pips": row[5],
            "tp_pips": row[6],
            "entry_time": row[7]
        })
    
    conn.close()
    return positions


def get_trade_history(limit: int = 50) -> List[Dict]:
    """Get recent trade history."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT bot_id, symbol, side, volume, entry_price, exit_price, pnl, entry_time, exit_time
        FROM positions 
        WHERE status = 'closed'
        ORDER BY exit_time DESC
        LIMIT ?
    """, (limit,))
    
    trades = []
    for row in cursor.fetchall():
        trades.append({
            "bot_id": row[0],
            "symbol": row[1],
            "side": row[2],
            "volume": row[3],
            "entry_price": row[4],
            "exit_price": row[5],
            "pnl": round(row[6], 2),
            "entry_time": row[7],
            "exit_time": row[8]
        })
    
    conn.close()
    return trades


def get_daily_pnl_history(days: int = 30) -> List[Dict]:
    """Get daily P&L for the last N days."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT date, total_pnl, trades_count
        FROM daily_stats
        ORDER BY date DESC
        LIMIT ?
    """, (days,))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            "date": row[0],
            "pnl": round(row[1], 2),
            "trades": row[2]
        })
    
    conn.close()
    return list(reversed(history))  # Reverse to chronological order


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Render dashboard HTML page."""
    templates = Jinja2Templates(directory="templates")
    
    summary = get_portfolio_summary()
    positions = get_active_positions()
    history = get_trade_history(20)
    pnl_history = get_daily_pnl_history(30)
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "positions": positions,
            "history": history,
            "pnl_history": pnl_history
        }
    )


@router.get("/api/dashboard/summary")
async def api_dashboard_summary():
    """API endpoint for portfolio summary."""
    return get_portfolio_summary()


@router.get("/api/dashboard/positions")
async def api_dashboard_positions():
    """API endpoint for active positions."""
    return get_active_positions()


@router.get("/api/dashboard/history")
async def api_dashboard_history(limit: int = 50):
    """API endpoint for trade history."""
    return get_trade_history(limit)


@router.get("/api/dashboard/pnl-history")
async def api_dashboard_pnl_history(days: int = 30):
    """API endpoint for daily P&L history."""
    return get_daily_pnl_history(days)


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
            if data == "ping":
                summary = get_portfolio_summary()
                positions = get_active_positions()
                await websocket.send_json({
                    "type": "update",
                    "summary": summary,
                    "positions": positions
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast_update():
    """Broadcast dashboard update to all connected clients."""
    summary = get_portfolio_summary()
    positions = get_active_positions()
    await manager.broadcast({
        "type": "update",
        "summary": summary,
        "positions": positions
    })
