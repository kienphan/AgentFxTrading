"""
Regression guard for the exit price reported when a position closes.

C-13 (audit 2026-09-22): the bots took the exit price from ``Symbol.Bid``/``Symbol.Ask``
at the moment the ``Positions.Closed`` handler happened to run, not the price the
position actually closed at. A stop swept by a spike that snaps back was reported at a
price that never traded.

The first fix looked the deal up with ``History.FirstOrDefault(h => h.PositionId == ...)``.
History holds one deal per close, so for a position that was partially closed at
break-even that returned the PARTIAL, not the exit. On 2026-09-23:

    GBPJPY #674942756  BE stop hit at 209.92  -> exit_price 210.09 (the partial)
    EURJPY #674942793  BE stop hit at 180.15  -> exit_price 180.33
    ETHUSD #674939909  TP hit at 2737.29      -> exit_price 2744.36
    BTCUSD #674942777  TP hit at 85832.38     -> exit_price 86149.11

The Judas bot also fell back to ``History.FindLast(label, SymbolName)``, which before
this position's deal is booked returns the previous position's exit.

Every bot now asks ``FinalClosingPrice``: the newest deal for the position that closed
the remaining volume within the last minute.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _body(cs_name: str, signature: str) -> str:
    src = (ROOT / "cBot" / cs_name).read_text(encoding="utf-8")
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
    raise AssertionError(f"Unbalanced braces in {cs_name} {signature!r}")


HANDLERS = [
    ("FlowRsiBot.cs", "private void OnPositionClosed(PositionClosedEventArgs args)"),
    ("AiAgentBot.cs", "private void OnPositionClosed(PositionClosedEventArgs args)"),
    ("AsianRangeJudasSweepBot.cs", "private void OnPositionsClosed(PositionClosedEventArgs args)"),
]
BOTS = [h[0] for h in HANDLERS]
HELPER = "private double? FinalClosingPrice(Position pos)"


@pytest.mark.parametrize("cs_name,signature", HANDLERS, ids=BOTS)
def test_close_handler_uses_the_final_deal(cs_name, signature):
    body = _body(cs_name, signature)
    assert "FinalClosingPrice(" in body, (
        f"{cs_name} does not take the exit price from the deal that closed the position."
    )
    assert "History.FirstOrDefault(h => h.PositionId" not in body, (
        f"{cs_name} takes the FIRST History deal of the position, which after a partial "
        f"close is the partial's price, not the exit."
    )
    assert "History.FindLast(" not in body, (
        f"{cs_name} falls back to the last trade under the label, which can be another position."
    )


@pytest.mark.parametrize("cs_name", BOTS)
def test_final_closing_price_selects_the_newest_matching_deal(cs_name):
    body = _body(cs_name, HELPER)
    assert "h.PositionId == pos.Id" in body
    assert "OrderByDescending(h => h.ClosingTime)" in body, (
        f"{cs_name} must pick the newest deal: an earlier one is a partial close."
    )
    assert "pos.VolumeInUnits" in body, (
        f"{cs_name} must match the deal that closed the remaining volume."
    )
    assert "ClosingTime >= cutoff" in body, (
        f"{cs_name} must ignore deals older than the close it is reporting, or before "
        f"History books the final deal it would return the partial again."
    )


@pytest.mark.parametrize("cs_name,signature", HANDLERS, ids=BOTS)
def test_exit_price_falls_back_when_history_is_not_ready(cs_name, signature):
    body = _body(cs_name, signature)
    assert "Symbol.Bid" in body and "Symbol.Ask" in body, (
        f"{cs_name} has no live-spread fallback for when History has not yet been "
        f"populated - a missing lookup would report no exit price at all."
    )
    assert "PositionCloseReason.TakeProfit" in body and "PositionCloseReason.StopLoss" in body, (
        f"{cs_name} should fall back to the TP/SL level the broker closed at before the spread."
    )
