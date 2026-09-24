"""
Regression guard for C-10 (audit 2026-09-22): a BE stop masqueraded as initial risk.

``_initialSlDistances`` is an in-RAM Dictionary, lost on every restart. The
fallback measured the CURRENT stop:

    : (pos.StopLoss.HasValue ? Math.Abs(pos.EntryPrice - pos.StopLoss.Value) / PipSize : 20.0)

For a position already moved to break-even that is ~0.5-1 pip, so
``currentRr = pnlPips / initialSlDist`` inflates by an order of magnitude.
Restarting with GBPJPY at +25p and the stop at BE+0.5p gives currentRr ~= 50,
blowing straight past ``TrailingStopTriggerRr = 1.8`` on the first tick and
strangling the trade -- and every R-gated decision (partial close, tiers) runs on
a fabricated R.

Two layers here: recover the real ``sl_pips`` from the server, which already has
it from the open report, and floor the local fallback so a BE stop can never be
mistaken for the original risk when recovery is unavailable.
"""

from pathlib import Path

import pytest

from app.portfolio import PortfolioManager

ROOT = Path(__file__).resolve().parent.parent
CS_PATH = ROOT / "cBot" / "FlowRsiBot.cs"


def _method_body(signature: str) -> str:
    src = CS_PATH.read_text(encoding="utf-8")
    start = src.index(signature)
    open_idx = src.index("{", start)
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces in {signature!r}")


# ── server: the original sl_pips must be retrievable ──

def test_open_positions_are_queryable_with_their_original_sl(tmp_path):
    pm = PortfolioManager(db_path=str(tmp_path / "restore.db"))
    pm.register_position(
        bot_id="gbpjpy-all-flowrsi", symbol="GBPJPY", side="Sell", volume=0.09,
        entry_price=195.40, sl_pips=22.5, tp_pips=45.0, account_id="acct-1",
    )
    rows = pm.get_open_positions(bot_id="gbpjpy-all-flowrsi", account_id="acct-1")
    assert len(rows) == 1
    assert rows[0]["sl_pips"] == pytest.approx(22.5), (
        "the stop distance recorded at entry is what a restarting bot needs back"
    )
    assert rows[0]["entry_price"] == pytest.approx(195.40), (
        "entry_price is how the bot matches a DB row to a live position"
    )


def test_closed_positions_are_not_returned(tmp_path):
    pm = PortfolioManager(db_path=str(tmp_path / "restore2.db"))
    pm.register_position(
        bot_id="b", symbol="EURUSD", side="Buy", volume=0.1, entry_price=1.1,
        sl_pips=20.0, tp_pips=40.0, account_id="a",
    )
    pm.close_position(bot_id="b", symbol="EURUSD", exit_price=1.2, pnl=5.0, account_id="a")
    assert pm.get_open_positions(bot_id="b", account_id="a") == []


# ── cBot: restore on start, and never trust a BE stop as initial risk ──

def test_bot_restores_initial_sl_distances_on_start():
    body = _method_body("protected override void OnStart()")
    assert "RestoreInitialSlDistances" in body, (
        "OnStart does not try to recover _initialSlDistances, so every restart "
        "re-derives R from whatever stop the position currently carries."
    )


def test_fallback_refuses_to_treat_a_break_even_stop_as_initial_risk():
    body = _method_body("private void ManageExits()")
    fallback_idx = body.index("pos.StopLoss.HasValue")
    region = body[max(0, fallback_idx - 600) : fallback_idx + 800]
    assert "MinSlFloorPips" in region or "effectiveMinSl" in region, (
        "The fallback still uses the raw current stop distance. On a position already "
        "at break-even that is ~0.5p, which inflates currentRr by ~50x."
    )


def _fallback_region() -> str:
    body = _method_body("private void ManageExits()")
    start = body.index("else", body.index("_initialSlDistances.ContainsKey(pos.Id)"))
    return body[start : body.index("double currentRr")]


def test_fallback_measures_the_stop_only_while_it_is_on_the_losing_side():
    """AUDJPY #675464477, 2026-09-24: the fallback measured a stop trailed 30p past entry as
    the initial risk. The trail distance is max(floor, that), so every move widened the next
    one and the stop rose at about half the pace of price (49p behind at 112.00, not 27p).
    A stop at or past entry is locked profit: the fallback must fall to the floor instead."""
    fallback = _fallback_region()
    assert "pos.StopLoss.Value < pos.EntryPrice" in fallback, "BUY: stop below entry is risk"
    assert "pos.StopLoss.Value > pos.EntryPrice" in fallback, "SELL: stop above entry is risk"
    assert "Math.Max(measured, effectiveMinSl)" in fallback


def test_a_failed_restore_is_retried_until_the_server_answers():
    """The same restart timed out the only restore call (60 s, server busy restarting) and
    nothing tried again, so the position stayed on the fallback R until it closed."""
    restore = _method_body("private async Task RestoreInitialSlDistances()")
    assert restore.count("ScheduleSlRestoreRetry(") == 2, "an error status and an exception both retry"
    assert restore.index("IsSuccessStatusCode") < restore.index("_slRestoreDone = true")
    assert "Interlocked.Exchange(ref _slRestoreInFlight, 0);" in restore[restore.rindex("finally"):]

    gate = _method_body("private void RestoreInitialSlDistancesIfDue()")
    assert "_slRestoreDone" in gate and "_nextSlRestoreAttempt" in gate
    assert "Interlocked.CompareExchange(ref _slRestoreInFlight, 1, 0)" in gate

    assert "RestoreInitialSlDistancesIfDue();" in _method_body("protected override void OnStart()")
    assert "RestoreInitialSlDistancesIfDue();" in _fallback_region(), (
        "only a position without a recorded distance needs the server lookup"
    )
