"""
TurtleBot: the on-disk cBot (Turtle rules for one instrument), its wiring into the Backtest
page, and the daily range that page has to allow before a 20/10 vs 55/20 comparison means
anything. See docs/turtle.md for the XAUUSD recipe this supports.

The cBot is compiled by the installer container, not by pytest, so the rule invariants below
are pinned against the source the same way tests/test_dashboard_sltp_and_close_reason.py and
tests/test_initial_sl_restore.py pin the other cBots.
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

SOURCE = (ROOT / "cBot" / "TurtleBot.cs").read_text(encoding="utf-8")

ACCOUNT = {"slug": "demo-turtle", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-turtle_pwd"}
BOT = "cbot-demo-turtle-xauusd-turtle"

# The recipe docs/turtle.md prints: one container, one symbol, a daily chart, its own account.
RUN_COMMAND = (
    "docker run -d --name cbot-demo-turtle-xauusd-turtle --restart unless-stopped --network host "
    "-v /home/forge/AgentFxTrading:/workspace -v /home/forge/ctrader:/root "
    f"{bc.DEFAULT_IMAGE} run /workspace/cBot/TurtleBot.algo "
    "--ctid=trader@example.com --pwd-file=/root/ctrader_data/ctid_demo-turtle_pwd "
    f"--account=10115236 --symbol=XAUUSD --period=d1 --full-access --BotId={BOT} "
    "--EntryChannelPeriod=20 --ExitChannelPeriod=10"
)


@pytest.fixture
def homes(tmp_path):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "TurtleBot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_demo-turtle_pwd").write_text("s3cret-password")
    return project, ctrader


def turtle_job(**over):
    job = {"id": 7, "strategy": "turtle", "algo": "TurtleBot.algo", "ctid_email": "trader@example.com",
           "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-turtle_pwd",
           "symbol": "XAUUSD", "period": "d1", "start_date": "2016-09-26", "end_date": "2026-09-25",
           "data_mode": "ticks", "spread_pips": None, "balance": 10000.0,
           "params": {"BotId": BOT, "EntryChannelPeriod": "20", "ExitChannelPeriod": "10"}}
    job.update(over)
    return job


# --- wiring into the backtest page -------------------------------------------------------

def test_the_algo_is_a_supported_backtest_strategy():
    assert bc.STRATEGY_BY_ALGO["TurtleBot.algo"] == "turtle"
    assert "turtle" in bc.SUPPORTED_STRATEGIES


def test_a_turtle_container_is_a_supported_source(homes):
    project, ctrader = homes
    row = bc.describe_source(BOT, RUN_COMMAND, project, ctrader)
    assert row == {"name": BOT, "strategy": "turtle", "symbol": "XAUUSD", "period": "d1",
                   "account_label": "", "supported": True, "reason": ""}


def test_an_unbuilt_turtle_algo_is_reported_as_such(homes):
    project, ctrader = homes
    (project / "cBot" / "TurtleBot.algo").unlink()
    assert bc.describe_source(BOT, RUN_COMMAND, project, ctrader)["reason"] == "TurtleBot.algo is not built"


def test_locked_params_switch_the_reporting_endpoints_off():
    locked = bc.locked_params("turtle", 7)
    assert locked == {"BotId": "bt-7", "AccountLabel": "backtest",
                      "ApiUrl": bc.DISABLED_URL, "AiReportUrl": bc.DISABLED_URL}


def test_the_backtest_spec_runs_a_daily_gold_chart_on_bridge_network(homes):
    project, ctrader = homes
    spec = bc.build_container_spec(turtle_job(), project, ctrader)
    command = spec["command"]

    assert spec["network_mode"] == "bridge" and spec["name"] == "bt-7"
    assert "--symbol=XAUUSD" in command and "--period=d1" in command
    assert "--start=26/09/2016" in command and "--end=25/09/2026" in command
    assert command[-len(bc.locked_params("turtle", 7)):] == [
        f"--{k}={v}" for k, v in bc.locked_params("turtle", 7).items()]
    assert f"--BotId={BOT}" not in command and "--BotId=bt-7" in command
    assert "s3cret-password" not in " ".join(command)


# --- how long a backtest may run ---------------------------------------------------------

def test_daily_charts_may_span_years_where_intraday_charts_may_not():
    ten_years = BacktestCreate(bot_name=BOT, start=date(2016, 9, 26), end=date(2026, 9, 25))
    validate_run_config(ten_years, date(2026, 9, 27), "d1")                     # no raise

    with pytest.raises(HTTPException):
        validate_run_config(ten_years, date(2026, 9, 27), "m15")

    assert (max_span_days("d1"), max_span_days("m15"), max_span_days(None)) == (
        bc.DAILY_MAX_SPAN_DAYS, bc.MAX_SPAN_DAYS, bc.MAX_SPAN_DAYS)


def test_the_cap_is_still_one_year_for_intraday_bots():
    assert max_span_days("m5") == bc.MAX_SPAN_DAYS == 365
    ok = BacktestCreate(bot_name=BOT, start=date(2025, 9, 26), end=date(2026, 9, 25))
    validate_run_config(ok, date(2026, 9, 27), "m15")                           # no raise


# --- the rules themselves ----------------------------------------------------------------

def test_channels_and_n_are_measured_on_closed_bars_only():
    # Bars.Count-1 is the bar still forming: no level a live order can rest on may move with it.
    assert "private int LastClosedIndex => Bars.Count - 2;" in SOURCE


def test_n_is_wilders_atr_not_a_simple_average():
    assert "rma = ((period - 1) * rma + TrueRange(i)) / period;" in SOURCE


def test_each_unit_stops_two_n_from_its_own_fill_and_the_channel_trails_it():
    assert "? pos.EntryPrice - StopDistanceN * unitN" in SOURCE
    assert "? Math.Max(initialStop, channelStop)" in SOURCE        # Buy: never below the channel
    assert ": Math.Min(initialStop, channelStop)" in SOURCE        # Sell


def test_the_pyramid_adds_one_unit_every_half_n():
    assert "double step = PyramidStepN * _n;" in SOURCE
    assert "if (units.Length >= MaxUnits) return;" in SOURCE


def test_a_minimum_lot_over_the_dollar_cap_refuses_the_entry():
    assert "return risk > MaxDollarRiskPerUnit ? 0.0 : units;" in SOURCE
    assert "Entry REFUSED" in SOURCE


def test_system_1_skips_exactly_one_breakout_after_a_winning_group():
    assert "private bool FailsafeBlocks(TradeType side)" in SOURCE
    assert "if (!_skipNextBreakout.TryGetValue(side, out bool skip) || !skip) return false;" in SOURCE
    # Consuming the flag is what keeps the rule from locking the bot out for good: a sticky
    # "last group won" test would skip every later signal in that direction, including the
    # losing one that is supposed to re-arm it.
    assert "_skipNextBreakout[side] = false;" in SOURCE
    assert "if (!EnableFailsafeFilter) return false;" in SOURCE


def test_no_take_profit_the_channel_is_the_exit():
    assert "var result = ExecuteMarketOrder(side, SymbolName, units, BotId, slPips, null, BotId);" in SOURCE


def test_reporting_is_live_only_so_a_backtest_never_books_trades():
    assert SOURCE.count("if (RunningMode != RunningMode.RealTime) return;") >= 3
    assert SOURCE.count("_httpClient.") == 1


def test_the_bot_id_puts_it_in_its_own_risk_bucket():
    assert strategy_of(BOT) == "turtle"
