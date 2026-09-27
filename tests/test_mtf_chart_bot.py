"""Source contract for the M15 multi-timeframe cBot."""

from pathlib import Path

CS = Path(__file__).resolve().parent.parent / "cBot" / "MtfChartBot.cs"


def _src() -> str:
    return CS.read_text(encoding="utf-8")


def test_bot_is_m15_and_refuses_a_live_account():
    src = _src()
    assert "TimeFrame.Minute15" in src
    assert "Account.IsLive" in src
    assert "Live account refused" in src


def test_decision_uses_closed_bars_and_fails_closed():
    src = _src()
    assert "series.Count - 2" in src
    assert "no trade" in src
    assert "IsSuccessStatusCode" in src


def test_order_is_sized_down_and_carries_stop_and_target():
    src = _src()
    assert "RoundingMode.Down" in src
    assert "Not rounding up" in src
    assert "ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, clientId)" in src
    assert "PlaceLimitOrder(side, SymbolName, volume, entry, BotId, slPips, tpPips, null, clientId)" in src
    assert "PlaceStopOrder(side, SymbolName, volume, entry, BotId, slPips, tpPips, null, clientId)" in src
    assert "CancelPendingOrder" in src
    assert "ClosePosition" in src


def test_own_fills_and_closes_reach_the_portfolio_like_the_other_bots():
    src = _src()
    assert "Positions.Opened += OnPositionOpened" in src
    assert "Positions.Closed += OnPositionClosed" in src
    assert "args.Position.Label != BotId" in src
    assert '"/portfolio/report"' in src
    assert 'action = "open"' in src
    assert 'action = "close"' in src


def test_forward_test_events_are_journaled():
    src = _src()
    assert '"/trade/mtf/event"' in src
    assert 'JournalEvent("close"' in src
    assert 'JournalEvent("error"' in src
    assert 'JournalEvent("expiry"' in src
    assert "ClientOrderId" in src
