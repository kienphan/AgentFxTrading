"""
OrderFlowBot: the cBot for the order-flow book's setups (docs/orderflow.md).

The cBot is compiled by the cTrader console container, not by pytest, so the contract is pinned
against the source the same way tests/test_fomc_drift_bot.py pins FomcDriftBot. The tests cover
the pieces where a silent regression would be expensive:

* the tick classifier and the diagonal imbalance rule (the footprint's definition of volume);
* the zone -> breakout -> first-test state machine, including "one entry per zone";
* the book's SL/TP rules and the RR gate;
* the reporting contract (sync/open/close/resume) and the backtest guard;
* the defaults that the docs and the probe calibration depend on.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "cBot" / "OrderFlowBot.cs").read_text(encoding="utf-8")

# Header comments cite the API docs and explain why Trades Filter is absent; only executable
# lines matter for the "no order outside the entry path" checks.
CODE = "\n".join(line for line in SOURCE.splitlines() if not line.lstrip().startswith("//"))


# --- lifecycle and wiring ----------------------------------------------------------------

def test_bot_is_utc_full_access():
    assert "[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]" in SOURCE


def test_bot_reads_ticks_and_seeds_history():
    assert "MarketData.GetTicks(SymbolName)" in SOURCE
    assert "_ticks.Tick += OnTickEvent;" in SOURCE
    assert "_ticks.LoadMoreHistory();" in SOURCE
    assert "SeedFromHistory();" in SOURCE
    assert "SEED_TOUCH" in SOURCE


def test_bot_requires_tick_data_in_backtest():
    assert "NO_TICKS" in SOURCE
    assert "backtest needs --data-mode=ticks" in SOURCE


def test_bot_does_not_trade_during_seed_or_warmup():
    assert "if (_seeding)" in SOURCE
    assert "if (!_warmupReady)" in SOURCE
    assert "WARMUP|bars=" in SOURCE


# --- defaults pinned to the documented design -------------------------------------------

def test_defaults_match_the_book_recipe():
    for snippet in (
        '"Bot ID (position label)", Group = "General", DefaultValue = "ORDERFLOW"',
        '"Footprint Period (minutes)", Group = "Footprint", DefaultValue = 30',
        '"Warm-up Bars", Group = "Footprint", DefaultValue = 96',
        '"Seed From Tick History", Group = "Footprint", DefaultValue = true',
        '"Seed Zone Bars", Group = "Footprint", DefaultValue = 96',
        '"Cluster Factor (x median cell)", Group = "Zones", DefaultValue = 2.0',
        '"Stack Ratio (x, 3=300%)", Group = "Zones", DefaultValue = 3.0',
        '"Stack Min Levels", Group = "Zones", DefaultValue = 3',
        '"Profile Confirmations", Group = "Zones", DefaultValue = 1',
        '"Breakout Bars", Group = "Entry", DefaultValue = 2',
        '"Max Spread (pips)", Group = "Entry", DefaultValue = 50.0',
        '"Risk per Trade (% Balance)", Group = "Risk", DefaultValue = 0.2',
        '"Stop Loss Mode", Group = "Risk", DefaultValue = OrderFlowSlMode.Area',
        '"Min Reward/Risk", Group = "Risk", DefaultValue = 1.5',
        '"Agent API URL", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/trade"',
        '"Portfolio Report Endpoint", Group = "Reporting", DefaultValue = "http://127.0.0.1:8000/portfolio/report"',
    ):
        assert snippet in SOURCE, snippet


# --- footprint definition ----------------------------------------------------------------

def test_quote_ticks_are_classified_by_the_side_that_moved():
    assert "double dAsk = t.Ask - _prevAsk;" in SOURCE
    assert "double dBid = t.Bid - _prevBid;" in SOURCE
    assert "bool buy = dAsk > 1e-9;" in SOURCE
    assert "bool sell = dBid < -1e-9;" in SOURCE
    assert "AddCell(bar.Cells, LevelOf(t.Ask), 0, 1);" in SOURCE
    assert "AddCell(bar.Cells, LevelOf(t.Bid), 1, 0);" in SOURCE


def test_one_tick_is_one_unit_of_volume():
    assert "one tick as one unit of volume" in SOURCE
    assert "int side = Classify(t);" in SOURCE
    assert "AddTickToBar(_m30, t, side)" in SOURCE
    assert "AddTickToBar(_m5, t, side)" in SOURCE
    assert "AddTickToBar(_m1, t, side)" in SOURCE


def test_stack_rule_is_diagonal_and_ratio_based():
    assert "cell.Ask >= ratio * up.Bid" in SOURCE
    assert "cell.Bid >= ratio * down.Ask" in SOURCE
    assert "if (length < minLevels)" in SOURCE


def test_price_levels_are_tick_aligned():
    assert "Math.Round(price / Symbol.TickSize, MidpointRounding.AwayFromZero)" in SOURCE


# --- zones -> breakout -> first test -----------------------------------------------------

def test_zone_kinds_are_the_book_setups():
    for kind in ("Cluster", "MultiNode", "StackedBuy", "StackedSell", "Profile"):
        assert kind in SOURCE


def test_breakout_requires_finished_bars_fully_outside():
    assert "bool above = bar.Low > zone.High;" in SOURCE
    assert "bool below = bar.High < zone.Low;" in SOURCE
    assert "zone.ArmBars++;" in SOURCE
    assert "ZoneMaxAgeBars" in SOURCE
    assert "bool inside = !above && !below;" in SOURCE


def test_first_test_is_from_the_breakout_side_only():
    assert "bool fromAbove = prevBid > zone.High + tol" in SOURCE
    assert "bool fromBelow = prevAsk < zone.Low - tol" in SOURCE
    assert "zone.Consumed = true;" in SOURCE
    assert "if (!zone.Armed || zone.Consumed || zone.Invalid)" in SOURCE
    assert "tested from the wrong side" in SOURCE


def test_each_zone_can_only_be_traded_once():
    # CheckTouches consumes the zone before TryEnter, and TryEnter itself never re-arms it.
    touch = SOURCE.index("zone.Consumed = true;")
    enter = SOURCE.index("TryEnter(zone, side, fromAbove ? ask : bid, time);")
    assert touch < enter
    assert "if (Positions.FindAll(BotId, SymbolName).Length > 0)" in SOURCE


def test_both_zones_lock_the_breakout_side():
    assert "ZoneBias direction = zone.Bias == ZoneBias.Both ? zone.ArmSide : zone.Bias;" in SOURCE
    assert "zone.ArmSide = ZoneBias.Long;" in SOURCE
    assert "zone.ArmSide = ZoneBias.Short;" in SOURCE


# --- protection ---------------------------------------------------------------------------

def test_three_stop_modes_exist():
    assert "public enum OrderFlowSlMode" in SOURCE
    assert "Area," in SOURCE and "LowVolume," in SOURCE and "Fixed" in SOURCE
    assert "private bool TryLowVolumeStop(Zone zone, TradeType side, out double sl)" in SOURCE


def test_take_profit_skips_the_zone_and_enforces_rr():
    assert "private bool TryNextHvn(Zone zone, TradeType side, double entry, out double hvnPrice)" in SOURCE
    assert "if (level <= floor)" in SOURCE
    assert "if (level >= ceiling)" in SOURCE
    assert "if (rr + 1e-9 < MinRR)" in SOURCE


def test_sizing_uses_pip_value_and_dollar_cap():
    assert "private double VolumeForRisk(double slPips" in SOURCE
    assert "double perUnitRisk = slPips * Symbol.PipValue;" in SOURCE
    assert "MaxDollarRiskPerTrade" in SOURCE
    assert "Symbol.NormalizeVolumeInUnits" in SOURCE


def test_position_management_and_order_flow_exit():
    assert "BreakEvenR" in SOURCE and "TrailStartR" in SOURCE
    assert "ModifyPosition(position, newSl, position.TakeProfit, position.HasTrailingStop)" in SOURCE
    assert "Order flow reversal (delta + opposite stack)" in SOURCE


# --- reporting ----------------------------------------------------------------------------

def test_reporting_contract_is_open_close_sync():
    assert 'action = "open"' in SOURCE
    assert 'action = "close"' in SOURCE
    assert 'action = "sync"' in SOURCE
    assert "ctrader_id = position.Id" in SOURCE
    assert "ReportPositionOpen(position, slPips, tpPips," in SOURCE
    assert "ReportPositionClosed(position," in SOURCE


def test_reports_only_in_realtime_and_resume_reports_open_positions():
    assert SOURCE.count("RunningMode != RunningMode.RealTime") >= 3
    assert "ResumeOpenPositions();" in SOURCE
    assert 'ReportPositionOpen(position, slPips, tpPips, "Resumed after restart", null, 0);' in SOURCE


def test_initial_stop_is_encoded_in_the_comment_for_restarts():
    assert 'string comment = string.Format(CultureInfo.InvariantCulture, "OF|{0}|{1}|{2:F1}"' in SOURCE
    assert "InitialSlFromComment(position)" in SOURCE
    assert "comment.Split('|')" in SOURCE


# --- gating -------------------------------------------------------------------------------

def test_spread_session_and_daily_limits_gate_entries():
    assert "if (spreadPips > MaxSpreadPips)" in SOURCE
    assert "if (!InSession(time))" in SOURCE
    assert "if (_tradesToday >= MaxTradesPerDay)" in SOURCE
    assert "private bool InSession(DateTime time)" in SOURCE


def test_dry_run_does_not_send_orders():
    assert "if (DryRun)" in SOURCE
    assert "ENTRY_DRY" in SOURCE


# --- footprint state persistence (warm-up must survive a restart) ------------------------

def test_state_persistence_is_configured_but_opt_out():
    assert ('[Parameter("State Directory (blank=off)", Group = "Footprint", '
            'DefaultValue = "/root/ctrader_data/orderflow_state")]') in SOURCE
    assert "public string StateDir { get; set; }" in SOURCE
    assert '[Parameter("Max State Gap (hours)", Group = "Footprint", DefaultValue = 48' in SOURCE
    assert "public int MaxStateGapHours { get; set; }" in SOURCE
    assert "if (string.IsNullOrWhiteSpace(StateDir)) return null;" in SOURCE


def test_state_is_never_used_by_a_backtest():
    # A stale file would silently change a replay, so both directions are live-only.
    assert "_stateFile = null;      // never read or write state during a backtest" in SOURCE
    assert "if (_stateFile == null || RunningMode != RunningMode.RealTime) return;" in SOURCE


def test_restore_rejects_a_foreign_or_mismatched_file():
    assert "if (doc.Version != StateVersion)" in SOURCE
    assert "if (!string.Equals(doc.Symbol, SymbolName, StringComparison.OrdinalIgnoreCase) || doc.Period != PeriodMinutes)" in SOURCE
    assert 'Print("STATE|discard|stale|age_h={0:F1}|max={1}", ageHours, MaxStateGapHours);' in SOURCE


def test_restore_brings_back_bars_indicators_and_zones():
    for snippet in (
        "_m30Bars.Clear();",
        "_atr = doc.Atr;",
        "_emaFast = doc.EmaFast;",
        "_emaSlow = doc.EmaSlow;",
        "_m30BarCount = doc.BarCount;",
        "_nextZoneId = doc.NextZoneId;",
        "_zones.Add(new Zone",
        "_restoredThroughUtc = _m30Bars[_m30Bars.Count - 1].Start.AddMinutes(PeriodMinutes);",
    ):
        assert snippet in SOURCE, snippet
    # A restored, already-warm bot announces READY instead of waiting another 96 bars.
    assert 'Print("READY|bars={0}|atr={1}|ema={2}/{3}|restored"' in SOURCE


def test_the_seed_only_replays_ticks_the_state_does_not_cover():
    assert "int covered = LowerBound(_restoredThroughUtc);" in SOURCE
    assert 'Print("SEED|skip_before_state|from={0:o}|ticks={1}", _restoredThroughUtc, covered - start);' in SOURCE
    # ...and a replayed bar that the state already holds is ignored instead of duplicated.
    assert "if (_m30Bars.Count > 0 && bar.Start <= _m30Bars[_m30Bars.Count - 1].Start) return;" in SOURCE


def test_state_is_written_atomically_and_not_on_every_tick():
    assert "private void SaveState(bool force = false)" in SOURCE
    assert "if (!force && (DateTime.UtcNow - _lastStateSaveUtc).TotalSeconds < 5) return;" in SOURCE
    assert "string tmp = _stateFile + \".tmp\";" in SOURCE
    assert "System.IO.File.Move(tmp, _stateFile);" in SOURCE


def test_the_whole_rebuilt_set_is_persisted_after_seeding():
    # The replay closes many bars inside a few seconds; the per-bar throttle would keep only one.
    assert "SaveState(force: true);" in SOURCE


def test_state_is_persisted_on_bar_close_entry_and_stop():
    assert SOURCE.count("SaveState();") >= 3          # warm-up bar, processed bar, entry
    assert SOURCE.count("SaveState(true") == 0
    stop_block = SOURCE[SOURCE.index("protected override void OnStop()"):]
    stop_block = stop_block[:stop_block.index("\n        }")]
    assert "SaveState();" in stop_block
