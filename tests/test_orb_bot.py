"""
OrbBot: the standalone opening-range-breakout cBot, its wiring into the Backtest page, and the
rule invariants ported from the dnse-kash reference (src/dnse_kash/bots/orb_bot.py).

The cBot is compiled by the installer container, not by pytest, so the rule invariants below are
pinned against the source the same way tests/test_turtle_bot.py and
tests/test_dashboard_sltp_and_close_reason.py pin the other cBots.

Background for the two "previous session's OR" switches: research/orb_prevday/SUMMARY.md.
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

SOURCE = (ROOT / "cBot" / "OrbBot.cs").read_text(encoding="utf-8")

ACCOUNT = {"slug": "demo-orb", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-orb_pwd"}
BOT = "cbot-demo-orb-xauusd"

# One container, one symbol, an m15 chart: the OR window is a wall-clock window, so the chart has
# to resolve 15 minutes.
RUN_COMMAND = (
    "docker run -d --name cbot-demo-orb-xauusd --restart unless-stopped --network host "
    "-v /home/forge/AgentFxTrading:/workspace -v /home/forge/ctrader:/root "
    f"{bc.DEFAULT_IMAGE} run /workspace/cBot/OrbBot.algo "
    "--ctid=trader@example.com --pwd-file=/root/ctrader_data/ctid_demo-orb_pwd "
    f"--account=10115236 --symbol=XAUUSD --period=m15 --full-access --BotId={BOT} "
    "--OpeningRangeMinutes=15 --SessionOpenHourWinterUtc=13"
)


@pytest.fixture
def homes(tmp_path):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "OrbBot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_demo-orb_pwd").write_text("s3cret-password")
    return project, ctrader


def orb_job(**over):
    job = {"id": 11, "strategy": "orb", "algo": "OrbBot.algo", "ctid_email": "trader@example.com",
           "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-orb_pwd",
           "symbol": "XAUUSD", "period": "m15", "start_date": "2025-09-26", "end_date": "2026-09-25",
           "data_mode": "ticks", "spread_pips": None, "balance": 10000.0,
           "params": {"BotId": BOT, "OpeningRangeMinutes": "15", "SessionOpenHourWinterUtc": "13"}}
    job.update(over)
    return job


# --- wiring into the backtest page -------------------------------------------------------

def test_the_algo_is_a_supported_backtest_strategy():
    assert bc.STRATEGY_BY_ALGO["OrbBot.algo"] == "orb"
    assert "orb" in bc.SUPPORTED_STRATEGIES


def test_an_orb_container_is_a_supported_source(homes):
    project, ctrader = homes
    row = bc.describe_source(BOT, RUN_COMMAND, project, ctrader)
    assert row == {"name": BOT, "strategy": "orb", "symbol": "XAUUSD", "period": "m15",
                   "account_label": "", "supported": True, "reason": ""}


def test_an_unbuilt_orb_algo_is_reported_as_such(homes):
    project, ctrader = homes
    (project / "cBot" / "OrbBot.algo").unlink()
    assert bc.describe_source(BOT, RUN_COMMAND, project, ctrader)["reason"] == "OrbBot.algo is not built"


def test_locked_params_switch_the_reporting_endpoints_off():
    assert bc.locked_params("orb", 11) == {"BotId": "bt-11", "AccountLabel": "backtest",
                                           "ApiUrl": bc.DISABLED_URL, "AiReportUrl": bc.DISABLED_URL}


def test_the_backtest_spec_runs_an_m15_gold_chart_on_bridge_network(homes):
    project, ctrader = homes
    spec = bc.build_container_spec(orb_job(), project, ctrader)
    command = spec["command"]

    # Same reason as the other bots: on --network host a backtest reaches 127.0.0.1:8000 and books
    # fake trades and ticks into production.
    assert spec["network_mode"] == "bridge" and spec["name"] == "bt-11"
    assert "--symbol=XAUUSD" in command and "--period=m15" in command
    assert "--start=26/09/2025" in command and "--end=25/09/2026" in command
    assert command[-len(bc.locked_params("orb", 11)):] == [
        f"--{k}={v}" for k, v in bc.locked_params("orb", 11).items()]
    assert f"--BotId={BOT}" not in command and "--BotId=bt-11" in command
    assert "s3cret-password" not in " ".join(command)


def test_an_orb_backtest_is_capped_at_one_year_like_the_other_intraday_bots():
    one_year = BacktestCreate(bot_name=BOT, start=date(2025, 9, 26), end=date(2026, 9, 25))
    validate_run_config(one_year, date(2026, 9, 27), "m15")                     # no raise
    assert max_span_days("m15") == bc.MAX_SPAN_DAYS == 365

    two_years = BacktestCreate(bot_name=BOT, start=date(2024, 9, 26), end=date(2026, 9, 25))
    with pytest.raises(HTTPException):
        validate_run_config(two_years, date(2026, 9, 27), "m15")


def test_orb_shares_the_intraday_risk_bucket():
    # OrbBot holds hours, not weeks: the session layers sized for intraday stops fit it, so it does
    # not get TurtleBot's separate bucket.
    assert strategy_of("cbot-demo-orb-xauusd") == "tms"


# --- the opening range --------------------------------------------------------------------

def test_the_range_is_a_time_window_of_closed_bars_not_a_candle_body():
    assert "DateTime open = SessionOpenUtc(bar.OpenTime);" in SOURCE
    assert "DateTime end = OrWindowEndUtc(open);" in SOURCE
    assert "if (bar.OpenTime < open) return;" in SOURCE
    assert "if (bar.OpenTime >= end) { FinalizeOr(); return; }" in SOURCE
    # The window is minutes from the session open, never a fixed bar count.
    assert "=> sessionOpen.AddMinutes(OpeningRangeMinutes);" in SOURCE


def test_the_breakout_is_a_closed_bar_beyond_the_range_plus_the_buffer():
    assert "bool longConfirm = closed.Close > _orHigh + buffer;" in SOURCE
    assert "bool shortConfirm = closed.Close < _orLow - buffer;" in SOURCE
    # Signal work happens on the closed-bar event; OnTick only carries the end-of-day flatten.
    assert "var closed = Bars.Last(1);" in SOURCE


def test_a_narrow_range_skips_the_session_instead_of_trading_noise():
    assert "double widthPips = (_orHigh - _orLow) / Symbol.PipSize;" in SOURCE
    assert "_orValid = widthPips >= MinOrWidthPips;" in SOURCE
    assert "if (!_orFinalized || !_orValid) return;" in SOURCE


def test_a_range_missed_while_the_bot_was_down_is_rebuilt_from_history():
    # A restart (the watchdog) after the window closed must not cost the session.
    assert "private void BackfillOrFromHistory()" in SOURCE
    assert "if (!_orHadWindow) BackfillOrFromHistory();" in SOURCE


# --- the previous session's opening range (regime / bias) ---------------------------------

def test_the_previous_or_gate_blocks_only_the_wrong_side_of_a_gap():
    # LONG : prevOR_low  > todayOR_high  (today opened below yesterday)
    # SHORT: prevOR_high < todayOR_low   (today opened above yesterday)
    assert "return side == TradeType.Buy ? _prevOrLow > _orHigh : _prevOrHigh < _orLow;" in SOURCE
    assert "if (!PrevDayOrFilterEnabled) return false;" in SOURCE


def test_the_previous_or_stop_anchors_on_the_opposite_boundary_of_yesterday():
    assert "double reference = side == TradeType.Buy ? _prevOrLow : _prevOrHigh;" in SOURCE
    # A level on the wrong side of the entry is not a stop; the caller falls back to StopMode.
    assert "if (side == TradeType.Buy && reference >= entry) return null;" in SOURCE
    assert "if (side == TradeType.Sell && reference <= entry) return null;" in SOURCE
    assert "double? prevOrStop = PrevDayOrStopPrice(side, entry);" in SOURCE


def test_a_rejected_gap_costs_the_signal_not_the_session():
    # Both ranges are already fixed for the day, so the opposite direction may still trade.
    assert "RejectPrevDayOrGap(TradeType.Buy, closed.Close);" in SOURCE
    assert "still armed" in SOURCE
    # ...but the rejection is logged once per side per day, not once per bar.
    assert "if (ShowLogs && _prevOrBlockLogged.Add(key))" in SOURCE


def test_only_the_immediately_previous_session_qualifies():
    # A narrow range must not be reused: a stale level once put a stop hundreds of pips away.
    assert "_prevOrFresh = false;" in SOURCE
    assert "opening range under " in SOURCE
    # A session that never opened (weekend, holiday) keeps the reference rather than dropping it.
    assert "had no opening range (market shut)" in SOURCE
    assert "if (!_orHadWindow)" in SOURCE


def test_both_previous_or_switches_default_to_off():
    assert ('[Parameter("Prev-Day OR Gap Filter", Group = "Prev-Day OR", DefaultValue = false)]'
            in SOURCE)
    assert ('[Parameter("Prev-Day OR Stop", Group = "Prev-Day OR", DefaultValue = false)]'
            in SOURCE)
    assert "public bool PrevDayOrFilterEnabled { get; set; }" in SOURCE
    assert "public bool PrevDayOrSlEnabled { get; set; }" in SOURCE


# --- the two guardrails the backtest showed are not optional -------------------------------

def test_the_stop_is_floored_so_a_degenerate_level_cannot_decide_the_record():
    # One trade with the stop $0.34 away on gold produced +69R of a headline +71.5R result.
    assert "slPips = Math.Max(slPips, MinSlPips);" in SOURCE
    assert ('[Parameter("Min Stop (pips)", Group = "Risk", DefaultValue = 300.0, MinValue = 0.0)]'
            in SOURCE)


def test_there_is_no_take_profit_unless_one_is_asked_for():
    # Capping at 1R turned +45R into -82R over the same trades: ORB lives on its right tail.
    assert "double? tpPips = TakeProfitPips > 0 ? TakeProfitPips : (double?)null;" in SOURCE
    assert ('[Parameter("Take Profit (pips, 0=none)", Group = "Risk", DefaultValue = 0.0, MinValue = 0.0)]'
            in SOURCE)


# --- session discipline -------------------------------------------------------------------

def test_one_position_and_one_signal_per_session_by_default():
    assert "if (Positions.FindAll(BotId, SymbolName).Length > 0) return;" in SOURCE
    assert "if (_tradesFired >= MaxTradesPerSession) return;" in SOURCE
    assert "if (_blockedAfterStop && !AllowReentryAfterSl) return;" in SOURCE


def test_nothing_opens_after_the_session_ends():
    assert "if (_sessionInvalidated || _eodFlattened) return;" in SOURCE
    assert "if (closed.OpenTime >= EodFlattenUtc(closed.OpenTime)) return;" in SOURCE
    assert "FlattenAll(\"End of session\");" in SOURCE


def test_the_session_clock_follows_us_dst_like_the_other_session_bots():
    assert "DateTime start = NthSunday(utc.Year, 3, 2).AddHours(7);" in SOURCE
    assert "DateTime end = NthSunday(utc.Year, 11, 1).AddHours(6);" in SOURCE
    assert "int hour = isDst ? winterHour - 1 : winterHour;" in SOURCE


def test_a_minimum_lot_over_the_dollar_cap_refuses_the_entry():
    assert "refusal = $\"minimum lot" in SOURCE
    assert "Max Dollar Risk" in SOURCE
    assert "return riskAmount" not in SOURCE      # sizing is risk %, not a fixed lot


# --- purity and reporting -----------------------------------------------------------------

def test_the_bot_is_pure_rules_with_no_ai_or_news_layer():
    for forbidden in ("UseAiGateMode", "AiTelemetryUrl", "EnableNewsFilter", "TelegramBotToken",
                      "CommandPollMs", "OpenAiApiKey"):
        assert forbidden not in SOURCE


def test_reporting_is_live_only_so_a_backtest_never_books_trades():
    assert SOURCE.count("if (RunningMode != RunningMode.RealTime) return;") >= 3
    assert SOURCE.count("_httpClient.") == 1
