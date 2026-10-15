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


def test_decision_uses_the_just_closed_bars_and_fails_closed():
    src = _src()
    # Count - 2 dropped the bar that had just closed; the index is resolved by time instead.
    assert "series.Count - 2" not in src
    assert src.count("ClosedBars(_d1") == 1 and src.count("ClosedBars(Bars, M15Bars)") == 1
    assert "no trade" in src
    assert "IsSuccessStatusCode" in src


def test_a_backtest_never_writes_to_the_portfolio_or_the_forward_test_journal():
    src = _src()
    assert "RunningMode == RunningMode.RealTime" in src
    assert '["running_mode"] = RunningMode.ToString()' in src
    # one choke point for the journal, plus the two portfolio reporters
    assert src.count("if (!IsRealTime)") >= 3
    assert "no /portfolio/report, no forward-test journal" in src


def test_a_market_order_is_measured_from_the_price_it_fills_at():
    src = _src()
    assert "double fillPrice = market ? (buy ? Symbol.Ask : Symbol.Bid) : entry;" in src
    assert "market_price_past_bracket" in src
    assert "Math.Abs(fillPrice - decision.Decision.StopLoss)" in src
    assert "MaxEntryDistancePips" in src


def test_a_pending_order_is_re_priced_instead_of_being_left_behind():
    src = _src()
    assert 'if (action == "MODIFY_PENDING_ORDER")' in src
    assert "ModifyPendingOrder(order, target, slPips, tpPips, null)" in src
    assert 'JournalEvent("modify"' in src


def test_the_daily_loss_limit_counts_only_this_bot():
    assert "trade.Label == BotId" in _src()


def test_the_fill_journals_the_money_it_actually_risks():
    assert '["risk_money"]' in _src()


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


def test_ai_timeout_is_a_parameter_long_enough_for_a_thinking_reply():
    # a full MTF reply from Claude Sonnet 5 with thinking took ~69 s; 45 s dropped every decision
    src = _src()
    assert '[Parameter("AI timeout (s)", Group = "API", DefaultValue = 120' in src
    assert "TimeSpan.FromSeconds(AiTimeoutSeconds)" in src
    assert "TimeSpan.FromSeconds(45)" not in src
