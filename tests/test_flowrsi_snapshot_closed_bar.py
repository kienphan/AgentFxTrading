"""
Regression guard for daily-check 2026-09-24 #9: the /trade snapshot still read the forming bar.

Inside OnBarClosed, Bars.LastBar is sometimes the bar that just opened, one tick old (14 of 15
bots at the 17:45 UTC close on 2026-09-23). 563fca4 moved EvaluateStrategySignals to
ClosedBarIndex(), but SendStateToAgentAsync kept reading index Bars.ClosePrices.Count - 1 for
fast/slow RSI, ATR and the swing levels, and started its bar list at Bars.Count - 1. The LLM and
the server gate then saw values one bar off from the ones the signal was taken on.
"""

from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")


def _method_body(signature: str) -> str:
    start = SRC.index(signature)
    open_idx = SRC.index("{", SRC.index(")", start))
    depth = 0
    for i in range(open_idx, len(SRC)):
        if SRC[i] == "{":
            depth += 1
        elif SRC[i] == "}":
            depth -= 1
            if depth == 0:
                return SRC[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces in {signature!r}")


def test_snapshot_indicators_come_from_the_closed_bar():
    body = _method_body("private async Task SendStateToAgentAsync(")
    assert "int index = ClosedBarIndex();" in body
    assert "Bars.ClosePrices.Count - 1" not in body


def test_snapshot_bar_list_starts_at_the_closed_bar():
    body = _method_body("private async Task SendStateToAgentAsync(")
    assert "Bars.Count - i" not in body
    list_start = body.index("var barList = new List<BarInfo>();")
    assert body.index("int index = ClosedBarIndex();") < list_start, (
        "the bar list must be built from the closed bar index"
    )
    assert "int idx = index - i;" in body
