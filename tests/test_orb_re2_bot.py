"""
OrbRe2Bot: the tick-break opening-range-breakout cBot, its wiring into the Backtest page, and
the rule invariants ported from the Pine study "ORB VN30F v2 - tick break + lọc trend"
(shorttitle ORB-RE2) and its Python reference (dnse-kash: src/dnse_kash/bots/orb_re2_bot.py).

The cBot is compiled by the installer container, not by pytest, so the rule invariants below are
pinned against the source the same way tests/test_orb_bot.py and tests/test_turtle_bot.py pin the
other cBots.

RE2 is not "OrbBot with different numbers": it changes the *timing* of the entry (a tick break
instead of a closed-bar confirm) and adds a DAILY trend gate. Those two are the whole point of the
variant, so they get the most assertions.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_command as bc  # noqa: E402
from app.backtest_api import BacktestCreate, max_span_days, validate_run_config  # noqa: E402
from app.risk_limits import strategy_of  # noqa: E402

SOURCE = (ROOT / "cBot" / "OrbRe2Bot.cs").read_text(encoding="utf-8")

ACCOUNT = {"slug": "demo-orb-re2", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-orb-re2_pwd"}
BOT = "cbot-demo-orb-re2-usdjpy"

# One container, one symbol, an M1 chart: the OR window is a 5-minute wall-clock window, so a chart
# wider than the window would swallow post-window movement inside its own bar (see the cBot header).
RUN_COMMAND = (
    "docker run -d --name cbot-demo-orb-re2-usdjpy --restart unless-stopped --network host "
    "-v /home/forge/AgentFxTrading:/workspace -v /home/forge/ctrader:/root "
    f"{bc.DEFAULT_IMAGE} run /workspace/cBot/OrbRe2Bot.algo "
    "--ctid=trader@example.com --pwd-file=/root/ctrader_data/ctid_demo-orb-re2_pwd "
    f"--account=10115236 --symbol=USDJPY --period=m1 --full-access --BotId={BOT} "
    "--OpeningRangeMinutes=5 --SessionOpenHourWinterUtc=0"
)


@pytest.fixture
def homes(tmp_path):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "OrbRe2Bot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_demo-orb-re2_pwd").write_text("s3cret-password")
    return project, ctrader


def orb_re2_job(**over):
    job = {"id": 12, "strategy": "orb_re2", "algo": "OrbRe2Bot.algo", "ctid_email": "trader@example.com",
           "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-orb-re2_pwd",
           "symbol": "USDJPY", "period": "m1", "start_date": "2025-09-26", "end_date": "2026-09-25",
           "data_mode": "ticks", "spread_pips": None, "balance": 10000.0,
           "params": {"BotId": BOT, "OpeningRangeMinutes": "5", "SessionOpenHourWinterUtc": "0"}}
    job.update(over)
    return job


# --- wiring into the backtest page -------------------------------------------------------

def test_the_algo_is_a_supported_backtest_strategy():
    assert bc.STRATEGY_BY_ALGO["OrbRe2Bot.algo"] == "orb_re2"
    assert "orb_re2" in bc.SUPPORTED_STRATEGIES


def test_an_orb_re2_container_is_a_supported_source(homes):
    project, ctrader = homes
    row = bc.describe_source(BOT, RUN_COMMAND, project, ctrader)
    assert row == {"name": BOT, "strategy": "orb_re2", "symbol": "USDJPY", "period": "m1",
                   "account_label": "", "supported": True, "reason": ""}


def test_an_unbuilt_orb_re2_algo_is_reported_as_such(homes):
    project, ctrader = homes
    (project / "cBot" / "OrbRe2Bot.algo").unlink()
    assert bc.describe_source(BOT, RUN_COMMAND, project, ctrader)["reason"] == "OrbRe2Bot.algo is not built"


def test_locked_params_switch_the_reporting_endpoints_off():
    # A locked KeyError would surface as a 500 on the Backtest page, so the strategy must have an
    # entry here and not merely a name in STRATEGY_BY_ALGO.
    assert bc.locked_params("orb_re2", 12) == {"BotId": "bt-12", "AccountLabel": "backtest",
                                               "ApiUrl": bc.DISABLED_URL, "AiReportUrl": bc.DISABLED_URL}


def test_the_backtest_spec_runs_an_m1_chart_on_bridge_network(homes):
    project, ctrader = homes
    spec = bc.build_container_spec(orb_re2_job(), project, ctrader)
    command = spec["command"]

    # Same reason as the other bots: on --network host a backtest reaches 127.0.0.1:8000 and books
    # fake trades and ticks into production.
    assert spec["network_mode"] == "bridge" and spec["name"] == "bt-12"
    assert "--symbol=USDJPY" in command and "--period=m1" in command
    assert "--start=26/09/2025" in command and "--end=25/09/2026" in command
    assert command[-len(bc.locked_params("orb_re2", 12)):] == [
        f"--{k}={v}" for k, v in bc.locked_params("orb_re2", 12).items()]
    assert f"--BotId={BOT}" not in command and "--BotId=bt-12" in command
    assert "s3cret-password" not in " ".join(command)


def test_an_orb_re2_backtest_is_capped_at_one_year_like_the_other_intraday_bots():
    one_year = BacktestCreate(bot_name=BOT, start=date(2025, 9, 26), end=date(2026, 9, 25))
    validate_run_config(one_year, date(2026, 9, 27), "m1")                      # no raise
    assert max_span_days("m1") == bc.MAX_SPAN_DAYS == 365

    two_years = BacktestCreate(bot_name=BOT, start=date(2024, 9, 26), end=date(2026, 9, 25))
    with pytest.raises(HTTPException):
        validate_run_config(two_years, date(2026, 9, 27), "m1")


def test_orb_re2_shares_the_intraday_risk_bucket():
    # RE2 holds hours, not weeks: the intraday layers sized for session stops fit it, so it does not
    # get TurtleBot's separate bucket.
    assert strategy_of("cbot-demo-orb-re2-usdjpy") == "tms"


# --- the opening range --------------------------------------------------------------------

def test_the_or_window_is_minutes_from_the_session_open():
    assert "private DateTime OrWindowEndUtc(DateTime sessionOpen) => sessionOpen.AddMinutes(OpeningRangeMinutes);" in SOURCE
    # Wall-clock window, not a candle body: bars are judged by their open time against [open, end).
    assert "DateTime open = SessionOpenUtc(bar.OpenTime);" in SOURCE
    assert "if (bar.OpenTime < open) return;" in SOURCE
    assert "if (bar.OpenTime >= end) return;" in SOURCE


def test_the_window_can_be_closed_by_the_bar_that_opens_after_it():
    # If no tick happens to land exactly on the boundary, the next bar open still ends the window.
    assert ("if (!_orFinalized && closed.OpenTime >= OrWindowEndUtc(SessionOpenUtc(closed.OpenTime)))\n"
            "                FinalizeOr();" in SOURCE)


def test_a_narrow_range_skips_the_session_instead_of_trading_noise():
    assert "double widthPips = (_orHigh - _orLow) / Symbol.PipSize;" in SOURCE
    assert "_orValid = widthPips >= MinOrWidthPips;" in SOURCE
    assert "if (!_orValid || _eodFlattened) return;" in SOURCE


def test_a_range_missed_while_the_bot_was_down_is_rebuilt_from_history():
    # A cold start or a watchdog restart after 09:05 must not cost the session.
    assert "private void BackfillOrFromHistory()" in SOURCE
    assert "if (!_orHadWindow) BackfillOrFromHistory();" in SOURCE


# --- the tick break (the alpha) -----------------------------------------------------------

def test_the_breakout_is_a_live_tick_break_not_a_closed_bar():
    # This is what separates RE2 from OrbBot: the signal is decided on quotes, in OnTick.
    assert "protected override void OnTick()" in SOURCE
    assert "EvaluateEntry(now);" in SOURCE
    assert "double ask = Symbol.Ask;" in SOURCE
    assert "double bid = Symbol.Bid;" in SOURCE
    assert "bool longTrigger = ask >= _orHigh + buffer;" in SOURCE
    assert "bool shortTrigger = bid <= _orLow - buffer;" in SOURCE
    # ...and the trigger is never gated on a closed bar's Close.
    assert "closed.Close > _orHigh" not in SOURCE


def test_the_tick_buffer_keeps_a_mere_touch_from_filling():
    assert "double buffer = TickBufferPips * Symbol.PipSize;" in SOURCE
    assert '[Parameter("Tick Break Buffer (pips)", Group = "Opening Range", DefaultValue = 0.0, MinValue = 0.0)]' in SOURCE


def test_a_narrow_range_that_triggers_both_sides_resolves_long_first():
    # One quote can satisfy both sides when the range is narrower than twice the buffer; the long
    # side wins, exactly as OrbBot resolves it.
    assert "if (longTrigger && allowLong) { side = TradeType.Buy; price = ask; }" in SOURCE
    assert "else { side = TradeType.Sell; price = bid; }" in SOURCE


def test_a_stale_quote_cannot_trade_a_weekend_or_holiday_gap():
    assert "private bool HasFreshQuote(DateTime now) => (now - _lastTickAt).TotalSeconds <= MaxQuoteAgeSeconds;" in SOURCE
    assert "if (!HasFreshQuote(now)) return;" in SOURCE


# --- the daily trend filter ---------------------------------------------------------------

def test_the_daily_references_come_only_from_closed_bars():
    # Index Count-1 is the daily bar the current session is still building; reading it would be
    # look-ahead. Both the closes and the ATR series start one bar further back.
    assert "for (int i = dBars.Count - 2; i >= 0 && values.Count < maxBars; i--)" in SOURCE
    assert "for (int i = dBars.Count - 2; i >= 1 && trs.Count < want; i--)" in SOURCE
    # The live daily bar is only ever touched through index Count-1, never read as a value.
    assert "dBars.ClosePrices[dBars.Count - 1]" not in SOURCE


def test_the_daily_references_are_read_once_per_session():
    assert "private void EnsureDailyReferences()" in SOURCE
    assert "if (_dailyRefDate == _sessionDate) return;" in SOURCE


def test_the_trend_filter_gates_each_side_separately():
    assert "private bool TrendAllows(TradeType side, double price)" in SOURCE
    # price vs MA
    assert "return isLong ? price > _maFast + buffer : price < _maFast - buffer;" in SOURCE
    # fast MA vs slow MA
    assert "return isLong ? _maFast > _maSlow + buffer : _maFast < _maSlow - buffer;" in SOURCE
    # A blocked break is refused, not silently flipped to the other side.
    assert "if (!TrendAllows(side, price))" in SOURCE


def test_the_trend_filter_defaults_to_price_vs_ema10_daily():
    assert '[Parameter("Trend Filter Mode", Group = "Trend Filter", DefaultValue = OrbRe2TrendFilterMode.PriceVsMa)]' in SOURCE
    assert '[Parameter("MA Length (fast)", Group = "Trend Filter", DefaultValue = 10, MinValue = 1, MaxValue = 400)]' in SOURCE
    assert '[Parameter("MA Type", Group = "Trend Filter", DefaultValue = OrbRe2MaType.Ema)]' in SOURCE


def test_a_missing_ma_allows_the_side_but_a_missing_atr_does_not():
    # The reference only refuses an entry when the ATR is missing, not when the MA is.
    assert "if (double.IsNaN(_maFast)) return true;" in SOURCE
    assert "if (double.IsNaN(_maFast) || double.IsNaN(_maSlow)) return true;" in SOURCE
    assert "if (!(_atrPips > 0))" in SOURCE
    assert "BREAK blocked — not enough daily bars for ATR{DailyAtrPeriod}(D-1)" in SOURCE


def test_the_trend_block_is_logged_once_per_session_per_side():
    # Pine's "chặn L"/"chặn S" labels are worth seeing once, not on every tick of the session.
    assert "private readonly HashSet<string> _trendBlockLogged = new HashSet<string>();" in SOURCE
    assert "if (!_trendBlockLogged.Add(key)) return;" in SOURCE
    assert "_trendBlockLogged.Clear();" in SOURCE


def test_the_ema_is_seeded_like_pines_ta_ema():
    # SMA(length) seed, then alpha = 2/(length+1) — not a recursive EMA from the first bar of history.
    assert "double seed = 0.0;" in SOURCE
    assert "double ema = seed / length;" in SOURCE
    assert "double alpha = 2.0 / (length + 1.0);" in SOURCE
    assert "for (int i = length; i < values.Count; i++) ema = alpha * values[i] + (1.0 - alpha) * ema;" in SOURCE


def test_all_three_ma_types_are_implemented():
    assert "case OrbRe2MaType.Sma: return SmaOf(closes, length);" in SOURCE
    assert "case OrbRe2MaType.Wma: return WmaOf(closes, length);" in SOURCE
    assert "default: return EmaOf(closes, length);" in SOURCE


# --- risk: the daily ATR stop and the R-multiple target -----------------------------------

def test_the_stop_is_a_multiple_of_the_previous_sessions_atr_and_the_target_is_r_multiples():
    assert "double slPips = SlAtrDailyMultiplier * _atrPips;" in SOURCE
    assert "double tpPips = slPips * TpRMultiple;" in SOURCE
    assert '[Parameter("SL = x * ATR14(D-1)", Group = "Risk", DefaultValue = 0.965, MinValue = 0.05, MaxValue = 10.0, Step = 0.005)]' in SOURCE
    assert '[Parameter("Take Profit (R multiple)", Group = "Risk", DefaultValue = 2.0, MinValue = 0.1, MaxValue = 20.0, Step = 0.1)]' in SOURCE


def test_the_atr_is_wilders_rma_over_the_daily_bars_ending_at_d_minus_1():
    assert "private double ComputeDailyAtrPips()" in SOURCE
    assert "atr = ((DailyAtrPeriod - 1) * atr + trs[i]) / DailyAtrPeriod;" in SOURCE
    assert "if (dBars == null || dBars.Count < DailyAtrPeriod + 2) return 0.0;" in SOURCE


def test_both_brackets_are_placed_with_the_order_so_they_fill_on_the_tick():
    assert "ExecuteMarketOrder(side, SymbolName, volume, BotId, slPips, tpPips, comment);" in SOURCE
    # RE2 places market orders with brackets, so there is nothing left to cancel at the flatten.
    assert "CancelAllOrders" not in SOURCE


def test_risk_sizing_and_a_fixed_lot_are_both_available():
    assert '[Parameter("Risk Sizing Enabled", Group = "Risk", DefaultValue = false)]' in SOURCE
    # Pine's single contract is the cTrader minimum lot, so the default stays FixedLots = 0.
    assert "units = FixedLots > 0" in SOURCE
    assert "double riskAmount = Account.Balance * (RiskPerTradePercent / 100.0);" in SOURCE
    assert "units = Symbol.NormalizeVolumeInUnits(riskAmount / perUnitRisk, RoundingMode.Down);" in SOURCE


def test_a_minimum_lot_over_the_dollar_cap_refuses_the_entry():
    assert 'refusal = $"minimum lot' in SOURCE
    assert "Max Dollar Risk" in SOURCE
    # Sizing up to reach a risk percentage is exactly the failure the cap exists to prevent.
    assert "Math.Max(units" not in SOURCE


def test_a_wide_spread_skips_the_signal():
    assert "double spreadPips = (ask - bid) / Symbol.PipSize;" in SOURCE
    assert "if (spreadPips > MaxSpreadPips)" in SOURCE


# --- session discipline -------------------------------------------------------------------

def test_one_position_and_one_signal_per_session_by_default():
    assert "if (_tradesFired >= MaxTradesPerSession) return;" in SOURCE
    assert "if (_blockedAfterStop && !AllowReentryAfterSl) return;" in SOURCE
    assert "if (Positions.FindAll(BotId, SymbolName).Length > 0) return;" in SOURCE
    assert '[Parameter("Max Trades per Session", Group = "Session", DefaultValue = 1, MinValue = 1, MaxValue = 10)]' in SOURCE
    assert '[Parameter("Allow Re-entry After Stop", Group = "Session", DefaultValue = false)]' in SOURCE


def test_a_stop_arms_the_no_re_entry_flag():
    assert "if (args.Reason == PositionCloseReason.StopLoss) _blockedAfterStop = true;" in SOURCE


def test_nothing_new_opens_after_the_entry_cutoff():
    assert "if (now >= EntryCutoffUtc(now)) return;" in SOURCE
    assert '[Parameter("Entry Cutoff Hour (Winter UTC)", Group = "Session", DefaultValue = 7, MinValue = 0, MaxValue = 23)]' in SOURCE


def test_the_session_is_flattened_at_eod_even_after_a_restart():
    assert "if (_sessionDate != DateTime.MinValue && !_eodFlattened && now >= EodFlattenUtc(now))" in SOURCE
    assert 'FlattenAll("End of session");' in SOURCE
    # OnStart closes what a restart left open past the flatten hour, without waiting for a bar.
    assert "FlattenIfPastEod();" in SOURCE
    assert "if (Server.Time < EodFlattenUtc(Server.Time)) return;" in SOURCE


def test_the_session_clock_supports_the_us_and_europe_dst_calendars():
    assert "int hour = isDst ? winterHour - 1 : winterHour;" in SOURCE
    assert "DateTime start = NthSunday(utc.Year, 3, 2).AddHours(7);" in SOURCE
    assert "DateTime end = NthSunday(utc.Year, 11, 1).AddHours(6);" in SOURCE
    assert "DateTime start = LastSunday(utc.Year, 3).AddHours(1);" in SOURCE
    assert "DateTime end = LastSunday(utc.Year, 10).AddHours(1);" in SOURCE
    # Vietnam has no DST, so the reference session is the default.
    assert '[Parameter("DST Rule", Group = "Session", DefaultValue = OrbRe2DstRule.None)]' in SOURCE


def test_the_direction_switch_can_restrict_the_bot_to_one_side():
    assert "bool allowLong = Direction != OrbRe2Direction.ShortOnly;" in SOURCE
    assert "bool allowShort = Direction != OrbRe2Direction.LongOnly;" in SOURCE
    assert '[Parameter("Trade Direction", Group = "Session", DefaultValue = OrbRe2Direction.Both)]' in SOURCE


# --- purity and reporting -----------------------------------------------------------------

def test_the_bot_is_pure_rules_with_no_ai_or_news_layer():
    for forbidden in ("UseAiGateMode", "AiTelemetryUrl", "EnableNewsFilter", "TelegramBotToken",
                      "CommandPollMs", "OpenAiApiKey"):
        assert forbidden not in SOURCE


def test_reporting_is_live_only_so_a_backtest_never_books_trades():
    assert SOURCE.count("if (RunningMode != RunningMode.RealTime) return;") >= 3
    assert SOURCE.count("_httpClient.") == 1


def test_the_open_report_carries_the_re2_context_for_the_journal():
    for field in ("or_high = ", "or_low = ", "or_width_pips = ", "atr_daily_pips = ",
                  "sl_atr_multiple = ", "tp_r_multiple = ", "tick_buffer_pips = ",
                  "trend_filter = ", "ma_fast = ", "ma_slow = "):
        assert field in SOURCE
