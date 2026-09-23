"""
Regression guards from the 2026-09-23 VPS audit.

1. Margin check. check_risk priced every open lot at $1000 whatever the instrument, so
   0.4 lots of ETHUSD and 0.3 of DE40 counted like forex lots and blocked US30, USTEC,
   XAUUSD and UK100 entries at a "64.4%" the account never used. The cBots now send
   cTrader's Account.Margin and check_risk prefers it.

2. Closed volume. record_partial_close shrinks `volume` to what is still open, and the
   final close left it there: GBPJPY #674942756 opened 0.08 lots, the closed row said
   0.06 lots next to the P&L of all 0.08.

3. Trailing step. FlowRsiBot moved the stop on every tick a pip at a time: ETHUSD
   #674939909 sent seven modifies in three seconds.
"""

from pathlib import Path

import pytest

from app.portfolio import PortfolioManager
from app.server import MarketSnapshot

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(db_path=str(tmp_path / "audit.db"))


def _open(pm, symbol="ETHUSD", volume=0.4, bot_id="ethusd-all-flowrsi"):
    pm.register_position(
        bot_id=bot_id, symbol=symbol, side="Sell", volume=volume, entry_price=2769.12,
        sl_pips=2506.3, tp_pips=3219.9, account_id="acct-1", ctrader_id=674939909,
    )


def _row(pm):
    conn = pm._get_conn()
    try:
        r = conn.execute("SELECT volume, initial_volume, pnl, status FROM positions").fetchone()
        return {"volume": r[0], "initial_volume": r[1], "pnl": r[2], "status": r[3]}
    finally:
        conn.close()


# --- margin -----------------------------------------------------------------------------

def test_reported_margin_overrides_the_per_lot_estimate(pm):
    # 1.2 open lots at the $1000/lot estimate is 61% of a $1952 balance: blocked.
    _open(pm, volume=1.2)
    allowed, reason = pm.check_risk("US30", "SELL", 0.1, account_balance=1952.88, account_id="acct-1")
    assert not allowed and "Margin usage" in reason

    # The broker says $180 is actually in use: 9%.
    allowed, reason = pm.check_risk("US30", "SELL", 0.1, account_balance=1952.88,
                                    account_id="acct-1", used_margin=180.0)
    assert allowed, reason


def test_reported_margin_still_blocks_when_really_high(pm):
    allowed, reason = pm.check_risk("EURUSD", "BUY", 0.01, account_balance=2000.0,
                                    account_id="acct-1", used_margin=1200.0)
    assert not allowed
    assert "60.0%" in reason


def test_zero_reported_margin_is_a_real_value(pm):
    _open(pm, volume=5.0)  # the estimate alone would block
    allowed, _ = pm.check_risk("EURUSD", "BUY", 0.01, account_balance=2000.0,
                               account_id="acct-1", used_margin=0.0)
    assert allowed


def test_snapshot_carries_account_margin():
    base = dict(symbol="EURUSD", timeframe="Minute15", ask=1.1, bid=1.1)
    assert MarketSnapshot(**base).account_margin is None
    assert MarketSnapshot(**base, account_margin=123.45).account_margin == 123.45


@pytest.mark.parametrize("cs_name", ["FlowRsiBot.cs", "AiAgentBot.cs", "AsianRangeJudasSweepBot.cs"])
def test_cbots_send_account_margin(cs_name):
    src = (ROOT / "cBot" / cs_name).read_text(encoding="utf-8")
    assert "public double account_margin { get; set; }" in src
    assert "account_margin = " in src and "Account.Margin" in src


def test_server_passes_reported_margin_to_every_risk_check():
    src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    calls = src.count("portfolio_manager.check_risk(")
    assert calls >= 2
    assert src.count("used_margin=snapshot.account_margin") == calls


# --- closed volume ----------------------------------------------------------------------

def test_close_restores_the_opening_volume_after_a_partial(pm):
    _open(pm, volume=0.4)
    pm.record_partial_close("ethusd-all-flowrsi", "ETHUSD", remaining_volume=0.3,
                            realized_pnl=2.48, account_id="acct-1", ctrader_id=674939909)
    assert _row(pm)["volume"] == pytest.approx(0.3)  # open: what is still exposed

    pm.close_position("ethusd-all-flowrsi", "ETHUSD", exit_price=2737.29, pnl=9.63,
                      account_id="acct-1", ctrader_id=674939909)
    row = _row(pm)
    assert row["status"] == "closed"
    assert row["volume"] == pytest.approx(0.4)
    assert row["pnl"] == pytest.approx(12.11)


def test_partial_on_a_row_without_initial_volume_records_it(pm):
    # Rows opened before the column existed have initial_volume NULL.
    _open(pm, volume=0.08)
    conn = pm._get_conn()
    conn.execute("UPDATE positions SET initial_volume = NULL")
    conn.commit()
    conn.close()

    pm.record_partial_close("ethusd-all-flowrsi", "ETHUSD", remaining_volume=0.06,
                            realized_pnl=2.21, account_id="acct-1", ctrader_id=674939909)
    assert _row(pm)["initial_volume"] == pytest.approx(0.08)
    pm.close_position("ethusd-all-flowrsi", "ETHUSD", exit_price=1.0, pnl=0.17,
                      account_id="acct-1", ctrader_id=674939909)
    assert _row(pm)["volume"] == pytest.approx(0.08)


def test_close_without_partial_keeps_volume(pm):
    _open(pm, volume=0.06)
    pm.close_position("ethusd-all-flowrsi", "ETHUSD", exit_price=2780.0, pnl=-9.6,
                      account_id="acct-1", ctrader_id=674939909)
    assert _row(pm)["volume"] == pytest.approx(0.06)


def test_price_columns_are_widened_on_postgres():
    src = (ROOT / "app" / "portfolio.py").read_text(encoding="utf-8")
    assert "USING {col}::text::double precision" in src, (
        "float4 -> double must go through text: a direct numeric cast keeps six digits."
    )
    for col in ("volume", "entry_price", "exit_price"):
        assert f"'{col}'" in src


# --- trailing step ----------------------------------------------------------------------

def test_flowrsi_trailing_moves_in_steps():
    src = (ROOT / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")
    assert "trailStepPrice" in src
    assert "candidateTrailSL >= pos.StopLoss.Value + trailStepPrice" in src
    assert "candidateTrailSL <= pos.StopLoss.Value - trailStepPrice" in src
