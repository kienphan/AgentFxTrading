"""
Regression guard for C-07 (audit 2026-09-22): Asian range was off by one bar.

``TrackAsianSession`` took its hour from ``Server.Time`` -- the moment the tick
closed the bar -- while reading High/Low from ``Bars.LastBar``, the bar that
OPENED 15 minutes earlier. With ``asianEndHour = 6``:

  * the 05:45-06:00 bar closes at 06:00 -> hour 6, fails `hour < 6` -> the last
    bar of the Asian session is DROPPED from the range;
  * the 23:45-00:00 bar closes at 00:00 -> hour 0, passes `hour >= 0` -> a bar
    from the previous day is folded in, and because `_asianSessionDate != date`
    the range is RESET to that single bar's High/Low.

``InitializeAsianSession`` (run on restart) uses ``bar.OpenTime.Hour`` and is
correct, so the two paths disagreed: a container restart silently changed
``_asianLow``/``_asianHigh``, and with them the sweep threshold,
``IsEntryTooExtended`` and ``CheckStructuralInvalidation``.

No .NET toolchain in this suite, so this pins the source-level contract: the
hour and the High/Low must come from the same bar.
"""

import re
from pathlib import Path

CS_PATH = Path(__file__).resolve().parent.parent / "cBot" / "AsianRangeJudasSweepBot.cs"


def _src() -> str:
    return CS_PATH.read_text(encoding="utf-8")


def _track_call_argument() -> str:
    match = re.search(r"TrackAsianSession\((?P<arg>[^;]*)\);", _src())
    assert match, "TrackAsianSession call site not found"
    return match.group("arg").strip()


def _track_body() -> str:
    src = _src()
    start = src.index("private void TrackAsianSession(")
    return src[start : src.index("private bool IsSessionSweepSideTaken")]


def test_session_hour_comes_from_the_bar_not_the_tick_clock():
    arg = _track_call_argument()
    assert "Server.Time" not in arg, (
        "TrackAsianSession is still driven by Server.Time (the tick that CLOSED the "
        "bar) while it reads High/Low from the bar that OPENED 15m earlier - the Asian "
        "range is off by one bar at both session edges."
    )


def test_session_hour_uses_the_same_bar_whose_high_low_is_read():
    """
    The hour and the High/Low must describe the same bar. Since 2026-09-23 the call passes
    the closed bar itself (ClosedBarIndex(): inside OnBarClosed Bars.LastBar is sometimes the
    bar that just opened), and TrackAsianSession reads all three from that one parameter.
    """
    assert _track_call_argument() == "Bars[closedIdx]"
    body = _track_body()
    assert "private void TrackAsianSession(Bar closedBar)" in body
    assert "closedBar.OpenTime.Hour" in body
    assert "closedBar.High" in body and "closedBar.Low" in body
    assert "Bars.LastBar" not in body


def test_live_tracking_agrees_with_restart_initialisation():
    """Both paths must key off OpenTime, or a restart silently redraws the range."""
    src = _src()
    init_start = src.index("private void InitializeAsianSession")
    init_body = src[init_start : src.index("private void TrackAsianSession")]
    assert "bar.OpenTime" in init_body, (
        "InitializeAsianSession no longer reads bar.OpenTime - the restart path and "
        "the live path must agree on which bars belong to the Asian session."
    )
    assert "closedBar.OpenTime" in _track_body(), (
        "Live tracking and restart initialisation disagree on the bar time source."
    )
