"""
Regression guard: FlowRSI's gated trailing stop could never arm.

``TargetRiskReward`` is 1.5 (``_FLOWRSI_BASE``, shipped on all 15 flowrsi presets)
and ``TpMode`` defaults to ``Risk_Reward_Ratio``, so a position's take profit sits
at exactly 1.5R -- less once the spread is paid, because the stop is measured from
the far side of the book (ETHUSD opened 2026-09-22 reported 2891.3p / 4239.9p =
1.47R). ``TrailingStopTriggerRr`` defaults to 1.8 and no preset overrides it, so

    if (EnableTrailingStop && currentRr >= TrailingStopTriggerRr)

is unreachable: the trade hits TP at ~1.47R before currentRr ever reaches 1.8.
Every per-symbol ``TrailingStopDistancePips`` in the preset table (ETHUSD 2000,
BTCUSD 30000, XAUUSD 300, the index values) was a dead parameter.

Two layers: a default the target R:R can actually reach, and a clamp in the bot so
a hand-edited trigger above the position's own take profit cannot silently disable
trailing again.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS_PATH = ROOT / "cBot" / "FlowRsiBot.cs"


def _method_body(signature: str) -> str:
    src = CS_PATH.read_text(encoding="utf-8")
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
    raise AssertionError(f"Unbalanced braces in {signature!r}")


def _default_value(parameter_name: str) -> float:
    """Read a [Parameter(... DefaultValue = X)] attribute off the property it decorates."""
    src = CS_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"\[Parameter\([^\]]*DefaultValue\s*=\s*([0-9.]+)[^\]]*\)\]\s*\n\s*public double "
        + re.escape(parameter_name)
        + r"\b",
        src,
    )
    assert match, f"No [Parameter(DefaultValue=...)] found for {parameter_name}"
    return float(match.group(1))


def test_trailing_trigger_default_sits_below_the_take_profit_it_must_beat():
    trigger = _default_value("TrailingStopTriggerRr")
    target_rr = _default_value("TargetRiskReward")
    assert trigger < target_rr, (
        f"TrailingStopTriggerRr={trigger} >= TargetRiskReward={target_rr}: the position "
        f"reaches TP before currentRr reaches the trigger, so trailing never arms"
    )


def test_trailing_trigger_default_survives_the_spread_paid_on_the_stop():
    """
    The stop is measured from the far side of the book, so the realised R:R at TP is
    TargetRiskReward * S / (S + spread), not TargetRiskReward. The four positions open
    on 2026-09-22 realised 1.40 (BTCUSD) to 1.49 (UK100). A trigger must clear the
    worst of those, not just the nominal target.
    """
    worst_observed_rr_at_tp = 1.40  # BTCUSD: 106544.6p TP / 76042.4p SL
    assert _default_value("TrailingStopTriggerRr") < worst_observed_rr_at_tp


def test_trailing_gate_clamps_a_trigger_the_take_profit_can_never_reach():
    body = _method_body("private void ManageExits()")
    gate = re.search(r"if \(EnableTrailingStop && currentRr >= ([A-Za-z_][A-Za-z0-9_]*)\)", body)
    assert gate, "The gated trailing stop condition is no longer recognisable"
    assert gate.group(1) != "TrailingStopTriggerRr", (
        "The trailing gate compares currentRr against the raw parameter. A trigger above "
        "the position's own take profit can never fire; clamp it against the TP first."
    )


def test_the_trailing_clamp_is_derived_from_the_positions_take_profit():
    body = _method_body("private void ManageExits()")
    clamp = re.search(r"double effectiveTrailTriggerRr\s*=", body)
    assert clamp, "No effectiveTrailTriggerRr computed in ManageExits"
    window = body[clamp.start() : clamp.start() + 900]
    assert "TakeProfit" in window, (
        "The clamp must be derived from the position's actual take profit, not a constant"
    )
    assert "initialSlDist" in window, (
        "The TP distance must be expressed in R (divided by the initial stop distance)"
    )
