"""
Regression guard: bar logic in OnBarClosed read the wrong bar (2026-09-23).

Inside ``OnBarClosed`` the last bar is not always the one that just closed. The bar
heartbeat showed it: at the 17:45 UTC close half the Judas bots saw 17:30 (the closed bar)
and half 17:45 (the bar that had just opened, holding its first tick). Everything that read
"the last bar" as the closed one then worked on a one-tick bar:

* Judas ``TrackAsianSession`` took that bar's High/Low and judged the session edge a bar late;
* Judas ``CheckJudasSweep`` measured the sweep, the close back inside the range, the rejection
  wick and the RSI filter on it - a one-tick bar has no wick, so a real sweep was rejected;
* FlowRSI ``EvaluateStrategySignals`` read the RSI cross, FVG, sweep and ATR at that index.

``ClosedBarIndex()`` tells the two apart by time: a bar whose full span has not elapsed on the
server clock is still forming, so the closed one is the bar before it. Tick-mode callers keep
reading the forming bar on purpose.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JUDAS = (ROOT / "cBot" / "AsianRangeJudasSweepBot.cs").read_text(encoding="utf-8")
FLOWRSI = (ROOT / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")
BOTS = {"judas": JUDAS, "flowrsi": FLOWRSI}


def _method(src: str, signature: str) -> str:
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
    raise AssertionError(f"Unbalanced braces in {signature}")


# --- ClosedBarIndex() --------------------------------------------------------------------

def _forming_condition(src: str) -> str:
    body = _method(src, "private int ClosedBarIndex()")
    m = re.search(r"if \((.+?)\)\s*\n\s*return last - 1;", body, re.S)
    assert m, "ClosedBarIndex() has no guard that steps back to the previous bar"
    return m.group(1)


def _is_forming(cond: str, *, last_open: datetime, now: datetime, last: int = 499) -> bool:
    py = cond.replace("Bars[last].OpenTime", "LAST_OPEN").replace("BarSpan()", "SPAN")
    py = py.replace("Server.Time", "NOW").replace("&&", " and ")
    py = re.sub(r"\blast\b", "LAST", py)
    return bool(eval(  # noqa: S307 - our own translated source under an empty builtins
        " ".join(py.split()), {"__builtins__": {}},
        {"LAST_OPEN": last_open, "NOW": now, "SPAN": timedelta(minutes=15), "LAST": last},
    ))


@pytest.mark.parametrize("bot", BOTS)
@pytest.mark.parametrize("last_open,forming", [
    (datetime(2026, 9, 23, 17, 30), False),  # last bar is the closed 17:30 bar -> use it
    (datetime(2026, 9, 23, 17, 45), True),   # last bar is the new 17:45 bar -> step back
], ids=["closed-bar-seen", "new-bar-seen"])
def test_closed_bar_is_told_apart_from_a_forming_bar_by_time(bot, last_open, forming):
    cond = _forming_condition(BOTS[bot])
    now = datetime(2026, 9, 23, 17, 45, 3)  # handled 3 s after the 17:45 close
    assert _is_forming(cond, last_open=last_open, now=now) is forming


@pytest.mark.parametrize("bot", BOTS)
def test_closed_bar_needs_a_previous_bar_to_step_back_to(bot):
    cond = _forming_condition(BOTS[bot])
    assert _is_forming(cond, last_open=datetime(2026, 9, 23, 17, 45),
                       now=datetime(2026, 9, 23, 17, 45, 3), last=0) is False


# --- Judas -------------------------------------------------------------------------------

JUDAS_ON_BAR = _method(JUDAS, "protected override void OnBarClosed()")


def test_judas_resolves_the_closed_bar_once_per_bar():
    assert JUDAS_ON_BAR.count("ClosedBarIndex()") == 1
    assert "int closedIdx = ClosedBarIndex();" in JUDAS_ON_BAR


def test_asian_session_is_fed_the_closed_bar():
    assert "TrackAsianSession(Bars[closedIdx])" in JUDAS_ON_BAR
    assert "Bars.LastBar" not in _method(JUDAS, "private void TrackAsianSession(")


def test_sweep_is_judged_on_the_closed_bar():
    assert "CheckJudasSweep(closedIdx, out" in JUDAS_ON_BAR
    sweep = _method(JUDAS, "private void CheckJudasSweep(")
    assert "Bars[barIndex]" in sweep
    assert "Bars.LastBar" not in sweep, "the sweep candle is still read from Bars.LastBar"
    assert "rsi.Result[barIndex]" in sweep
    assert "LastValue" not in sweep, "the RSI filter still reads the forming bar's value"


def test_signal_bar_bookkeeping_uses_the_closed_bar():
    assert re.search(r"_lastCrossBarIndex\s*=\s*closedIdx;", JUDAS_ON_BAR)
    assert re.search(r"_lastCrossBarTime\s*=\s*Bars\[closedIdx\]\.OpenTime;", JUDAS_ON_BAR)
    assert re.search(r"_barsSinceCross\s*=\s*closedIdx\s*-\s*_lastCrossBarIndex;", JUDAS_ON_BAR)
    assert "Bars.Count - 1" not in JUDAS_ON_BAR


@pytest.mark.parametrize("helper", ["private bool buyCondition()", "private bool sellCondition()"])
def test_tick_mode_still_reads_the_forming_bar(helper):
    """With _calculateOnBarClosed off the bot trades intrabar; that is its design."""
    assert "CheckJudasSweep(Bars.Count - 1, out" in _method(JUDAS, helper)


# --- FlowRSI -----------------------------------------------------------------------------

def test_flowrsi_evaluates_the_closed_bar():
    body = _method(FLOWRSI, "private void EvaluateStrategySignals(")
    assert "int index = ClosedBarIndex();" in body
    assert "Count - 1" not in body


# --- MtfChartBot --------------------------------------------------------------------------
# Same bug class, one layer down: the MTF bot feeds D1/H4/H1/M15 candles to the analyst, and
# `series.Count - 2` on each series dropped the bar that had just closed (2026-09-29 review).

MTF = (ROOT / "cBot" / "MtfChartBot.cs").read_text(encoding="utf-8")


def _mtf_forming_condition(src: str) -> str:
    body = _method(src, "private int ClosedIndexFor(Bars series)")
    m = re.search(r"if \((.+?)\)\s*\n\s*return last - 1;", body, re.S)
    assert m, "ClosedIndexFor() has no guard that steps back to the forming bar"
    return m.group(1)


def _is_forming_mtf(cond: str, *, last_open: datetime, now: datetime, span: timedelta,
                    last: int = 499) -> bool:
    py = cond.replace("series[last].OpenTime", "LAST_OPEN").replace("SpanOf(series)", "SPAN")
    py = py.replace("Server.Time", "NOW").replace("&&", " and ")
    py = re.sub(r"\blast\b", "LAST", py)
    return bool(eval(  # noqa: S307 - our own translated source under an empty builtins
        " ".join(py.split()), {"__builtins__": {}},
        {"LAST_OPEN": last_open, "NOW": now, "SPAN": span, "LAST": last},
    ))


@pytest.mark.parametrize("last_open,span,now,forming", [
    (datetime(2026, 9, 29, 17, 30), timedelta(minutes=15), datetime(2026, 9, 29, 17, 45, 3), False),
    (datetime(2026, 9, 29, 17, 45), timedelta(minutes=15), datetime(2026, 9, 29, 17, 45, 3), True),
    (datetime(2026, 9, 29, 12, 0), timedelta(hours=4), datetime(2026, 9, 29, 17, 45, 3), False),
    (datetime(2026, 9, 29, 16, 0), timedelta(hours=4), datetime(2026, 9, 29, 17, 45, 3), True),
    (datetime(2026, 9, 28, 0, 0), timedelta(days=1), datetime(2026, 9, 29, 17, 45, 3), False),
], ids=["m15-closed", "m15-new", "h4-closed", "h4-forming", "d1-closed"])
def test_mtf_resolves_each_series_by_time(last_open, span, now, forming):
    cond = _mtf_forming_condition(MTF)
    assert _is_forming_mtf(cond, last_open=last_open, now=now, span=span) is forming


def test_mtf_feeds_the_index_it_resolved_for_every_timeframe():
    # Server.Time is an instance property, so the helper that reads it must not be static.
    assert "private static int ClosedIndexFor" not in MTF
    body = _method(MTF, "private List<Dictionary<string, object>> ClosedBars(Bars series, int count)")
    assert "int lastClosed = ClosedIndexFor(series);" in body
    assert "series.Count - 2" not in MTF
    for key, series in (("d1", "_d1"), ("h4", "_h4"), ("h1", "_h1"), ("m15", "Bars")):
        assert f'["{key}"] = ClosedBars({series},' in MTF
