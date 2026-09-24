"""
Tests for Bot Quantitative Performance Leaderboard & Ranking System.
"""

import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient

from app.leaderboard import calculate_quant_score, compute_bot_leaderboard, period_start
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

def test_leaderboard_account_type_filter(temp_db):
    """The ranking is scoped to the active mode and never mixes live with demo."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    conn.execute("INSERT INTO accounts VALUES ('acc_live_1', 'live', 'Live 1', 1)")
    conn.execute("INSERT INTO accounts VALUES ('acc_live_2', 'live', 'Live 2', 1)")

    def add(bot_id, account_id, pnl):
        conn.execute("""
            INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
            VALUES (?, 'XAUUSD', 'BUY', 0.05, 2500.0, ?, 'closed', '2026-09-01 10:00:00', '2026-09-01 08:00:00', ?)
        """, (bot_id, pnl, account_id))

    add("DemoBot", "acc_demo_1", 100.0)
    add("LiveBot", "acc_live_1", 30.0)
    add("LiveBot2", "acc_live_2", -10.0)
    conn.commit()
    conn.close()

    # account_type scopes the ranking to a single mode
    live = compute_bot_leaderboard(account_id="all", db_path=temp_db, account_type="live")
    assert live["account_type"] == "live"
    assert live["fleet_total_trades"] == 2
    assert {r["bot_id"] for r in live["rankings"]} == {"LiveBot", "LiveBot2"}
    assert live["fleet_total_pnl_usd"] == 20.0

    demo = compute_bot_leaderboard(account_id="all", db_path=temp_db, account_type="demo")
    assert demo["account_type"] == "demo"
    assert demo["fleet_total_trades"] == 1
    assert {r["bot_id"] for r in demo["rankings"]} == {"DemoBot"}

    # Without a type the ranking covers every configured account
    every = compute_bot_leaderboard(account_id="all", db_path=temp_db)
    assert every["account_type"] is None
    assert every["fleet_total_trades"] == 3

    # One account narrows inside its mode; "live"/"demo" stay valid shorthand
    one = compute_bot_leaderboard(account_id="acc_live_2", db_path=temp_db, account_type="live")
    assert {r["bot_id"] for r in one["rankings"]} == {"LiveBot2"}
    shorthand = compute_bot_leaderboard(account_id="live", db_path=temp_db)
    assert shorthand["account_type"] == "live"
    assert shorthand["fleet_total_trades"] == 2

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


def _utc_ago(**delta) -> str:
    """A positions-table timestamp ('YYYY-MM-DD HH:MM:SS', UTC) this long ago."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def _add_closed(conn, bot_id, pnl, volume=0.1, exit_time="2026-09-01 10:00:00",
                account_id="acc_demo_1", symbol="XAUUSD"):
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, exit_time, entry_time, account_id)
        VALUES (?, ?, 'BUY', ?, 2500.0, ?, 'closed', ?, ?, ?)
    """, (bot_id, symbol, volume, pnl, exit_time, exit_time, account_id))


def test_profit_factor_without_losses_is_unbounded(temp_db):
    """A bot that has only won gets an unbounded PF (None), not its gross profit in dollars."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for pnl in (0.2, 0.3, 0.3):  # $0.80 of wins on micro lots used to read as PF 0.80
        _add_closed(conn, "MicroBot", pnl, volume=0.01)
    conn.commit()
    conn.close()

    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db)
    bot = res["rankings"][0]
    assert bot["profit_factor"] is None
    assert bot["composite_score"] == calculate_quant_score(100.0, None, 0.8, 3)[0]
    # Scored as the top of the PF curve, the same as any PF >= 3
    assert calculate_quant_score(100.0, None, 0.8, 3) == calculate_quant_score(100.0, 3.0, 0.8, 3)
    assert res["lot_neutral"]["rankings"][0]["profit_factor"] is None
    assert res["lot_neutral"]["rankings"][0]["payoff_ratio"] is None


def test_lot_neutral_ranking_ignores_lot_size(temp_db):
    """The same trades on 0.01 and 1.00 lots score the same lot-neutral, but not in USD."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    per_lot_results = [300.0, -150.0, 200.0, -100.0, 250.0, 300.0]
    for i, per_lot in enumerate(per_lot_results):
        ts = f"2026-09-0{i + 1} 10:00:00"
        _add_closed(conn, "MicroLotBot", per_lot * 0.01, volume=0.01, exit_time=ts)
        _add_closed(conn, "BigLotBot", per_lot * 1.0, volume=1.0, exit_time=ts)
    conn.commit()
    conn.close()

    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db)
    usd = {r["bot_id"]: r for r in res["rankings"]}
    lot = {r["bot_id"]: r for r in res["lot_neutral"]["rankings"]}

    # USD: the big-lot bot wins on Net PnL alone
    assert usd["BigLotBot"]["composite_score"] > usd["MicroLotBot"]["composite_score"]
    assert usd["BigLotBot"]["rank"] == 1

    # Lot-neutral: identical figures and score
    keys = ("total_trades", "win_rate", "profit_factor", "payoff_ratio", "net_per_lot",
            "max_dd_per_lot", "return_dd", "composite_score", "tier_badge")
    assert {k: lot["BigLotBot"][k] for k in keys} == {k: lot["MicroLotBot"][k] for k in keys}


def test_lot_neutral_stats_values(temp_db):
    """Per-lot PF, payoff, net, max drawdown and Return/DD on a known sequence."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    # Per lot, in exit order: +100, -50, -50, +200  -> equity 100, 50, 0, 200
    # (inserted out of order: the drawdown must follow exit_time, not insertion order)
    _add_closed(conn, "SeqBot", 40.0, volume=0.2, exit_time="2026-09-04 10:00:00")   # +200/lot
    _add_closed(conn, "SeqBot", 10.0, volume=0.1, exit_time="2026-09-01 10:00:00")   # +100/lot
    _add_closed(conn, "SeqBot", -25.0, volume=0.5, exit_time="2026-09-02 10:00:00")  # -50/lot
    _add_closed(conn, "SeqBot", -5.0, volume=0.1, exit_time="2026-09-03 10:00:00")   # -50/lot
    conn.commit()
    conn.close()

    bot = compute_bot_leaderboard(account_id="demo", db_path=temp_db)["lot_neutral"]["rankings"][0]
    assert bot["total_trades"] == 4
    assert bot["win_rate"] == 50.0
    assert bot["profit_factor"] == 3.0     # 300 / 100
    assert bot["payoff_ratio"] == 3.0      # avg win 150 / avg loss 50
    assert bot["net_per_lot"] == 200.0
    assert bot["max_dd_per_lot"] == 100.0  # peak 100 -> trough 0
    assert bot["return_dd"] == 2.0


def test_leaderboard_period_filter(temp_db):
    """A period keeps only the trades closed inside its rolling window; open positions always count."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for ago in (dict(hours=2), dict(days=3), dict(days=20), dict(days=100), dict(days=300), dict(days=800)):
        _add_closed(conn, "PeriodBot", 10.0, exit_time=_utc_ago(**ago))
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, entry_time, account_id)
        VALUES ('PeriodBot', 'XAUUSD', 'BUY', 0.1, 2500.0, 5.0, 'open', ?, 'acc_demo_1')
    """, (_utc_ago(days=900),))
    conn.commit()
    conn.close()

    expected = {"1d": 1, "1w": 2, "1m": 3, "6m": 4, "1y": 5, "all": 6}
    for period, trades in expected.items():
        res = compute_bot_leaderboard(account_id="demo", db_path=temp_db, period=period)
        assert res["period"] == period
        assert res["fleet_total_trades"] == trades, period
        assert res["lot_neutral"]["rankings"][0]["total_trades"] == trades, period
        assert res["rankings"][0]["floating_pnl_usd"] == 5.0, period
    assert compute_bot_leaderboard(account_id="demo", db_path=temp_db, period="all")["period_start"] is None

    # An unknown period falls back to all time
    fallback = compute_bot_leaderboard(account_id="demo", db_path=temp_db, period="bogus")
    assert fallback["period"] == "all"
    assert fallback["fleet_total_trades"] == 6


def test_period_start_is_utc_window():
    now = datetime(2026, 9, 24, 12, 30, 0, tzinfo=timezone.utc)
    assert period_start("1d", now) == "2026-09-23 12:30:00"
    assert period_start("1w", now) == "2026-09-17 12:30:00"
    assert period_start("all", now) is None


def test_bots_without_closed_trades_rank_last(temp_db):
    """A bot with nothing closed in the window is unrated and sits below even a losing bot."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    conn.execute("INSERT INTO cbot_configs (name, run_command) VALUES ('IdleBot', 'run')")
    _add_closed(conn, "LosingBot", -80.0, exit_time=_utc_ago(days=30))
    _add_closed(conn, "LosingBot", -60.0, exit_time=_utc_ago(days=30))
    conn.commit()
    conn.close()

    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db)
    for rankings, top in ((res["rankings"], res["top_performer"]),
                          (res["lot_neutral"]["rankings"], res["lot_neutral"]["top_performer"])):
        assert [r["bot_id"] for r in rankings] == ["LosingBot", "IdleBot"]
        assert rankings[1]["tier_badge"] == "UNRATED"
        assert rankings[0]["tier_badge"] == "TIER_C"
        assert top["bot_id"] == "LosingBot"

    # Nothing closed at all: no top performer
    idle_only = compute_bot_leaderboard(account_id="demo", db_path=temp_db, period="1d")
    assert idle_only["top_performer"] is None
    assert idle_only["lot_neutral"]["top_performer"] is None


def test_api_leaderboard_period_and_lot_neutral():
    client = TestClient(app)
    resp = client.get("/api/leaderboard?account_id=all&period=1w")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "1w"
    assert data["period_start"]
    assert "rankings" in data["lot_neutral"]
    assert "top_performer" in data["lot_neutral"]


def test_dashboard_page_renders_leaderboard_tabs():
    client = TestClient(app)
    html = client.get("/demo/dashboard").text
    assert 'id="lb-tab-group"' in html
    assert 'data-lb-tab="lot"' in html
    assert 'id="lb-period-group"' in html
    for period in ("1d", "1w", "1m", "6m", "1y", "all"):
        assert f'data-lb-period="{period}"' in html
    assert 'id="leaderboard-lot-table-body"' in html
