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
    assert "if (Positions.FindAll(BotId, SymbolName).Length > 0) return;" in SOURCE
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
    # Four reporters return early on the bare guard; the partial-close path folds it into one
    # condition together with its null check.
    assert SOURCE.count("if (RunningMode != RunningMode.RealTime) return;") == 4
    assert "if (RunningMode != RunningMode.RealTime || position == null) return;" in SOURCE
