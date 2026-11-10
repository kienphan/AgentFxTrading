"""
Tests for Bot Quantitative Performance Leaderboard & Ranking System.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient

import app.leaderboard as leaderboard_module
from app.leaderboard import (FULL_SAMPLE_TRADES, MAX_CURVE_POINTS, InvalidDateRange,
                             calculate_quant_score, compute_bot_leaderboard, compute_bot_pnl_curves,
                             date_range_bounds, period_start)
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
    # Every column of both rankings is a sort target (11 USD + 10 lot-neutral headers)
    assert html.count('data-lb-sort="') == 21
    assert 'data-lb-sort="total_pnl_usd"' in html and 'data-lb-sort="payoff_ratio"' in html


def _utc_ago(**delta) -> str:
    """A positions-table timestamp ('YYYY-MM-DD HH:MM:SS', UTC) this long ago."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def _add_closed(conn, bot_id, pnl, volume=0.1, exit_time="2026-09-01 10:00:00",
                account_id="acc_demo_1", symbol="XAUUSD", sl_pips=None):
    conn.execute("""
        INSERT INTO positions (bot_id, symbol, side, volume, entry_price, sl_pips, pnl, status, exit_time, entry_time, account_id)
        VALUES (?, ?, 'BUY', ?, 2500.0, ?, ?, 'closed', ?, ?, ?)
    """, (bot_id, symbol, volume, sl_pips, pnl, exit_time, exit_time, account_id))


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
    keys = ("total_trades", "win_rate", "profit_factor", "payoff_ratio", "net_units",
            "max_dd_units", "return_dd", "composite_score", "tier_badge")
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
    assert bot["basis"] == "lot"          # no stop distance on record: per lot
    assert bot["net_units"] == 200.0
    assert bot["max_dd_units"] == 100.0  # peak 100 -> trough 0
    assert bot["return_dd"] == 2.0


def test_leaderboard_period_filter(temp_db):
    """A period keeps the trades closed, and the positions opened, inside its rolling window."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for ago in (dict(hours=2), dict(days=3), dict(days=20), dict(days=100), dict(days=300), dict(days=800)):
        _add_closed(conn, "PeriodBot", 10.0, exit_time=_utc_ago(**ago))
    for pnl, ago in ((5.0, dict(days=900)), (3.0, dict(hours=1))):
        conn.execute("""
            INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, entry_time, account_id)
            VALUES ('PeriodBot', 'XAUUSD', 'BUY', 0.1, 2500.0, ?, 'open', ?, 'acc_demo_1')
        """, (pnl, _utc_ago(**ago)))
    conn.commit()
    conn.close()

    expected = {"1d": 1, "1w": 2, "1m": 3, "6m": 4, "1y": 5, "all": 6}
    for period, trades in expected.items():
        res = compute_bot_leaderboard(account_id="demo", db_path=temp_db, period=period)
        assert res["period"] == period
        assert res["fleet_total_trades"] == trades, period
        assert res["lot_neutral"]["rankings"][0]["total_trades"] == trades, period
        # The position opened 900 days ago built its floating P&L outside every window but "all"
        bot = res["rankings"][0]
        assert bot["floating_pnl_usd"] == (8.0 if period == "all" else 3.0), period
        assert bot["open_positions_count"] == (2 if period == "all" else 1), period
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


def test_date_range_bounds_are_vietnam_days():
    """A custom range covers whole Vietnam-time days (UTC+7), both included, as UTC bounds."""
    now = datetime(2026, 9, 25, 3, 0, 0, tzinfo=timezone.utc)  # 10:00 on 25/09 in Vietnam
    # 00:00 on 01/09 in Vietnam is 17:00 UTC the day before; the end is the midnight after 20/09
    assert date_range_bounds("2026-09-01", "2026-09-20", now) == \
        ("2026-08-31 17:00:00", "2026-09-20 17:00:00", False)
    # A range down to today still runs, so open positions count
    assert date_range_bounds("2026-09-25", "2026-09-25", now) == \
        ("2026-09-24 17:00:00", "2026-09-25 17:00:00", True)
    # 23:30 UTC on 24/09 is already 25/09 in Vietnam: a range ending on 24/09 is over
    late = datetime(2026, 9, 24, 23, 30, 0, tzinfo=timezone.utc)
    assert date_range_bounds(None, "2026-09-24", late) == (None, "2026-09-24 17:00:00", False)
    # A missing day leaves that side open
    assert date_range_bounds("2026-09-01", None, now) == ("2026-08-31 17:00:00", None, True)
    assert date_range_bounds("", "", now) == (None, None, True)

    for bad in (("2026-09-21", "2026-09-20"), ("2026-13-01", None), ("01/09/2026", None),
                (None, "yesterday"), (None, "9999-12-31")):
        with pytest.raises(InvalidDateRange):
            date_range_bounds(*bad, now)


def test_leaderboard_custom_range(temp_db):
    """A custom range keeps the trades closed on its Vietnam-time days; open positions only
    count while the range reaches today."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    # 01/03/2024 to 20/03/2024 in Vietnam is 29/02 17:00 to 20/03 17:00 UTC
    for exit_time in ("2024-02-29 16:59:59", "2024-02-29 17:00:00", "2024-03-10 12:00:00",
                      "2024-03-20 16:59:59", "2024-03-20 17:00:00"):
        _add_closed(conn, "RangeBot", 10.0, exit_time=exit_time)
    # A closed row without an exit time falls back to its entry time
    for entry_time in ("2024-03-05 00:00:00", "2024-04-05 00:00:00"):
        conn.execute("""
            INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, entry_time, account_id)
            VALUES ('RangeBot', 'XAUUSD', 'BUY', 0.1, 2500.0, 10.0, 'closed', ?, 'acc_demo_1')
        """, (entry_time,))
    for pnl, entry_time in ((4.0, "2024-03-10 00:00:00"), (3.0, _utc_ago(hours=1)), (5.0, _utc_ago(days=10))):
        conn.execute("""
            INSERT INTO positions (bot_id, symbol, side, volume, entry_price, pnl, status, entry_time, account_id)
            VALUES ('RangeBot', 'XAUUSD', 'BUY', 0.1, 2500.0, ?, 'open', ?, 'acc_demo_1')
        """, (pnl, entry_time))
    conn.commit()
    conn.close()

    past = compute_bot_leaderboard(account_id="demo", db_path=temp_db,
                                   date_from="2024-03-01", date_to="2024-03-20")
    assert past["period"] == "custom"
    assert past["period_start"] == "2024-02-29 17:00:00"
    assert past["period_end"] == "2024-03-20 17:00:00"
    assert past["fleet_total_trades"] == 4
    assert past["lot_neutral"]["rankings"][0]["total_trades"] == 4
    # The range is over: even the position opened inside it stays out, its P&L is today's
    assert past["includes_open_positions"] is False
    bot = past["rankings"][0]
    assert bot["open_positions_count"] == 0
    assert bot["floating_pnl_usd"] == 0.0
    assert bot["total_pnl_usd"] == 40.0

    # Down to today: the positions opened inside the range count, the older one does not
    vn_today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    recent = compute_bot_leaderboard(account_id="demo", db_path=temp_db,
                                     date_from=(vn_today - timedelta(days=3)).isoformat(),
                                     date_to=vn_today.isoformat())
    assert recent["includes_open_positions"] is True
    assert recent["fleet_total_trades"] == 0
    assert recent["rankings"][0]["open_positions_count"] == 1
    assert recent["rankings"][0]["floating_pnl_usd"] == 3.0

    # Open-ended: from a day on, up to now
    since = compute_bot_leaderboard(account_id="demo", db_path=temp_db, date_from="2024-03-15")
    assert since["period"] == "custom" and since["period_end"] is None
    assert since["fleet_total_trades"] == 3  # both sides of midnight 20/03 (VN) and the 05/04 fallback
    assert since["rankings"][0]["open_positions_count"] == 2

    with pytest.raises(InvalidDateRange):
        compute_bot_leaderboard(account_id="demo", db_path=temp_db,
                                date_from="2024-03-20", date_to="2024-03-01")


def test_api_leaderboard_custom_range(temp_db, monkeypatch):
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RangeBot", 10.0, exit_time="2024-03-10 12:00:00")
    _add_closed(conn, "RangeBot", 10.0, exit_time="2024-05-10 12:00:00")
    conn.commit()
    conn.close()
    _use_leaderboard_db(monkeypatch, temp_db)

    client = TestClient(app)
    resp = client.get("/api/leaderboard?account_id=all&account_type=demo&period=custom"
                      "&date_from=2024-03-01&date_to=2024-03-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "custom"
    assert data["fleet_total_trades"] == 1
    assert data["includes_open_positions"] is False

    for query in ("date_from=2024-03-31&date_to=2024-03-01", "date_from=bogus", "date_to=2024-02-30"):
        resp = client.get(f"/api/leaderboard?account_id=all&account_type=demo&period=custom&{query}")
        assert resp.status_code == 400, query


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


def _use_leaderboard_db(monkeypatch, db_file):
    """Point the leaderboard (API and page) at a test database.

    The running-bots filter is switched off too: the temp DB's bot ids have no containers, so a
    live Docker lookup would hide every test bot. Tests that exercise the filter set it back.
    """
    import app.dashboard as dashboard_module
    real = leaderboard_module.get_db_connection
    monkeypatch.setattr(leaderboard_module, "get_db_connection", lambda db_path=None: real(db_file))
    monkeypatch.setattr(dashboard_module, "_running_bot_ids", lambda: None)


def test_api_leaderboard_period_and_lot_neutral(temp_db, monkeypatch):
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for pnl in (20.0, 30.0):
        _add_closed(conn, "WinnerBot", pnl, sl_pips=25.0, exit_time=_utc_ago(days=2))
    conn.commit()
    conn.close()
    _use_leaderboard_db(monkeypatch, temp_db)

    client = TestClient(app)
    resp = client.get("/api/leaderboard?account_id=all&account_type=demo&period=1w")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "1w"
    assert data["period_start"]
    assert data["full_sample_trades"] == FULL_SAMPLE_TRADES
    usd, lot = data["rankings"][0], data["lot_neutral"]["rankings"][0]
    assert usd["profit_factor"] is None          # unbounded PF travels as null, never as a number
    assert lot["profit_factor"] is None and lot["payoff_ratio"] is None
    assert lot["basis"] == "risk"
    assert data["lot_neutral"]["top_performer"]["bot_id"] == "WinnerBot"


def test_leaderboard_ranks_only_bots_the_caller_marks_running(temp_db):
    """A stopped bot disappears from both rankings, the fleet totals and the top performer: it
    keeps its trades and its cbot_configs row, so only the caller's running set can hide it."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RunningBot", 30.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "StoppedBot", 500.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "manual", 40.0, exit_time=_utc_ago(days=1))
    conn.commit()
    conn.close()

    # IdleRunning has no trade but its container is on, so it keeps an UNRATED row; manual has
    # no container at all but still traded, so it stays for the manual-trading statistics
    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db,
                                  bot_ids={"RunningBot", "IdleRunning"})
    for rankings in (res["rankings"], res["lot_neutral"]["rankings"]):
        assert {r["bot_id"] for r in rankings} == {"RunningBot", "manual", "IdleRunning"}
        assert rankings[-1]["bot_id"] == "IdleRunning" and rankings[-1]["total_trades"] == 0
    assert res["fleet_total_trades"] == 2
    assert res["fleet_total_pnl_usd"] == 70.0
    assert res["top_performer"]["bot_id"] == "manual"

    # Nothing running and nothing manual: an empty leaderboard, not every bot that ever traded
    conn = sqlite3.connect(str(temp_db))
    conn.execute("DELETE FROM positions WHERE bot_id = 'manual'")
    conn.commit()
    conn.close()
    empty = compute_bot_leaderboard(account_id="demo", db_path=temp_db, bot_ids=set())
    assert empty["rankings"] == [] and empty["lot_neutral"]["rankings"] == []
    assert empty["top_performer"] is None and empty["fleet_total_pnl_usd"] == 0.0


def test_bot_pnl_curves_keep_only_running_bots(temp_db):
    """The chart follows the table: a stopped bot's line leaves the payload, and the trade
    count the panel reports counts only the plotted bots."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RunningBot", 20.0, exit_time=_utc_ago(days=2))
    _add_closed(conn, "RunningBot", 5.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "StoppedBot", -9.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "manual", 7.0, exit_time=_utc_ago(days=1))
    conn.commit()
    conn.close()

    res = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="1w",
                                 bot_ids={"RunningBot"})
    # StoppedBot is off; manual stays despite having no container
    assert {s["bot_id"]: s["net"] for s in res["series"]} == {"RunningBot": 25.0, "manual": 7.0}
    assert res["total_trades"] == 3 and res["total_bots"] == 2


def test_bot_pnl_curves_always_show_manual_for_comparison(temp_db):
    """A window with no manual trade keeps a Manual line at $0, on the same daily grid as the
    bots; the unfiltered call (no running filter) does not invent one."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RunningBot", 20.0, exit_time=_utc_ago(days=2))
    conn.commit()
    conn.close()

    res = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="1w",
                                 bot_ids={"RunningBot"})
    by_id = {s["bot_id"]: s for s in res["series"]}
    assert set(by_id) == {"RunningBot", "manual"}
    manual, bot = by_id["manual"], by_id["RunningBot"]
    assert manual["total_trades"] == 0 and manual["net"] == 0.0
    assert [p["t"] for p in manual["points"]] == [p["t"] for p in bot["points"]]
    assert all(p["v"] == 0.0 for p in manual["points"])

    plain = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="1w")
    assert [s["bot_id"] for s in plain["series"]] == ["RunningBot"]


def test_leaderboard_api_hides_bots_that_are_not_running(temp_db, monkeypatch):
    """Both leaderboard endpoints take the running set, so the table and its chart agree."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RunningBot", 20.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "StoppedBot", 90.0, exit_time=_utc_ago(days=1))
    _add_closed(conn, "manual", 11.0, exit_time=_utc_ago(days=1))
    conn.commit()
    conn.close()
    _use_leaderboard_db(monkeypatch, temp_db)
    import app.dashboard as dashboard_module
    monkeypatch.setattr(dashboard_module, "_running_bot_ids", lambda: {"RunningBot"})

    client = TestClient(app)
    ranking = client.get("/api/leaderboard?account_id=all&account_type=demo&period=1w").json()
    assert [r["bot_id"] for r in ranking["rankings"]] == ["RunningBot", "manual"]
    assert ranking["fleet_total_pnl_usd"] == 31.0
    curve = client.get("/api/leaderboard/pnl-curves?account_id=all&account_type=demo"
                       "&period=1w").json()
    assert {s["bot_id"] for s in curve["series"]} == {"RunningBot", "manual"}


def test_running_bot_ids_map_containers_to_bot_ids(monkeypatch):
    """Only running containers count, and the id is the container's --BotId (not its name);
    without Docker the filter says "unknown" so the full table still renders."""
    import app.dashboard as dashboard_module

    class FakeDocker:
        is_available = True

        @staticmethod
        def running_container_names():
            return {"cbot-running"}

    class FakePortfolio:
        @staticmethod
        def get_cbot_configs():
            return [
                {"name": "cbot-running", "run_command": "ctrader-cli --BotId=orb_xauusd --x"},
                {"name": "cbot-stopped", "run_command": "ctrader-cli --BotId=orb_gbpjpy --x"},
            ]

    monkeypatch.setattr(dashboard_module, "docker_manager", FakeDocker())
    monkeypatch.setattr(dashboard_module, "get_portfolio_manager", lambda: FakePortfolio())
    assert dashboard_module._running_bot_ids() == {"orb_xauusd"}

    class NoDocker:
        is_available = False

    monkeypatch.setattr(dashboard_module, "docker_manager", NoDocker())
    assert dashboard_module._running_bot_ids() is None


def test_dashboard_page_embeds_leaderboard_escaped(temp_db, monkeypatch):
    """The page hands its ranking to the table renderer as JSON; a bot_id from a bot's report
    must not come out as markup."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    # Inside the page's default 7-day window
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _add_closed(conn, "<img src=x onerror=alert(1)>", 12.0, exit_time=recent)
    conn.commit()
    conn.close()
    _use_leaderboard_db(monkeypatch, temp_db)

    html = TestClient(app).get("/demo/dashboard").text
    assert "const lbInitial = {" in html
    assert '"period": "1w"' in html
    assert "<img src=x onerror" not in html.lower()
    assert "\\u003cimg src=x onerror" in html


def test_dashboard_page_renders_leaderboard_tabs():
    client = TestClient(app)
    html = client.get("/demo/dashboard").text
    assert 'id="lb-tab-group"' in html
    assert 'data-lb-tab="lot"' in html
    # The period picker: one button, its choices in a popover
    assert 'id="lb-period-btn"' in html and 'aria-controls="lb-period-pop"' in html
    assert 'id="lb-period-pop"' in html
    for period in ("1d", "1w", "1m", "6m", "1y", "all",
                   "today", "yesterday", "thisweek", "lastweek", "thismonth", "lastmonth"):
        assert f'data-lb-period="{period}"' in html
    assert 'id="lb-date-from" type="date"' in html
    assert 'id="lb-date-to" type="date"' in html
    assert 'id="lb-date-apply"' in html
    assert 'id="leaderboard-lot-table-body"' in html


DASHBOARD = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"

# The period picker's presets, run in node on a pinned clock: the days each preset covers, in
# Vietnam time whatever the zone node runs in
PRESET_HARNESS = """
const LB_CALENDAR = {today: 1, yesterday: 1, thisweek: 1, lastweek: 1, thismonth: 1, lastmonth: 1};
let lbDateFrom = '2026-09-10', lbDateTo = '';
%(functions)s
const out = {};
for (const [name, utc] of Object.entries(%(clocks)s)) {
    Date.now = () => Date.parse(utc);
    out[name] = {};
    for (const p of [...Object.keys(LB_CALENDAR), 'custom', '1w']) {
        const range = lbRangeFor(p);
        out[name][p] = range && [range.from, range.to, lbRangeText(range)];
    }
}
console.log(JSON.stringify(out));
"""


def _dashboard_function(src: str, name: str) -> str:
    """Source of `function name(...) {...}` in the dashboard script, matched on braces."""
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_period_picker_calendar_presets(tmp_path):
    html = DASHBOARD.read_text(encoding="utf-8")
    functions = [_dashboard_function(html, name) for name in
                 ("lbHas", "lbVnToday", "lbAddDays", "lbIsoDay", "lbRangeFor", "lbShortDay", "lbRangeText")]
    clocks = {
        "fri": "2026-09-25T03:00:00Z",       # Friday 25/09, 10:00 in Vietnam
        "vn_oct": "2026-09-30T17:30:00Z",    # still 30/09 in UTC, already Thursday 01/10 in Vietnam
        "new_year": "2027-01-03T02:00:00Z",  # Sunday 03/01/2027 in Vietnam
    }
    script = tmp_path / "presets.js"
    script.write_text(PRESET_HARNESS % {"functions": "\n".join(functions), "clocks": json.dumps(clocks)},
                      encoding="utf-8")
    run = subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "TZ": "America/New_York"})
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    fri = out["fri"]
    assert fri["today"] == ["2026-09-25", "2026-09-25", "25/09"]
    assert fri["yesterday"] == ["2026-09-24", "2026-09-24", "24/09"]
    assert fri["thisweek"] == ["2026-09-21", "2026-09-25", "21/09 – 25/09"]  # weeks start on Monday
    assert fri["lastweek"] == ["2026-09-14", "2026-09-20", "14/09 – 20/09"]
    assert fri["thismonth"] == ["2026-09-01", "2026-09-25", "01/09 – 25/09"]
    assert fri["lastmonth"] == ["2026-08-01", "2026-08-31", "01/08 – 31/08"]
    assert fri["custom"] == ["2026-09-10", "", "from 10/09"]
    assert fri["1w"] is None  # a rolling window has no days

    oct1 = out["vn_oct"]
    assert oct1["today"][:2] == ["2026-10-01", "2026-10-01"]
    assert oct1["thismonth"][:2] == ["2026-10-01", "2026-10-01"]
    assert oct1["lastmonth"][:2] == ["2026-09-01", "2026-09-30"]
    assert oct1["thisweek"][:2] == ["2026-09-28", "2026-10-01"]

    ny = out["new_year"]
    assert ny["thisweek"][:2] == ["2026-12-28", "2027-01-03"]  # Sunday closes the week
    assert ny["lastmonth"] == ["2026-12-01", "2026-12-31", "01/12/2026 – 31/12/2026"]
    assert ny["custom"][2] == "from 10/09/2026"  # another year's day shows its year


def test_small_sample_is_pulled_toward_neutral(temp_db):
    """Three lucky winners (unbounded PF, no drawdown) must not outrank a long, profitable record."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for i in range(3):
        _add_closed(conn, "LuckyBot", 0.1, volume=0.01, sl_pips=20.0, exit_time=f"2026-09-0{i + 1} 10:00:00")
    # 40 trades, 60% winners at 1.5R against 1R losers
    for i in range(40):
        pnl = 30.0 if i % 5 in (0, 1, 3) else -20.0
        _add_closed(conn, "SteadyBot", pnl, volume=0.1, sl_pips=20.0,
                    exit_time=f"2026-08-{1 + i // 2:02d} {10 + i % 2}:00:00")
    conn.commit()
    conn.close()

    res = compute_bot_leaderboard(account_id="demo", db_path=temp_db)
    for rankings, top in ((res["rankings"], res["top_performer"]),
                          (res["lot_neutral"]["rankings"], res["lot_neutral"]["top_performer"])):
        by_id = {r["bot_id"]: r for r in rankings}
        assert top["bot_id"] == "SteadyBot"
        assert by_id["LuckyBot"]["tier_badge"] not in ("TIER_S", "TIER_A")

    # The weighting fades out at FULL_SAMPLE_TRADES: from there the score is the plain weighted sum
    def raw(n):  # WR 60%, PF 3.0, +$500, n trades
        return 0.30 * 75.0 + 0.30 * 100.0 + 0.20 * 100.0 + 0.20 * min(100.0, 40.0 + 3.0 * n)
    full, half = FULL_SAMPLE_TRADES, FULL_SAMPLE_TRADES // 2
    assert calculate_quant_score(60.0, 3.0, 500.0, full)[0] == pytest.approx(raw(full), abs=0.05)
    assert calculate_quant_score(60.0, 3.0, 500.0, half)[0] == pytest.approx(50 + (raw(half) - 50) / 2, abs=0.05)


def test_lot_neutral_takes_trades_over_their_risk(temp_db):
    """Risk-sized trades: a tight-stop winner on big lots and a wide-stop loser on small lots.

    Per lot the loser weighs four times the winner (PF 0.5); over the risk each took they are +2R
    and -1R (PF 2.0), which is what the bot actually did with its money.
    """
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    # $100 risked each time: 1.0 lot on a 10-pip stop, 0.25 lot on a 40-pip stop ($10/pip/lot)
    _add_closed(conn, "RiskBot", 200.0, volume=1.0, sl_pips=10.0, exit_time="2026-09-01 10:00:00")
    _add_closed(conn, "RiskBot", -100.0, volume=0.25, sl_pips=40.0, exit_time="2026-09-02 10:00:00")
    conn.commit()
    conn.close()

    bot = compute_bot_leaderboard(account_id="demo", db_path=temp_db)["lot_neutral"]["rankings"][0]
    assert bot["basis"] == "risk"
    assert bot["profit_factor"] == 2.0
    assert bot["payoff_ratio"] == 2.0
    assert bot["return_dd"] == 1.0  # +2R then -1R: net 1R over a 1R drawdown
    # One trade without a stop distance: the whole bot falls back to per lot, never mixing units
    conn = sqlite3.connect(str(temp_db))
    _add_closed(conn, "RiskBot", 50.0, volume=0.5, sl_pips=None, exit_time="2026-09-03 10:00:00")
    conn.commit()
    conn.close()
    bot = compute_bot_leaderboard(account_id="demo", db_path=temp_db)["lot_neutral"]["rankings"][0]
    assert bot["basis"] == "lot"
    assert bot["profit_factor"] == round((200.0 + 100.0) / 400.0, 2)


def test_same_second_exits_walk_in_id_order(temp_db):
    """A close-all stamps several trades with one exit time: the equity curve follows the row id,
    so the drawdown does not change from one refresh to the next."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "BasketBot", 100.0, volume=1.0, exit_time="2026-09-01 10:00:00")
    _add_closed(conn, "BasketBot", -30.0, volume=1.0, exit_time="2026-09-02 10:00:00")
    for pnl in (-50.0, 100.0):  # ids 3, 4: same second
        _add_closed(conn, "BasketBot", pnl, volume=1.0, exit_time="2026-09-03 10:00:00")
    conn.commit()
    conn.close()

    bot = compute_bot_leaderboard(account_id="demo", db_path=temp_db)["lot_neutral"]["rankings"][0]
    # 100 -> 70 -> 20 -> 120: the -50 (lower id) comes first, a drawdown of 80 from the peak of 100
    assert bot["max_dd_units"] == 80.0
    assert bot["return_dd"] == 1.5

    # Whatever order the DB hands the tied rows back in
    tied = [{"id": 4, "exit_time": "2026-09-03 10:00:00"}, {"id": 3, "exit_time": "2026-09-03 10:00:00"},
            {"id": 9, "exit_time": "2026-09-01 10:00:00"}]
    assert [t["id"] for t in leaderboard_module._oldest_first(tied)] == [9, 3, 4]


VN = timezone(timedelta(hours=7))


def _vn_day_close_ms(year, month, day):
    """Epoch ms of the Vietnam-time midnight that closes the given VN day."""
    return int(datetime(year, month, day, tzinfo=VN).timestamp() * 1000) + 86_400_000


def _curve_at(points, year, month, day):
    """The curve's cumulative value at the close of a Vietnam-time day."""
    at = _vn_day_close_ms(year, month, day)
    return next(p["v"] for p in points if p["t"] == at)


def test_bot_pnl_curves_step_once_per_day(temp_db):
    """Each bot gets one point per Vietnam-time day, so a day's trades add up to a single step,
    and the rolling total still ends on the ranking's Closed PnL."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    # Out of order, one close-all tie, and a second bot so the series are per bot
    _add_closed(conn, "CurveBot", 30.0, exit_time="2026-09-03 10:00:00")
    _add_closed(conn, "CurveBot", 10.0, exit_time="2026-09-01 10:00:00")
    _add_closed(conn, "CurveBot", -5.0, exit_time="2026-09-02 10:00:00")
    _add_closed(conn, "OtherBot", 7.0, exit_time="2026-09-01 11:00:00")
    conn.commit()
    conn.close()

    res = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="all")
    assert res["period"] == "all"
    assert res["granularity"] == "day"
    assert res["unit"] == "usd" and res["basis"] == "usd"
    assert res["total_trades"] == 4 and res["total_bots"] == 2
    by_id = {s["bot_id"]: s for s in res["series"]}
    points = by_id["CurveBot"]["points"]
    # 0 at the midnight before the first trade, then one close per day (31/08 17:00 UTC is
    # midnight on 01/09 in Vietnam)
    assert points[0] == {"t": leaderboard_module._epoch_ms("2026-08-31 17:00:00"), "v": 0.0}
    assert _curve_at(points, 2026, 9, 1) == 10.0
    assert _curve_at(points, 2026, 9, 2) == 5.0
    assert _curve_at(points, 2026, 9, 3) == 35.0
    assert _curve_at(by_id["OtherBot"]["points"], 2026, 9, 1) == 7.0
    assert by_id["CurveBot"]["net"] == 35.0
    # The best final result is first, matching the ranking table
    assert [s["bot_id"] for s in res["series"]] == ["CurveBot", "OtherBot"]
    # One close per day from 01/09 to today, plus the baseline before the first day
    vn_today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    assert len(points) == (vn_today - datetime(2026, 9, 1).date()).days + 2
    # Interior points sit on a Vietnam-time midnight; the last is clamped to now
    for point in points[1:-1]:
        assert (point["t"] + 7 * 3_600_000) % 86_400_000 == 0, point
    stamps = [p["t"] for p in points]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    # The curve's final value is the ranking's Closed PnL for that bot
    closed = {r["bot_id"]: r["closed_pnl_usd"]
              for r in compute_bot_leaderboard(account_id="demo", db_path=temp_db)["rankings"]}
    assert {b_id: s["net"] for b_id, s in by_id.items()} == closed


def test_bot_pnl_curves_follow_the_shared_period_and_account_filters(temp_db):
    """The curves take the ranking's window, mode and account scope, and start at 0 at the
    window's start so the line reads from the left edge of the period."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    conn.execute("INSERT INTO accounts VALUES ('acc_live_1', 'live', 'Live 1', 1)")
    for ago in (dict(hours=2), dict(days=3), dict(days=20)):
        _add_closed(conn, "PeriodBot", 10.0, exit_time=_utc_ago(**ago))
    _add_closed(conn, "LiveOnlyBot", 50.0, exit_time=_utc_ago(hours=3), account_id="acc_live_1")
    conn.commit()
    conn.close()

    res = compute_bot_pnl_curves(account_id="all", db_path=temp_db, account_type="demo", period="1w")
    assert res["total_trades"] == 2
    bot = res["series"][0]
    assert bot["bot_id"] == "PeriodBot"
    assert bot["net"] == 20.0
    # Baseline at the window's UTC start, then one close per Vietnam-time day up to today
    assert bot["points"][0] == {"t": leaderboard_module._epoch_ms(res["period_start"]), "v": 0.0}
    assert len(bot["points"]) == 9  # the window's 8 VN days, plus the baseline
    assert bot["points"][-1]["v"] == 20.0

    # One account narrows inside its mode; the default window is the ranking's 1w
    live = compute_bot_pnl_curves(account_id="acc_live_1", db_path=temp_db, account_type="live")
    assert [s["bot_id"] for s in live["series"]] == ["LiveOnlyBot"]
    assert live["period_start"] and live["period_end"] is None

    with pytest.raises(InvalidDateRange):
        compute_bot_pnl_curves(account_id="all", db_path=temp_db,
                               date_from="2024-03-20", date_to="2024-03-01")


def test_bot_pnl_curves_lot_basis_takes_the_position_size_out(temp_db):
    """On the lot tab the line walks size-neutral results: risk units when every trade has a
    stop distance, per lot when one does not, never a mix of both."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "RiskBot", 200.0, volume=1.0, sl_pips=10.0, exit_time="2026-09-01 10:00:00")
    _add_closed(conn, "RiskBot", -100.0, volume=0.25, sl_pips=40.0, exit_time="2026-09-02 10:00:00")
    conn.commit()
    conn.close()

    res = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="all", basis="lot")
    assert res["unit"] == "risk"
    bot = res["series"][0]
    assert bot["basis"] == "risk"
    # P&L over lots x stop pips: +200/(1x10) = +20, then -100/(0.25x40) = -10
    assert _curve_at(bot["points"], 2026, 9, 1) == 20.0
    assert _curve_at(bot["points"], 2026, 9, 2) == 10.0
    assert bot["net"] == 10.0

    # A trade without a stop distance drops the whole bot back to per lot
    conn = sqlite3.connect(str(temp_db))
    _add_closed(conn, "RiskBot", 50.0, volume=0.5, sl_pips=None, exit_time="2026-09-03 10:00:00")
    conn.commit()
    conn.close()
    res = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="all", basis="lot")
    assert res["unit"] == "lot"
    points = res["series"][0]["points"]
    assert (_curve_at(points, 2026, 9, 1), _curve_at(points, 2026, 9, 2),
            _curve_at(points, 2026, 9, 3)) == (200.0, -200.0, -100.0)
    # The USD curve of the same trades is untouched by the basis
    usd = compute_bot_pnl_curves(account_id="demo", db_path=temp_db, period="all")
    points = usd["series"][0]["points"]
    assert (_curve_at(points, 2026, 9, 1), _curve_at(points, 2026, 9, 2),
            _curve_at(points, 2026, 9, 3)) == (200.0, 100.0, 150.0)


def test_bot_pnl_curves_thin_long_series_but_keep_the_ends():
    points = [{"t": i * 1000, "v": float(i)} for i in range(MAX_CURVE_POINTS * 3)]
    thinned = leaderboard_module._downsample(points)
    assert len(thinned) == MAX_CURVE_POINTS
    assert thinned[0] == points[0]
    assert thinned[-1] == points[-1]
    assert leaderboard_module._downsample(points[:5]) == points[:5]


def test_epoch_ms_reads_utc_text_and_skips_junk():
    assert leaderboard_module._epoch_ms("2026-09-01 10:00:00") == \
        int(datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert leaderboard_module._epoch_ms("2026-09-01T10:00:00Z") == \
        leaderboard_module._epoch_ms("2026-09-01 10:00:00")
    assert leaderboard_module._epoch_ms(None) is None
    assert leaderboard_module._epoch_ms("not a time") is None


def test_api_leaderboard_pnl_curves(temp_db, monkeypatch):
    conn = sqlite3.connect(str(temp_db))
    conn.execute("INSERT INTO accounts VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    _add_closed(conn, "CurveBot", 20.0, sl_pips=20.0, exit_time=_utc_ago(days=2))
    _add_closed(conn, "CurveBot", -5.0, sl_pips=20.0, exit_time=_utc_ago(days=1))
    conn.commit()
    conn.close()
    _use_leaderboard_db(monkeypatch, temp_db)

    client = TestClient(app)
    resp = client.get("/api/leaderboard/pnl-curves?account_id=all&account_type=demo&period=1w")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unit"] == "usd" and data["period"] == "1w"
    assert data["granularity"] == "day"
    assert data["series"][0]["bot_id"] == "CurveBot"
    assert data["series"][0]["points"][-1]["v"] == 15.0

    # The dashboard alias, and the lot-neutral basis the ranking tab asks for
    lot = client.get("/api/dashboard/leaderboard/pnl-curves?account_id=all&account_type=demo"
                     "&period=1w&basis=lot")
    assert lot.status_code == 200
    assert lot.json()["unit"] == "risk"
    # +20 over 2 risk units, then -5 over the same: 10 - 2.5
    assert lot.json()["series"][0]["points"][-1]["v"] == 7.5

    for query in ("date_from=2024-03-31&date_to=2024-03-01", "date_from=bogus", "date_to=2024-02-30"):
        resp = client.get(f"/api/leaderboard/pnl-curves?account_id=all&account_type=demo&period=custom&{query}")
        assert resp.status_code == 400, query


def test_dashboard_page_renders_bot_pnl_chart():
    html = TestClient(app).get("/demo/dashboard").text
    assert 'id="lb-chart-panel"' in html
    assert 'id="lb-pnl-chart"' in html and 'aria-label="Cumulative P&amp;L per bot"' in html
    assert 'id="lb-chart-bot"' in html
    assert 'id="lb-chart-toggle"' in html and 'aria-controls="lb-chart-body"' in html
    assert 'id="lb-chart-empty"' in html


def test_portfolio_summary_profit_factor_without_losses(temp_db, monkeypatch):
    """The Overview's PF follows the leaderboard: no loss yet is unbounded (None), not the gross profit."""
    import app.dashboard as dashboard_module
    conn = sqlite3.connect(str(temp_db))
    conn.execute("ALTER TABLE accounts ADD COLUMN last_balance REAL")
    conn.execute("ALTER TABLE accounts ADD COLUMN last_equity REAL")
    conn.execute("INSERT INTO accounts (account_id, account_type, label, is_configured) VALUES ('acc_demo_1', 'demo', 'Demo 1', 1)")
    for pnl in (0.2, 0.3, 0.3):
        _add_closed(conn, "MicroBot", pnl, volume=0.01)
    conn.commit()
    conn.close()
    monkeypatch.setattr(dashboard_module, "get_db", lambda: leaderboard_module.get_db_connection(temp_db))

    summary = dashboard_module.get_portfolio_summary("acc_demo_1")
    assert summary["total_trades"] == 3
    assert summary["profit_factor"] is None


CURVE_HARNESS = """
const els = {};
function fakeEl(id) {
    return els[id] = els[id] || { id, innerHTML: '', textContent: '', value: '', hidden: false,
        className: '', style: {}, classList: {toggle() {}, add() {}, remove() {}, contains: () => false},
        getContext: () => ({}) };
}
const document = {getElementById: fakeEl, querySelectorAll: () => []};
let built = null;
class Chart {
    constructor(ctx, config) {
        built = this;
        this.data = config.data;
        this.options = config.options;
        this.updates = 0;
    }
    update() { this.updates += 1; }
}
const LB_CURVE_COLORS = ['#38bdf8', '#f59e0b'];
const LB_CURVE_EMPTY = 'No closed trades in this period';
let lbCurveUnit = 'usd', lbCurveChart = null, lbChartBot = '';
%(functions)s
const data = %(data)s;
const out = {};
renderBotPnlCurves(data);
out.datasets = built.data.datasets.map(d => ({label: d.label, x: d.data.map(p => p.x),
    y: d.data.map(p => p.y), color: d.borderColor, fill: d.fill,
    interp: d.cubicInterpolationMode, tension: d.tension}));
out.select = els['lb-chart-bot'].innerHTML;
out.subtitle = els['lb-chart-subtitle'].textContent;
out.canvasHidden = els['lb-pnl-chart'].hidden;
out.emptyHidden = els['lb-chart-empty'].hidden;
out.y40 = built.options.scales.y.ticks.callback(40);
out.yNeg5 = built.options.scales.y.ticks.callback(-5);
out.tooltip = built.options.plugins.tooltip.callbacks.label({dataset: {label: 'Bot A'}, parsed: {x: 0, y: 12.3}});
out.delta = built.options.plugins.tooltip.callbacks.afterLabel(
    {dataset: {data: [{y: 0}, {y: 10}]}, dataIndex: 1, parsed: {y: 10}});
out.zeroGrid = built.options.scales.y.grid.color({tick: {value: 0}});
out.otherGrid = built.options.scales.y.grid.color({tick: {value: 5}});
out.xtick = built.options.scales.x.ticks.callback(Date.parse('2026-09-01T03:00:00Z'));
out.xtickFull = built.options.plugins.tooltip.callbacks.title([{parsed: {x: Date.parse('2026-09-01T03:00:00Z')}}]);
lbChartBot = 'A';
renderBotPnlCurves(data);
out.focused = built.data.datasets.map(d => d.label);
out.focusedFill = built.data.datasets[0].fill;
renderBotPnlCurves({unit: 'risk', series: [data.series[0]]});
out.riskY = built.options.scales.y.ticks.callback(12);
out.riskSubtitle = els['lb-chart-subtitle'].textContent;
renderBotPnlCurves({unit: 'usd', series: []});
out.emptyDatasets = built.data.datasets.length;
out.emptyCanvasHidden = els['lb-pnl-chart'].hidden;
out.emptyNoteHidden = els['lb-chart-empty'].hidden;
out.emptyNote = els['lb-chart-empty'].textContent;
out.updates = built.updates;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_pnl_chart_maps_series_to_datasets(tmp_path):
    """The chart draws the payload's per-bot curves, follows the focus picker, and formats the
    axes and tooltip in the unit the server reported."""
    html = DASHBOARD.read_text(encoding="utf-8")
    functions = [_dashboard_function(html, name) for name in
                 ("escapeHtml", "lbCurveEmpty", "lbCurveDay", "lbCurveDayFull", "lbCurveMoney",
                  "lbEnsureCurveChart", "renderBotPnlCurves")]
    data = {"unit": "usd", "series": [
        {"bot_id": "A", "bot_name": "Bot A", "net": 20.0, "basis": "usd",
         "points": [{"t": 1788231600000, "v": 0.0}, {"t": 1788318000000, "v": 20.0}]},
        {"bot_id": "B", "bot_name": "Bot B", "net": -5.0, "basis": "usd",
         "points": [{"t": 1788231600000, "v": 0.0}, {"t": 1788318000000, "v": -5.0}]},
    ]}
    script = tmp_path / "curve.js"
    script.write_text(CURVE_HARNESS % {"functions": "\n".join(functions), "data": json.dumps(data)},
                      encoding="utf-8")
    run = subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "TZ": "Asia/Saigon"})
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    assert [d["label"] for d in out["datasets"]] == ["Bot A", "Bot B"]
    assert out["datasets"][0]["x"] == [1788231600000, 1788318000000]
    assert out["datasets"][0]["y"] == [0.0, 20.0]
    assert [d["color"] for d in out["datasets"]] == ["#38bdf8", "#f59e0b"]
    # Daily points are smoothed with a monotone spline, unfilled while several bots share the chart
    assert {d["interp"] for d in out["datasets"]} == {"monotone"}
    assert {d["fill"] for d in out["datasets"]} == {False}
    # The focus picker lists every bot with its final result
    assert "Bot A · $20.00" in out["select"] and "Bot B · -$5.00" in out["select"]
    assert out["canvasHidden"] is False and out["emptyHidden"] is True
    # USD axis, tooltip, day ticks (Vietnam time) and the zero line
    assert out["y40"] == "$40" and out["yNeg5"] == "-$5"
    assert out["tooltip"] == "Bot A: $12.30"
    assert out["delta"] == "Δ +$10.00"
    assert out["xtick"] == "01/09" and out["xtickFull"] == "01/09/2026"
    assert "148,163,184" in out["zeroGrid"] and "64,64,64" in out["otherGrid"]
    assert "Daily cumulative" in out["subtitle"]
    # Focusing one bot narrows the chart to its line, with a soft area under it
    assert out["focused"] == ["Bot A"]
    assert out["focusedFill"] is True
    # The lot-neutral tab reads in risk units and says so
    assert out["riskY"] == "12R"
    assert "lot per stop pip" in out["riskSubtitle"]
    # Nothing closed: the canvas gives way to the empty note
    assert out["emptyDatasets"] == 0
    assert out["emptyCanvasHidden"] is True and out["emptyNoteHidden"] is False
    assert out["emptyNote"] == "No closed trades in this period"
    assert out["updates"] >= 4


SORT_HARNESS = """
const LB_TIER_ORDER = {TIER_S: 5, TIER_A: 4, TIER_B: 3, TIER_C: 2, UNRATED: 1};
let lbSorts = {usd: null, lot: null};
%(functions)s
const bots = [
    {bot_id: 'A', bot_name: 'Alpha', rank: 1, total_trades: 5, win_rate: 60, profit_factor: 2, total_pnl_usd: 50, payoff_ratio: 2.5, return_dd: 3, net_units: 10, tier_badge: 'TIER_B'},
    {bot_id: 'B', bot_name: 'Bravo', rank: 2, total_trades: 0, win_rate: 0, profit_factor: 1, total_pnl_usd: 0, payoff_ratio: 0, return_dd: null, net_units: 0, tier_badge: 'UNRATED'},
    {bot_id: 'C', bot_name: 'Charlie', rank: 3, total_trades: 3, win_rate: 80, profit_factor: null, total_pnl_usd: -10, payoff_ratio: null, return_dd: null, net_units: 4, tier_badge: 'TIER_A'},
    {bot_id: 'D', bot_name: 'delta', rank: 4, total_trades: 2, win_rate: 40, profit_factor: 1.5, total_pnl_usd: 20, payoff_ratio: 1.25, return_dd: -1, net_units: -2, tier_badge: 'TIER_B'},
    {bot_id: 'E', bot_name: 'Echo', rank: 5, total_trades: 4, win_rate: 55, profit_factor: 1.8, total_pnl_usd: 50, payoff_ratio: 1.9, return_dd: 2, net_units: 8, tier_badge: 'TIER_B'},
];
const ids = rows => rows.map(b => b.bot_id);
const out = {};
out.default = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'total_pnl_usd', dir: 'desc'};
out.pnlDesc = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'total_pnl_usd', dir: 'asc'};
out.pnlAsc = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'profit_factor', dir: 'desc'};
out.pfDesc = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'profit_factor', dir: 'asc'};
out.pfAsc = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'win_rate', dir: 'desc'};
out.winDesc = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'bot_name', dir: 'asc'};
out.byName = ids(lbSortedRows(bots, 'usd'));
lbSorts.usd = {key: 'tier_badge', dir: 'desc'};
out.byTier = ids(lbSortedRows(bots, 'usd'));
lbSorts.lot = {key: 'payoff_ratio', dir: 'desc'};
out.payoffDesc = ids(lbSortedRows(bots, 'lot'));
lbSorts.usd = {key: 'return_dd', dir: 'asc'};
out.rddAsc = ids(lbSortedRows(bots, 'usd'));
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_leaderboard_column_sorting(tmp_path):
    """Headers sort by value, flip direction, keep the server's rank order for ties, put a
    bot without closed trades after real metrics, and rank unbounded PF as the best."""
    html = DASHBOARD.read_text(encoding="utf-8")
    functions = [_dashboard_function(html, name) for name in ("lbSortValue", "lbSortedRows")]
    script = tmp_path / "sort.js"
    script.write_text(SORT_HARNESS % {"functions": "\n".join(functions)}, encoding="utf-8")
    run = subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr

    out = json.loads(run.stdout)
    assert out["default"] == ["A", "B", "C", "D", "E"]          # no sort = server ranking
    assert out["pnlDesc"] == ["A", "E", "D", "B", "C"]          # tie keeps rank 1 before rank 5
    assert out["pnlAsc"] == ["C", "B", "D", "A", "E"]
    assert out["pfDesc"] == ["C", "A", "E", "D", "B"]           # null PF (no losses) is best
    assert out["pfAsc"] == ["D", "E", "A", "C", "B"]            # ... and B (no trades) is last
    assert out["winDesc"] == ["C", "A", "E", "D", "B"]
    assert out["byName"] == ["A", "B", "C", "D", "E"]
    assert out["byTier"] == ["C", "A", "D", "E", "B"]           # TIER_A, TIER_B by rank, UNRATED
    assert out["payoffDesc"] == ["C", "A", "E", "D", "B"]
    assert out["rddAsc"] == ["D", "E", "A", "C", "B"]           # no drawdown yet counts as +∞
