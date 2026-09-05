"""
Tests for Bot Quantitative Performance Leaderboard & Ranking System.
"""

import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.leaderboard import calculate_quant_score, compute_bot_leaderboard
from app.server import app


@pytest.fixture
def temp_db(tmp_path):
    """Creates a temporary SQLite database with positions and accounts tables."""
    db_file = tmp_path / "test_portfolio.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE accounts (
            account_id TEXT PRIMARY KEY,
            account_type TEXT,
            label TEXT,
            is_configured INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            volume REAL NOT NULL,
            entry_price REAL NOT NULL,
            sl_pips REAL,
            tp_pips REAL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            pnl REAL,
            status TEXT DEFAULT 'open',
            account_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE cbot_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            run_command TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_file


def test_calculate_quant_score_tiers():
    """Test score computation and tier classification."""
    # Top tier performer (High win rate, high PF, strong positive PnL)
    score_s, tier_s, label_s, color_s = calculate_quant_score(
        win_rate=80.0, profit_factor=3.5, net_pnl=250.0, total_trades=10
    )
    assert score_s >= 80.0
    assert tier_s == "TIER_S"
    assert "Tier S" in label_s

    # Tier A performer (Solid win rate and PF)
    score_a, tier_a, label_a, _ = calculate_quant_score(
        win_rate=60.0, profit_factor=2.1, net_pnl=60.0, total_trades=8
    )
    assert 68.0 <= score_a < 80.0
    assert tier_a == "TIER_A"

    # Tier B performer (Break-even or modest gains)
    score_b, tier_b, label_b, _ = calculate_quant_score(
        win_rate=50.0, profit_factor=1.1, net_pnl=5.0, total_trades=5
    )
    assert 50.0 <= score_b < 68.0
    assert tier_b == "TIER_B"

    # Tier C performer (Negative PnL, low win rate)
    score_c, tier_c, label_c, _ = calculate_quant_score(
        win_rate=20.0, profit_factor=0.3, net_pnl=-150.0, total_trades=6
    )
    assert score_c < 50.0
    assert tier_c == "TIER_C"


def test_compute_bot_leaderboard_empty_db(temp_db):
    """Test leaderboard computation on empty database."""
    res = compute_bot_leaderboard(account_id="all", db_path=temp_db)
    assert res["total_bots"] == 0
    assert res["fleet_total_trades"] == 0
    assert res["fleet_win_rate"] == 0.0
    assert res["fleet_total_pnl_usd"] == 0.0
    assert res["top_performer"] is None
    assert res["rankings"] == []


def test_compute_bot_leaderboard_with_data(temp_db):
    """Test leaderboard with multiple bots and positions."""
    conn = sqlite3.connect(str(temp_db))
    # Account fixtures
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    conn.execute("INSERT INTO accounts VALUES ('acc_live_1', 'live', 'Live 1', 1)")

    # Bot 1: Asian Range Judas Sweep (High Win Rate on XAUUSD)
    for i in range(4):
        conn.execute("""
            INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
            VALUES ('AsianRangeJudasSweepBot', 'XAUUSD', 'BUY', 0.05, 2500.0, 50.0, 'closed', '2026-09-01 10:00:00', '2026-09-01 08:00:00', 'acc_demo_1')
        """)
    # 1 Loss for Bot 1
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
        VALUES ('AsianRangeJudasSweepBot', 'XAUUSD', 'SELL', 0.05, 2510.0, -25.0, 'closed', '2026-09-02 10:00:00', '2026-09-02 08:00:00', 'acc_demo_1')
    """)

    # Bot 2: AiAgentBot TMS (Mixed results on EURUSD)
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
        VALUES ('AiAgentBot_TMS', 'EURUSD', 'BUY', 0.1, 1.0850, 20.0, 'closed', '2026-09-01 12:00:00', '2026-09-01 09:00:00', 'acc_demo_1')
    """)
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
        VALUES ('AiAgentBot_TMS', 'EURUSD', 'SELL', 0.1, 1.0820, -40.0, 'closed', '2026-09-02 12:00:00', '2026-09-02 09:00:00', 'acc_demo_1')
    """)

    # 1 Active open position for Bot 1
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, entry_time, account_id)
        VALUES ('AsianRangeJudasSweepBot', 'XAUUSD', 'BUY', 0.05, 2520.0, 10.0, 'open', '2026-09-05 08:00:00', 'acc_demo_1')
    """)

    conn.commit()
    conn.close()

    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db)
    assert res["total_bots"] == 2
    assert res["fleet_total_trades"] == 7  # 5 closed + 2 closed
    assert res["top_performer"] is not None
    assert res["top_performer"]["bot_id"] == "AsianRangeJudasSweepBot"
    assert res["top_performer"]["rank"] == 1
    assert res["top_performer"]["win_rate"] == 80.0  # 4 wins out of 5 closed trades
    assert res["top_performer"]["profit_factor"] == 8.0  # $200 / $25
    assert res["top_performer"]["closed_pnl_usd"] == 175.0  # 200 - 25
    assert res["top_performer"]["floating_pnl_usd"] == 10.0
    assert res["top_performer"]["total_pnl_usd"] == 185.0
    assert res["top_performer"]["tier_badge"] == "TIER_S"

    # Bot 2 checks
    bot2 = [r for r in res["rankings"] if r["bot_id"] == "AiAgentBot_TMS"][0]
    assert bot2["rank"] == 2
    assert bot2["total_trades"] == 2
    assert bot2["win_rate"] == 50.0
    assert bot2["closed_pnl_usd"] == -20.0


def test_api_leaderboard_endpoints():
    """Test FastAPI REST endpoints for leaderboard."""
    client = TestClient(app)

    # Test /api/leaderboard
    resp = client.get("/api/leaderboard?account_id=all")
    assert resp.status_code == 200
    data = resp.json()
    assert "rankings" in data
    assert "fleet_win_rate" in data
    assert "total_bots" in data

    # Test /api/dashboard/leaderboard alias
    resp_alias = client.get("/api/dashboard/leaderboard?account_id=demo")
    assert resp_alias.status_code == 200
    assert "rankings" in resp_alias.json()


def test_dashboard_page_renders_leaderboard():
    """Test that /demo/dashboard renders the leaderboard elements cleanly."""
    client = TestClient(app)
    resp = client.get("/demo/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "view-leaderboard" in html
    assert "cBot Performance &amp; Quant Ranking" in html
    assert "leaderboard-table-body" in html
    assert "Fleet Win Rate" in html
