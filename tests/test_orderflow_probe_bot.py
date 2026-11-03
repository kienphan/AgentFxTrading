"""
OrderFlowProbeBot: the measurement cBot that decides whether the order-flow book's setups can be
built on cTrader tick data (docs/orderflow-probe.md).

The cBot is compiled by the cTrader console container, not by pytest, so its contract is pinned
against the source the same way tests/test_fomc_drift_bot.py pins FomcDriftBot. Two kinds of
checks live here:

* wiring  - the probe really reads ticks and depth, and really never trades or calls the backend;
* rules   - the quote-tick classifier, the tick-aligned price levels and the diagonal stack test
            are spelled the same way the design doc describes them.

The classifier is also reimplemented in Python below. It is not a second engine; it states the
four cases (buy / sell / both / neutral) as examples so a future edit that flips a sign fails
here before anyone reads a probe log.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "cBot" / "OrderFlowProbeBot.cs").read_text(encoding="utf-8")
# The header comment cites the cTrader API docs; only executable lines matter for the
# "never trades / never calls home" checks.
CODE = "\n".join(line for line in SOURCE.splitlines()
                 if not line.lstrip().startswith("//"))


# --- wiring ------------------------------------------------------------------------------

def test_probe_runs_utc_with_full_access():
    assert "[Robot(TimeZone = TimeZones.UTC, AccessRights = AccessRights.FullAccess)]" in SOURCE


def test_probe_reads_ticks_and_depth():
    assert "MarketData.GetTicks(SymbolName)" in SOURCE
    assert "_ticks.Tick += OnTick;" in SOURCE
    assert "_ticks.LoadMoreHistory();" in SOURCE
    assert "_ticks.GetServerFirstTime()" in SOURCE
    assert "MarketData.GetMarketDepth(SymbolName)" in SOURCE


def test_probe_never_trades():
    for forbidden in ("ExecuteMarketOrder", "PlaceLimitOrder", "PlaceStopOrder", "PlaceMarketOrder",
                      "ModifyPosition", "ClosePosition", "CancelPendingOrder"):
        assert forbidden not in CODE, f"the probe must not call {forbidden}"


def test_probe_never_calls_the_backend():
    for forbidden in ("HttpClient", "WebSocket", "http://", "https://", "/trade", "portfolio/report"):
        assert forbidden not in CODE, f"the probe must not depend on {forbidden}"


def test_probe_drives_history_loading_from_the_robot_timer():
    # Timer.Start alone does nothing: the cBot has to override OnTimer (or subscribe TimerTick).
    assert "Timer.Start(TimeSpan.FromSeconds(LoadStepSeconds));" in SOURCE
    assert "protected override void OnTimer()" in SOURCE


def test_probe_keeps_a_live_bar_and_finalizes_it_at_the_boundary():
    assert "AddTickToCells(_liveCells, t);" in SOURCE
    assert "else if (target > _liveBarStart)" in SOURCE
    assert "FinalizeLiveBar();" in SOURCE


# --- defaults ----------------------------------------------------------------------------

def test_defaults_match_the_probe_spec():
    for snippet in (
        '"Footprint Period (minutes)", Group = "Probe", DefaultValue = 30',
        '"Load Step (seconds)", Group = "Probe", DefaultValue = 2',
        '"Max Load Time (minutes)", Group = "Probe", DefaultValue = 10',
        '"Max Load Calls", Group = "Probe", DefaultValue = 300',
        '"Max Ticks To Scan (0 = all)", Group = "Probe", DefaultValue = 2000000',
        '"DOM Interval (seconds)", Group = "Live", DefaultValue = 30',
        '"Rate Interval (seconds)", Group = "Live", DefaultValue = 60',
        '"Write CSV (Documents)", Group = "Reporting", DefaultValue = false',
        '"Bot ID", Group = "General", DefaultValue = "ORDERFLOW_PROBE"',
    ):
        assert snippet in SOURCE, snippet


# --- footprint rules ---------------------------------------------------------------------

def test_quote_ticks_are_classified_by_the_side_that_moved():
    assert "double dAsk = t.Ask - _prevAsk;" in SOURCE
    assert "double dBid = t.Bid - _prevBid;" in SOURCE
    assert "bool buy = dAsk > 1e-9;" in SOURCE
    assert "bool sell = dBid < -1e-9;" in SOURCE
    assert "AddCell(cells, LevelOf(t.Ask), 0, 1);" in SOURCE   # buy prints at the ask
    assert "AddCell(cells, LevelOf(t.Bid), 1, 0);" in SOURCE   # sell prints at the bid


def test_price_levels_are_tick_aligned():
    assert "Math.Round(price / Symbol.TickSize, MidpointRounding.AwayFromZero)" in SOURCE


def test_stack_rule_is_diagonal_and_ratio_based():
    assert 'private static readonly double[] StackRatios = { 2.0, 3.0, 4.0 };' in SOURCE
    assert "bool buy = cell.Ask > 0 && up != null && up.Bid > 0 && cell.Ask >= ratio * up.Bid;" in SOURCE
    assert "bool sell = cell.Bid > 0 && down != null && down.Ask > 0 && cell.Bid >= ratio * down.Ask;" in SOURCE
    assert "if (curBuy >= 3) buyStacks++;" in SOURCE


def test_history_scan_skips_empty_session_bars():
    assert "else if (target > bar.Start)" in SOURCE
    assert "bar = new BarAgg { Start = target };" in SOURCE
    assert "Empty bars in the gap are not recorded" in SOURCE


# --- the four classifier cases, stated in Python -----------------------------------------

def classify(prev_bid, prev_ask, bid, ask):
    """Return 0 neutral, 1 buy at ask, 2 sell at bid, 3 both (same rule as AddTickToCells)."""
    d_ask = ask - prev_ask
    d_bid = bid - prev_bid
    buy = d_ask > 1e-9
    sell = d_bid < -1e-9
    if buy and sell:
        return 3
    if buy:
        return 1
    if sell:
        return 2
    return 0


def test_classifier_cases():
    assert classify(100.00, 100.01, 100.01, 100.02) == 1   # both legs up: buyer at ask
    assert classify(100.00, 100.01, 99.99, 100.00) == 2    # both legs down: seller at bid
    assert classify(100.00, 100.01, 99.99, 100.02) == 3    # spread widened: one print each side
    assert classify(100.00, 100.01, 100.00, 100.01) == 0   # unchanged quote: no volume


# --- log contract ------------------------------------------------------------------------

def test_probe_prints_the_measurement_keys():
    for key in ("PROBE_ENV", "PROBE_SERVER_FIRST", "HIST_LOAD", "PROBE_HISTORY_DONE",
                "HIST_SUMMARY", "STAT|", "STACKS|", "CLUSTERS|", "MULTINODE|", "GAP|",
                "DOM|", "LIVE|", "FP_LIVE|"):
        assert key in SOURCE, key


def test_csv_is_optional_and_documented():
    assert "if (WriteCsv)" in SOURCE
    assert "WriteHistoryCsv(_rows);" in SOURCE
    assert "Environment.SpecialFolder.MyDocuments" in SOURCE
