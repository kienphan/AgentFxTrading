"""
OrbRE2Bot: the cTrader port of dnse-kash's ``orb_re2_bot.py`` (Pine "ORB VN30F v2").

The cBot is compiled by the cTrader console container, not by pytest, so the contract is pinned
against the source the same way tests/test_orb_bot.py pins OrbBot. The tests cover the pieces
that separate RE2 from the shipped OrbBot — an entry that fires on the TICK instead of a closed
bar, the daily trend filter, the whipsaw guard, the ATR band and the anti-extension gate — plus
the OR/SL/TP plumbing it inherits in spirit from OrbBot.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "cBot" / "OrbRE2Bot.cs").read_text(encoding="utf-8")

# Header comments cite the Python reference and explain the deltas; only executable lines matter
# for the "no closed-bar entry" and ordering checks.
CODE = "\n".join(line for line in SOURCE.splitlines() if not line.lstrip().startswith("//"))


def _idx(needle: str) -> int:
    pos = SOURCE.find(needle)
    assert pos >= 0, needle
    return pos


# --- the tick entry path (the headline difference from OrbBot) ----------------------------

def test_entry_fires_on_ticks_not_on_a_closed_bar():
    assert "protected override void OnTick()" in SOURCE
    assert "TryEnterOnTick();" in SOURCE
    # OrbBot's closed-bar confirm path must not exist here at all.
    assert "EvaluateBreakout" not in SOURCE
    assert "closed.Close > _orHigh" not in SOURCE
    assert "closed.Close < _orLow" not in SOURCE


def test_the_tick_stop_sits_outside_the_boundary_by_the_buffer():
    assert "double longTrig = _orHigh + TickBufferPips * Symbol.PipSize;" in SOURCE
    assert "double shortTrig = _orLow - TickBufferPips * Symbol.PipSize;" in SOURCE
    assert "bool brokeLong = price >= longTrig;" in SOURCE
    assert "bool brokeShort = price <= shortTrig;" in SOURCE


def test_the_or_window_must_be_finished_before_a_tick_can_enter():
    assert "if (!_orFinalized || !_orValid) return;" in SOURCE
    assert "if (now < OrWindowEndUtc(sessionOpen)) return;" in SOURCE


# --- whipsaw guard -------------------------------------------------------------------

def test_the_opposite_break_is_recorded_before_any_gate():
    # A refused breakout still counts: the guard has to know OR_low was pierced even when the
    # trend filter blocked that attempt (the Python bot's documented 20/10 failure mode).
    assert "bool whipsawLong = RecentBreak(_brokenLowAt, now);" in SOURCE
    assert "bool whipsawShort = RecentBreak(_brokenHighAt, now);" in SOURCE
    assert "if (brokeLong) _brokenHighAt = now;" in SOURCE
    assert "if (brokeShort) _brokenLowAt = now;" in SOURCE
    assert _idx("if (brokeLong) _brokenHighAt = now;") < _idx("RefreshTrend(price);")
    assert _idx("if (brokeShort) _brokenLowAt = now;") < _idx("RefreshTrend(price);")


def test_the_whipsaw_guard_blocks_that_side_only():
    assert "bool blockLong = WhipsawGuardEnabled && whipsawLong;" in SOURCE
    assert "bool blockShort = WhipsawGuardEnabled && whipsawShort;" in SOURCE
    assert 'if (brokeLong && allowLong && !blockLong)' in SOURCE
    assert 'if (brokeShort && allowShort && !blockShort)' in SOURCE


def test_the_whipsaw_window_zero_means_the_whole_session():
    assert 'if (WhipsawWindowMinutes <= 0) return true;' in SOURCE
    assert "return (now - at.Value).TotalSeconds <= WhipsawWindowMinutes * 60.0;" in SOURCE


def test_the_whipsaw_guard_is_on_by_default():
    assert '[Parameter("Whipsaw Guard", Group = "Whipsaw", DefaultValue = true)]' in SOURCE


# --- daily trend filter ----------------------------------------------------------------

def test_the_trend_modes_match_the_python_reference():
    assert "public enum Re2TrendMode" in SOURCE
    assert "Off = 0," in SOURCE and "PriceVsMa = 1," in SOURCE and "MaCross = 2" in SOURCE
    assert 'if (TrendFilterMode == Re2TrendMode.Off)' in SOURCE
    assert 'if (TrendFilterMode == Re2TrendMode.PriceVsMa)' in SOURCE


def test_price_vs_ma_uses_the_buffer_on_both_sides():
    assert "_trendUp = price > _maFast.Value + buffer;" in SOURCE
    assert "_trendDown = price < _maFast.Value - buffer;" in SOURCE


def test_ma_cross_compares_fast_against_slow_with_the_same_buffer():
    assert "_trendUp = _maFast.Value > _maSlow.Value + buffer;" in SOURCE
    assert "_trendDown = _maFast.Value < _maSlow.Value - buffer;" in SOURCE


def test_a_breakout_against_the_daily_trend_is_refused_and_logged_once():
    assert "if (_trendUp) { TryOpen(TradeType.Buy, longTrig); return; }" in SOURCE
    assert "if (_trendDown) { TryOpen(TradeType.Sell, shortTrig); return; }" in SOURCE
    assert "if (!_trendBlockLongLogged)" in SOURCE
    assert "if (!_trendBlockShortLogged)" in SOURCE
    assert "but the trend filter refuses" in SOURCE


def test_only_closed_daily_bars_feed_the_ma_and_the_atr():
    # The forming daily bar is skipped, and any bar belonging to today's session date is
    # excluded, so the filter can never look ahead.
    assert "for (int i = daily.Count - 2; i >= 0; i--)" in SOURCE
    assert "if (_sessionDate != DateTime.MinValue && t.Date >= _sessionDate) continue;" in SOURCE


def test_the_ema_is_seeded_with_the_sma_like_pine():
    assert "double alpha = 2.0 / (length + 1.0);" in SOURCE
    assert "ema /= length;" in SOURCE
    assert "ema = (values[i] - ema) * alpha + ema;" in SOURCE


def test_all_three_moving_average_types_exist():
    assert "case Re2MaType.Sma:" in SOURCE
    assert "case Re2MaType.Wma:" in SOURCE
    assert "double denom = length * (length + 1) / 2.0;" in SOURCE


# --- ATR band and anti-extension --------------------------------------------------------

def test_the_daily_atr_band_skips_dead_and_blow_off_days():
    assert "if (MinAtrPips > 0 && _dailyAtrPips < MinAtrPips)" in SOURCE
    assert "if (MaxAtrPips > 0 && _dailyAtrPips > MaxAtrPips)" in SOURCE
    assert "private bool DailyAtrOutOfBand()" in SOURCE


def test_price_already_stretched_from_the_ma_is_refused():
    assert "double limit = MaxMaDistanceAtrMult * atrPrice;" in SOURCE
    assert "double distance = Math.Abs(price - _maFast.Value);" in SOURCE
    assert "if (distance <= limit) return false;" in SOURCE
    assert "pullback risk" in SOURCE


def test_no_entry_without_a_daily_atr():
    assert "if (_dailyAtrPips <= 0)" in SOURCE
    assert "Entry refused — daily ATR" in SOURCE


# --- risk plumbing ---------------------------------------------------------------------

def test_the_stop_is_the_daily_atr_multiple_floored_by_min_sl():
    assert "double rawSlPips = _dailyAtrPips > 0" in SOURCE
    assert ": DefaultSlPips;" in SOURCE
    assert "double slPips = Math.Max(rawSlPips, MinSlPips);" in SOURCE
    assert "double rawSlPips = _dailyAtrPips > 0" in SOURCE


def test_the_take_profit_is_an_r_multiple_of_the_stop():
    assert "? slPips * TakeProfitRMultiple" in SOURCE
    assert ': (TakeProfitPips > 0 ? TakeProfitPips : (double?)null);' in SOURCE


def test_spot_gold_style_defaults_are_not_assumed():
    # RE2 targets FX/index CFDs: the gold-sized 300-pip floor must not be inherited.
    assert '[Parameter("Min Stop (pips)", Group = "Risk", DefaultValue = 10.0, MinValue = 0.0)]' in SOURCE
    assert '[Parameter("SL Daily ATR Multiplier", Group = "Risk", DefaultValue = 0.5' in SOURCE
    assert '[Parameter("Take Profit (R multiple, 0=off)", Group = "Risk", DefaultValue = 2.0' in SOURCE


# --- session discipline ----------------------------------------------------------------

def test_one_position_and_one_signal_per_session():
    # The guard covers every label this instance owns, adopted ones included.
    assert "if (MyPositions().Length > 0) return;" in SOURCE
    assert "if (_tradesFired >= MaxTradesPerSession) return;" in SOURCE
    assert "if (_blockedAfterStop && !AllowReentryAfterSl) return;" in SOURCE


def test_the_entry_window_and_the_eod_flatten_both_cut_entries_off():
    assert "if (MaxEntryWindowMinutes > 0 && (now - sessionOpen).TotalMinutes > MaxEntryWindowMinutes) return;" in SOURCE
    assert "if (now >= EodFlattenUtc(now)) return;" in SOURCE
    assert 'FlattenAll("End of session");' in SOURCE


def test_a_narrow_opening_range_skips_the_session():
    assert "double widthPips = (_orHigh - _orLow) / Symbol.PipSize;" in SOURCE
    assert "_orValid = widthPips >= MinOrWidthPips;" in SOURCE


def test_a_range_missed_while_the_bot_was_down_is_rebuilt_from_history():
    assert "private void BackfillOrFromHistory()" in SOURCE
    assert "if (!_orHadWindow) BackfillOrFromHistory();" in SOURCE


def test_the_session_roll_over_resets_the_whipsaw_memory():
    for reset in ("_brokenHighAt = null;", "_brokenLowAt = null;",
                  "_whipsawLongLogged = false;", "_whipsawShortLogged = false;",
                  "_trendBlockLongLogged = false;", "_trendBlockShortLogged = false;"):
        assert reset in SOURCE, reset


# --- reporting contract ----------------------------------------------------------------

def test_the_dashboard_contract_is_kept():
    assert 'action = "sync"' in SOURCE
    assert 'action = "open"' in SOURCE
    assert 'action = "close"' in SOURCE
    assert 'action = "partial_close"' in SOURCE
    assert "entry_indicators = JsonSerializer.Serialize(new" in SOURCE
    for key in ("trend_mode = TrendFilterMode.ToString()", "daily_atr_pips = Math.Round(_dailyAtrPips, 1)",
                "or_width_pips = Math.Round((_orHigh - _orLow) / Symbol.PipSize, 1)",
                "whipsaw_guard = WhipsawGuardEnabled"):
        assert key in SOURCE, key


def test_backtests_do_not_post_to_the_live_endpoints():
    # Same guard as the other cBots: reporting is skipped outside RealTime running mode.
    # Five bare guards (four reporters + the reconciliation sweep); the partial-close path folds
    # the guard into one condition together with its null check.
    assert SOURCE.count("if (RunningMode != RunningMode.RealTime) return;") == 5
    assert "if (RunningMode != RunningMode.RealTime || position == null) return;" in SOURCE


# --- adopted labels (renaming a bot must not orphan its live position) -------------------

def test_adopted_labels_are_a_parameter_and_parsed_on_start():
    assert '[Parameter("Adopted Bot IDs (comma separated)", Group = "General", DefaultValue = "")]' in SOURCE
    assert "public string AdoptedBotIds { get; set; }" in SOURCE
    assert 'foreach (var part in (AdoptedBotIds ?? string.Empty).Split(\',\'))' in SOURCE
    assert "if (label.Length > 0 && label != BotId) _adoptedLabels.Add(label);" in SOURCE


def test_the_position_helper_never_calls_itself():
    start = SOURCE.index("private Position[] MyPositions()")
    body = SOURCE[SOURCE.index("{", start):]
    body = body[:body.index("\n        }\n")]
    assert "Positions.FindAll(BotId, SymbolName)" in body
    assert "MyPositions()" not in body, "the helper must query Positions directly or it recurses forever"


def test_every_position_query_goes_through_the_helper():
    # Entry guard, flush, breakeven and trailing must all see adopted positions.
    uses = SOURCE.count("MyPositions()")
    assert uses >= 6, uses
    assert "if (MyPositions().Length > 0) return;" in SOURCE          # entry guard
    assert "foreach (var position in MyPositions())" in SOURCE          # EOD flatten
    assert SOURCE.count("foreach (var pos in MyPositions())") == 2      # breakeven + trailing
    assert "var positions = MyPositions();" in SOURCE                   # telemetry


def test_the_close_handler_accepts_adopted_labels():
    assert "if (!ManagesLabel(position.Label)) return;" in SOURCE
    assert "private bool ManagesLabel(string label) =>" in SOURCE


def test_reports_carry_the_positions_own_label():
    # The server matches a close by bot_id + ctrader_id, so an adopted position must be reported
    # under the label its row was opened with, not under the new bot id.
    assert SOURCE.count('string owner = string.IsNullOrEmpty(position.Label) ? BotId : position.Label;') == 2
    assert "bot_id = owner," in SOURCE


def test_telemetry_is_published_per_label():
    # One live snapshot per bot id: an adopted position also refreshes the old label's cache.
    assert "PostTelemetry(BotId, positions.Where(" in SOURCE
    assert "PostTelemetry(label, adopted);" in SOURCE
    assert "bot_id = label," in SOURCE

# --- startup reconciliation of deals booked while the bot was down -----------------------

def test_closed_deals_are_reconciled_on_start():
    # A position that closes during a restart raises no PositionClosed event: the row would stay
    # "open" on the dashboard forever (US30 398836070, 2026-11-04 02:48:55Z).
    assert ('[Parameter("Reconcile Closed Deals (hours, 0=off)", Group = "Reporting", '
            'DefaultValue = 24, MinValue = 0, MaxValue = 168)]') in SOURCE
    assert "public int ReconcileClosedHours { get; set; }" in SOURCE
    assert "ReconcileClosedDealsFromHistory();" in SOURCE
    assert "if (ReconcileClosedHours <= 0) return;" in SOURCE
    assert "if (RunningMode != RunningMode.RealTime) return;" in SOURCE


def test_reconciliation_reports_only_real_final_closes():
    # An open position id means a partial close: reporting it would close the row early.
    assert "if (openIds.Contains(deal.PositionId)) continue;" in SOURCE
    assert "if (!seen.Add(deal.PositionId)) continue;" in SOURCE
    assert "if (deal.ClosingTime.ToUniversalTime() < since) continue;" in SOURCE
    assert "if (!string.Equals(deal.SymbolName, SymbolName, StringComparison.OrdinalIgnoreCase)) continue;" in SOURCE


def test_reconciliation_uses_the_brokers_own_numbers():
    assert "reason = \"Reconciled from broker history (bot was down at close)\"" in SOURCE
    assert "ctrader_id = deal.PositionId," in SOURCE
    assert "pnl = Math.Round(deal.NetProfit, 2)," in SOURCE
    assert "exit_price = deal.ClosingPrice," in SOURCE
    assert "exit_time = deal.ClosingTime.ToUniversalTime().ToString(\"o\")," in SOURCE

def test_adopted_labels_are_reconciled_too():
    assert "ManagesLabel(deal.Label)" in SOURCE
