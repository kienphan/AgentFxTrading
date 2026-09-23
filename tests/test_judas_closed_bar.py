"""
Regression guard: the Judas Asian range read the wrong bar (2026-09-23).

``OnBarClosed`` handed ``Bars.LastBar`` to ``TrackAsianSession`` as the bar that just closed.
It usually is, but the bar heartbeat showed otherwise: at the 17:45 UTC close half the Judas
bots reported 17:30 (the closed bar) and half 17:45 (the bar that had just opened). When the
new bar is read, the range takes the high/low of a bar holding its first tick, the closed
bar's extremes are never added, and a session boundary is judged one bar late - so the Asian
high/low can miss the real extreme and a later "sweep" is measured against the wrong level.

``ClosedBar()`` tells the two apart by time: a bar whose full span has not elapsed on the
server clock is still forming, and the closed one is the bar before it.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "cBot" / "AsianRangeJudasSweepBot.cs").read_text(encoding="utf-8")


def _method(signature: str) -> str:
    start = SRC.index(signature)
    open_idx = SRC.index("{", start)
    depth = 0
    for i in range(open_idx, len(SRC)):
        if SRC[i] == "{":
            depth += 1
        elif SRC[i] == "}":
            depth -= 1
            if depth == 0:
                return SRC[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces in {signature}")


def test_asian_session_is_fed_the_closed_bar():
    on_bar = _method("protected override void OnBarClosed()")
    assert "TrackAsianSession(ClosedBar())" in on_bar
    track = _method("private void TrackAsianSession(")
    assert "Bars.LastBar" not in track, "TrackAsianSession still reads Bars.LastBar directly"


def _forming_condition() -> str:
    body = _method("private Bar ClosedBar()")
    m = re.search(r"if \((.+?)\)\s*\n\s*return Bars\.Last\(1\);", body, re.S)
    assert m, "ClosedBar() has no guard that falls back to Bars.Last(1)"
    return m.group(1)


def _is_forming(cond: str, *, last_open: datetime, now: datetime, count: int = 500) -> bool:
    py = cond.replace("last.OpenTime", "LAST_OPEN").replace("BarSpan()", "SPAN")
    py = py.replace("Server.Time", "NOW").replace("Bars.Count", "COUNT").replace("&&", " and ")
    return bool(eval(  # noqa: S307 - our own translated source under an empty builtins
        " ".join(py.split()), {"__builtins__": {}},
        {"LAST_OPEN": last_open, "NOW": now, "SPAN": timedelta(minutes=15), "COUNT": count},
    ))


@pytest.mark.parametrize("last_open,forming", [
    (datetime(2026, 9, 23, 17, 30), False),  # LastBar is the closed 17:30 bar -> use it
    (datetime(2026, 9, 23, 17, 45), True),   # LastBar is the new 17:45 bar -> use Last(1)
], ids=["closed-bar-seen", "new-bar-seen"])
def test_closed_bar_tells_a_forming_bar_apart_by_time(last_open, forming):
    cond = _forming_condition()
    now = datetime(2026, 9, 23, 17, 45, 3)  # handled 3 s after the 17:45 close
    assert _is_forming(cond, last_open=last_open, now=now) is forming


def test_closed_bar_needs_a_previous_bar_to_fall_back_to():
    cond = _forming_condition()
    assert _is_forming(cond, last_open=datetime(2026, 9, 23, 17, 45),
                       now=datetime(2026, 9, 23, 17, 45, 3), count=1) is False
