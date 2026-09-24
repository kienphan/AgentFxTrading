"""
Regression guard for daily-check 2026-09-24 #8: one label for three different closes.

FlowRsiBot's OnPositionClosed labels a PositionCloseReason.Closed that it holds no reason for as
"Closed (Take Profit Early)" / "(Cut Loss Early)". Three paths ended there:

  - the user closing the position in cTrader (51, 70, 72 at 19:36 VN, before a container restart),
  - the AI CLOSE_ALL exit, which called a bare ClosePosition,
  - the high-watermark circuit breaker, which did too.

The close-reason statistics could not tell an AI exit, a circuit breaker and a manual close
apart, and the manual closes looked like early take-profits far from TP (FILL_FAR_FROM_LEVEL).
"""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")


def test_ai_close_all_records_its_reason():
    block = SRC[SRC.index('if (action == "CLOSE_ALL")'):]
    block = block[: block.index("[AI Emergency Exit]")]
    assert 'CloseWithReason(pos, $"AI Close: {decision.reason}")' in block
    assert "ClosePosition(pos);" not in block


def test_circuit_breaker_records_its_reason():
    block = SRC[SRC.index("[Circuit Breaker Triggered]"):]
    block = block[: block.index("EnableTelegramAlerts")]
    assert 'CloseWithReason(pos, "Circuit Breaker")' in block
    assert "ClosePosition(pos);" not in block


def test_no_full_close_bypasses_the_reason_map():
    """Every full close but CloseWithReason's own goes through it; partial closes pass a volume."""
    bare = re.findall(r"\bClosePosition\(pos\)", SRC)
    assert len(bare) == 1, f"bare ClosePosition(pos) calls: {len(bare)} (only CloseWithReason may make one)"


def test_a_close_the_bot_did_not_make_is_labelled_as_such():
    assert "Take Profit Early" not in SRC
    assert "Cut Loss Early" not in SRC
    assert '"Closed outside the bot (cTrader)"' in SRC
