import sys
from pathlib import Path
import asyncio
import json
import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app, ws_manager
from app.dashboard import broadcast_update, broadcast_tick, broadcast_event


def test_websocket_connection_and_ping():
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        # Send ping
        ws.send_text(json.dumps({"type": "ping", "account_id": "all"}))
        data = ws.receive_json()
        assert data["type"] == "update"
        assert "summary" in data
        assert "positions" in data
        assert data["account_id"] == "all"


def test_websocket_subscription_and_logging_broadcast():
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        # Subscribe
        ws.send_text(json.dumps({"type": "subscribe", "account_id": "demo", "mode": "demo"}))
        data = ws.receive_json()
        assert data["type"] == "update"
        
        # Test direct log broadcast
        test_log_line = "12:00:00 [INFO] AgentFxTrading: [AI Decision] BUY EURUSD"
        ws_manager.broadcast_log_threadsafe(test_log_line)
        # Verify broadcast receives if event loop was set or via async broadcast
        asyncio.run(ws_manager.broadcast_log(test_log_line))
        received = ws.receive_json()
        assert received["type"] == "log"
        assert received["line"] == test_log_line


def test_websocket_tick_and_event_broadcast():
    client = TestClient(app)
    with client.websocket_connect("/ws/dashboard") as ws:
        # Initial ping/update
        ws.send_text(json.dumps({"type": "ping", "account_id": "all"}))
        _ = ws.receive_json()

        # Broadcast tick
        asyncio.run(broadcast_tick(symbol="XAUUSD", bid=2650.50, ask=2650.80, account_id="demo"))
        tick_msg = ws.receive_json()
        assert tick_msg["type"] == "tick"
        assert tick_msg["symbol"] == "XAUUSD"
        assert tick_msg["bid"] == 2650.50
        assert tick_msg["ask"] == 2650.80

        # Broadcast event
        asyncio.run(broadcast_event(event_type="GUARDRAIL", message="High impact news pause active", bot_id="judas_xau", account_id="demo"))
        event_msg = ws.receive_json()
        assert event_msg["type"] == "event"
        assert event_msg["event_type"] == "GUARDRAIL"
        assert "High impact news" in event_msg["message"]
