"""Recent Trades pagination and Vietnam-time display on the dashboard."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app  # noqa: E402
from app.db import get_db_connection  # noqa: E402

client = TestClient(app)


# --- format_vn_time -------------------------------------------------------------------------

def test_format_vn_time_shifts_sqlite_utc_to_gmt7_and_reorders_fields():
    from app.dashboard import format_vn_time

    # SQLite datetime('now') writes UTC as 'YYYY-MM-DD HH:MM:SS'; Vietnam is a fixed UTC+7.
    assert format_vn_time("2026-09-22 03:04:05") == "10:04:05 22/09/2026"


def test_format_vn_time_rolls_the_date_forward_across_midnight():
    from app.dashboard import format_vn_time

    assert format_vn_time("2026-09-22 20:30:00") == "03:30:00 23/09/2026"


def test_format_vn_time_leaves_unparseable_or_missing_values_alone():
    from app.dashboard import format_vn_time

    assert format_vn_time(None) is None
    assert format_vn_time("") == ""
    assert format_vn_time("not a timestamp") == "not a timestamp"


# --- format_duration ------------------------------------------------------------------------

@pytest.mark.parametrize("entry, exit_, expected", [
    ("2026-09-22 03:00:00", "2026-09-22 03:00:42", "42s"),
    ("2026-09-22 03:00:00", "2026-09-22 03:04:12", "4m 12s"),
    ("2026-09-22 03:00:00", "2026-09-22 05:28:30", "2h 28m"),
    ("2026-09-20 03:00:00", "2026-09-22 07:10:00", "2d 4h"),
])
def test_format_duration_picks_the_two_most_useful_units(entry, exit_, expected):
    from app.dashboard import format_duration

    assert format_duration(entry, exit_) == expected


def test_format_duration_is_none_when_a_time_is_missing_unparseable_or_reversed():
    from app.dashboard import format_duration

    assert format_duration(None, "2026-09-22 03:00:00") is None
    assert format_duration("2026-09-22 03:00:00", None) is None
    assert format_duration("garbage", "2026-09-22 03:00:00") is None
    assert format_duration("2026-09-22 04:00:00", "2026-09-22 03:00:00") is None


# --- Recent Trades pagination ----------------------------------------------------------------

SEED_ACCOUNT = "test-pager-acc"
SEED_BOT = "test-pager-bot"
SEED_COUNT = 23  # 10 per page -> 3 pages, last one holds 3 rows


@pytest.fixture
def seeded_closed_trades():
    """23 closed trades for one isolated account, newest exit first: trade #23 closed last."""
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (SEED_ACCOUNT,))
        for i in range(1, SEED_COUNT + 1):
            conn.execute(
                """INSERT INTO positions (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips,
                                          entry_time, exit_time, exit_price, pnl, status, account_id)
                   VALUES (?, ?, 'buy', 0.1, 1.1, 20, 40, ?, ?, 1.2, ?, 'closed', ?)""",
                (SEED_BOT, f"SYM{i:02d}", f"2026-09-01 {i:02d}:00:00", f"2026-09-02 {i:02d}:00:00",
                 float(i), SEED_ACCOUNT),
            )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (SEED_ACCOUNT,))
        conn.commit()
    finally:
        conn.close()


def test_trade_history_returns_one_page_of_ten_with_paging_metadata(seeded_closed_trades):
    from app.dashboard import get_trade_history

    page = get_trade_history(account_id=SEED_ACCOUNT, page=1, page_size=10)

    assert page["total"] == SEED_COUNT
    assert page["page"] == 1
    assert page["page_size"] == 10
    assert page["total_pages"] == 3
    assert [t["symbol"] for t in page["items"]] == [f"SYM{i:02d}" for i in range(23, 13, -1)]


def test_trade_history_last_page_holds_the_remainder(seeded_closed_trades):
    from app.dashboard import get_trade_history

    page = get_trade_history(account_id=SEED_ACCOUNT, page=3, page_size=10)

    assert [t["symbol"] for t in page["items"]] == ["SYM03", "SYM02", "SYM01"]
    assert page["page"] == 3


def test_trade_history_page_beyond_the_end_is_empty_but_keeps_totals(seeded_closed_trades):
    from app.dashboard import get_trade_history

    page = get_trade_history(account_id=SEED_ACCOUNT, page=9, page_size=10)

    assert page["items"] == []
    assert page["total"] == SEED_COUNT
    assert page["total_pages"] == 3


def test_trade_history_times_are_rendered_in_vietnam_time(seeded_closed_trades):
    from app.dashboard import get_trade_history

    newest = get_trade_history(account_id=SEED_ACCOUNT, page=1, page_size=10)["items"][0]

    assert newest["entry_time"] == "06:00:00 02/09/2026"   # 2026-09-01 23:00 UTC -> next day in VN
    assert newest["exit_time"] == "06:00:00 03/09/2026"
    assert newest["duration"] == "1d 0h"


def test_history_endpoint_exposes_pagination(seeded_closed_trades):
    resp = client.get(f"/api/dashboard/history?account_id={SEED_ACCOUNT}&page=2&page_size=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2
    assert body["total"] == SEED_COUNT
    assert body["total_pages"] == 3
    assert [t["symbol"] for t in body["items"]] == [f"SYM{i:02d}" for i in range(13, 3, -1)]


def test_history_endpoint_defaults_to_first_page_of_ten(seeded_closed_trades):
    body = client.get(f"/api/dashboard/history?account_id={SEED_ACCOUNT}").json()

    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 10


# --- Active Positions open time --------------------------------------------------------------

def test_active_positions_open_time_is_rendered_in_vietnam_time():
    from app.dashboard import get_active_positions

    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM positions WHERE account_id = ?", (SEED_ACCOUNT,))
        conn.execute(
            """INSERT INTO positions (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips,
                                      entry_time, status, account_id)
               VALUES (?, 'XAUUSD', 'sell', 0.2, 2400.0, 30, 60, '2026-09-22 18:45:10', 'open', ?)""",
            (SEED_BOT, SEED_ACCOUNT),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        positions = get_active_positions(SEED_ACCOUNT)
        assert len(positions) == 1
        assert positions[0]["entry_time"] == "01:45:10 23/09/2026"
    finally:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM positions WHERE account_id = ?", (SEED_ACCOUNT,))
            conn.commit()
        finally:
            conn.close()


# --- Dashboard page ---------------------------------------------------------------------------

def test_dashboard_page_renders_recent_trades_pager():
    html = client.get("/demo/dashboard").text

    assert 'id="history-prev"' in html
    assert 'id="history-next"' in html
    assert 'id="history-page-label"' in html
    assert "<th>Open / Close</th>" in html
