"""
Regression guards for X-01 and X-02 (audit 2026-09-22).

X-01 -- ``close_position`` matched on ``bot_id + symbol + status='open' + account_id``
only. The cBot has always SENT ``ctrader_id`` in the payload, but the positions
table had no such column, so it was dropped on the floor. Safe today only because
``MaxPositionsAllowed = 1``; the moment that is raised, one close report closes
EVERY open position for that (bot_id, symbol). The same weakness applies to the
partial-close path added for C-03.

X-02 -- the Judas XAUUSD preset passes ``sweepBufferPips = 30.0``. Gold quotes at
1 pip = $0.01, so that is a **$0.30** buffer: far too tight to tell a real sweep
from noise. The bot's own auto-scale (AsianRangeJudasSweepBot.cs:517) wants 500
($5.00) for gold, but only fires when ``maxAsianRangePips <= 500`` and the preset
passes 8000, so it never ran. The same value also sets the structural-invalidation
threshold, so gold's "decisive break" was $0.30 too.
"""

from pathlib import Path

import pytest

from app.cbot_presets import PRESETS
from app.portfolio import PortfolioManager

ROOT = Path(__file__).resolve().parent.parent


# ── X-01 ──

@pytest.fixture
def pm(tmp_path):
    manager = PortfolioManager(db_path=str(tmp_path / "identity.db"))
    for ctid, entry in ((111, 1.1000), (222, 1.1050)):
        manager.register_position(
            bot_id="eurusd-bot", symbol="EURUSD", side="Buy", volume=0.1,
            entry_price=entry, sl_pips=20.0, tp_pips=40.0, account_id="acct-1",
            ctrader_id=ctid,
        )
    return manager


def _statuses(manager):
    conn = manager._get_conn()
    try:
        rows = conn.execute(
            "SELECT ctrader_id, status FROM positions ORDER BY ctrader_id"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def test_close_only_affects_the_reported_position(pm):
    pm.close_position(
        bot_id="eurusd-bot", symbol="EURUSD", exit_price=1.1100, pnl=10.0,
        account_id="acct-1", ctrader_id=111,
    )
    statuses = _statuses(pm)
    assert statuses[111] == "closed"
    assert statuses[222] == "open", (
        "closing one position closed every open position for the same (bot_id, symbol) - "
        "this is what breaks the moment MaxPositionsAllowed goes above 1"
    )


def test_partial_close_only_affects_the_reported_position(pm):
    pm.record_partial_close(
        bot_id="eurusd-bot", symbol="EURUSD", remaining_volume=0.05,
        realized_pnl=4.0, account_id="acct-1", ctrader_id=222,
    )
    conn = pm._get_conn()
    try:
        rows = conn.execute(
            "SELECT ctrader_id, volume, pnl FROM positions ORDER BY ctrader_id"
        ).fetchall()
    finally:
        conn.close()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    assert by_id[222][0] == pytest.approx(0.05)
    assert by_id[111][0] == pytest.approx(0.1), (
        "a partial close shrank a different position's volume"
    )
    assert by_id[111][1] in (None, 0), (
        "a partial close banked P&L onto a different position"
    )


def test_close_without_a_ctrader_id_still_works(pm):
    """Guard against over-fixing: older payloads and the legacy path must keep working."""
    assert pm.close_position(
        bot_id="eurusd-bot", symbol="EURUSD", exit_price=1.1100, pnl=10.0,
        account_id="acct-1",
    )


def test_close_position_with_ctrader_id_matches_open_position_having_null_ctrader_id(tmp_path):
    """If a position was registered without ctrader_id, closing with ctrader_id must still find and close it."""
    manager = PortfolioManager(db_path=str(tmp_path / "legacy_close.db"))
    manager.register_position(
        bot_id="hk50-bot", symbol="HK50", side="Sell", volume=0.2,
        entry_price=24858.5, sl_pips=50.0, tp_pips=100.0, account_id="acct-live",
        ctrader_id=None,
    )
    assert manager.close_position(
        bot_id="hk50-bot", symbol="HK50", exit_price=24799.7, pnl=0.59,
        account_id="acct-live", ctrader_id=347787252, close_reason="Session End",
    )
    conn = manager._get_conn()
    try:
        row = conn.execute("SELECT status, pnl, ctrader_id, close_reason FROM positions WHERE bot_id = ?", ("hk50-bot",)).fetchone()
        assert row[0] == "closed"
        assert row[1] == pytest.approx(0.59)
        assert row[2] == 347787252
        assert row[3] == "Session End"
    finally:
        conn.close()


def test_partial_close_with_ctrader_id_matches_open_position_having_null_ctrader_id(tmp_path):
    """If a position was registered without ctrader_id, partial close must bank PnL and attach ctrader_id."""
    manager = PortfolioManager(db_path=str(tmp_path / "legacy_partial.db"))
    manager.register_position(
        bot_id="hk50-bot", symbol="HK50", side="Sell", volume=0.2,
        entry_price=24858.5, sl_pips=50.0, tp_pips=100.0, account_id="acct-live",
        ctrader_id=None,
    )
    # Partial close:
    assert manager.record_partial_close(
        bot_id="hk50-bot", symbol="HK50", remaining_volume=0.1, realized_pnl=0.52,
        account_id="acct-live", ctrader_id=347787252,
    )
    conn = manager._get_conn()
    try:
        row = conn.execute("SELECT status, volume, pnl, ctrader_id FROM positions WHERE bot_id = ?", ("hk50-bot",)).fetchone()
        assert row[0] == "open"
        assert row[1] == pytest.approx(0.1)
        assert row[2] == pytest.approx(0.52)
        assert row[3] == 347787252
    finally:
        conn.close()

    # Subsequent final close uses ctrader_id:
    assert manager.close_position(
        bot_id="hk50-bot", symbol="HK50", exit_price=24799.7, pnl=0.59,
        account_id="acct-live", ctrader_id=347787252,
    )
    conn = manager._get_conn()
    try:
        row = conn.execute("SELECT status, volume, pnl, ctrader_id FROM positions WHERE bot_id = ?", ("hk50-bot",)).fetchone()
        assert row[0] == "closed"
        assert row[1] == pytest.approx(0.2)  # initial_volume restored on close
        assert row[2] == pytest.approx(1.11)  # 0.52 + 0.59
    finally:
        conn.close()


def _rows(manager):
    conn = manager._get_conn()
    try:
        return [
            tuple(r) for r in conn.execute(
                "SELECT id, ctrader_id, status, volume, pnl FROM positions ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_replayed_close_does_not_close_the_next_position(pm):
    """
    A close report whose id has no open row -- replayed, or sent for a position whose
    open report never reached the server -- must not fall back to "any open position of
    the pair". That closed position 222 with 111's exit and P&L, and rewrote its
    ctrader_id to 111.
    """
    assert pm.close_position(
        bot_id="eurusd-bot", symbol="EURUSD", exit_price=1.0980, pnl=-9.7,
        account_id="acct-1", ctrader_id=111,
    )
    before = _rows(pm)

    assert not pm.close_position(
        bot_id="eurusd-bot", symbol="EURUSD", exit_price=1.0980, pnl=-9.7,
        account_id="acct-1", ctrader_id=111,
    )
    assert _rows(pm) == before, "a replayed close for 111 changed another position"


def test_partial_close_for_an_unknown_id_touches_no_position(pm):
    before = _rows(pm)
    assert not pm.record_partial_close(
        bot_id="eurusd-bot", symbol="EURUSD", remaining_volume=0.05,
        realized_pnl=4.0, account_id="acct-1", ctrader_id=999,
    )
    assert _rows(pm) == before, "a partial close for an unknown id shrank another position"


def test_close_and_partial_close_return_false_when_no_position_matched(tmp_path):
    """Unmatched closes must return False and not silently succeed."""
    manager = PortfolioManager(db_path=str(tmp_path / "empty.db"))
    assert not manager.close_position(
        bot_id="ghost-bot", symbol="EURUSD", exit_price=1.10, pnl=0.0,
        account_id="acct-1", ctrader_id=999,
    )
    assert not manager.record_partial_close(
        bot_id="ghost-bot", symbol="EURUSD", remaining_volume=0.05, realized_pnl=1.0,
        account_id="acct-1", ctrader_id=999,
    )

# ── X-02 ──

def test_gold_judas_sweep_buffer_is_scaled_for_dollar_quoting():
    buffer_pips = PRESETS[("judas", "XAUUSD")]["params"]["sweepBufferPips"]
    assert buffer_pips >= 300.0, (
        f"XAUUSD sweep buffer is {buffer_pips} pips = ${buffer_pips * 0.01:.2f}. Gold "
        f"quotes 1 pip = $0.01, so this cannot separate a real sweep from noise - and it "
        f"doubles as the structural-invalidation threshold."
    )


def test_gold_buffer_matches_the_bots_own_auto_scale_intent():
    src = (ROOT / "cBot" / "AsianRangeJudasSweepBot.cs").read_text(encoding="utf-8")
    assert "sweepBufferPips = 500.0" in src, (
        "The bot's gold auto-scale value moved; the preset should track it."
    )
    assert PRESETS[("judas", "XAUUSD")]["params"]["sweepBufferPips"] == 500.0, (
        "The preset should use the same $5.00 buffer the bot's own auto-scale intends, "
        "since the auto-scale branch never fires (maxAsianRangePips is 8000)."
    )


@pytest.mark.parametrize("cs_name", ["FlowRsiBot.cs", "AiAgentBot.cs"])
@pytest.mark.parametrize("action", ["open", "partial_close", "close"])
def test_bot_sends_ctrader_id_with_every_position_report(cs_name, action):
    """The server-side narrowing is inert unless the bot actually sends the id."""
    src = (ROOT / "cBot" / cs_name).read_text(encoding="utf-8")
    idx = src.index(f'action = "{action}",')
    window = src[max(0, idx - 300) : idx]
    assert "ctrader_id" in window, (
        f"{cs_name} sends action={action} without ctrader_id, so the server cannot tell "
        f"this position from any other open one for the same (bot_id, symbol)."
    )
