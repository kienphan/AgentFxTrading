"""
Regression guard for daily-check 2026-09-24 #3: the broker minimum lot overrode the risk %.

    #83 ustec-newyork     0.1 lot, SL 1190.1p  -> -11.90 $ at SL = 0.72% (RiskPerTradePercent 0.2) 3.6x
    #82 us30-all-flowrsi  0.1 lot, SL 1387.6p  -> -13.88 $ at SL = 0.84% (RiskPercentage 0.5)      1.7x
    id 62 de40-london     0.1 lot, SL 1048.3p  -> -12.01 $ at SL                                    ~3.5x

Both bots size from the risk %, then clamp up to Symbol.VolumeInUnitsMin. The only check after
the clamp was an absolute dollar cap (MaxDollarRiskPerTrade / MaxRiskPerTradeMoney), which these
trades stayed under. MaxMinLotRiskMultiple (default 1.5, 0 = off) refuses such an entry.
"""

import re
from pathlib import Path

import pytest

CBOT = Path(__file__).resolve().parent.parent / "cBot"


def _src(bot: str) -> str:
    return (CBOT / bot).read_text(encoding="utf-8")


def _method_body(bot: str, signature: str) -> str:
    src = _src(bot)
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


@pytest.mark.parametrize("bot", ["AiAgentBot.cs", "FlowRsiBot.cs"])
def test_parameter_defaults_to_one_and_a_half_times_target(bot):
    m = re.search(r"\[Parameter\(([^\]]*)\)\]\s*public double MaxMinLotRiskMultiple\b", _src(bot))
    assert m, f"{bot} has no MaxMinLotRiskMultiple parameter"
    assert "DefaultValue = 1.5" in m.group(1)
    assert "MinValue = 0" in m.group(1), "0 must stay allowed to switch the check off"


def test_aiagent_refuses_the_entry_before_the_order():
    src = _src("AiAgentBot.cs")
    order_idx = src.index('ExecuteMarketOrder(tradeType, SymbolName, volume, "AI_Agent"')
    check_idx = src.rfind("if (MaxMinLotRiskMultiple > 0", 0, order_idx)
    assert check_idx > 0, "the multiple is not checked before the entry order"
    guard = src[check_idx:order_idx]
    assert "riskAmount * MaxMinLotRiskMultiple" in guard
    assert 'ReportGuardrailBlockedAsync("MinLotExceedsRiskPct"' in guard
    assert "return;" in guard
    # The check must see the final volume, after the MaxAllowedLots cap and the min-lot clamp.
    assert src.rindex("if (volume < Symbol.VolumeInUnitsMin) volume = Symbol.VolumeInUnitsMin;", 0, check_idx) > 0


def test_flowrsi_sizing_returns_zero_above_the_multiple():
    body = _method_body("FlowRsiBot.cs", "private double CalculateDynamicVolumeInUnits(double slPips)")
    clamp_idx = body.index("if (normalizedUnits < Symbol.VolumeInUnitsMin)")
    after = body[clamp_idx:]
    check_idx = after.find("finalRisk > effectiveRiskAmount * MaxMinLotRiskMultiple")
    assert check_idx > 0, "the clamped risk is not compared with the RiskPercentage target"
    guard = after[check_idx : after.index("return normalizedUnits;")]
    assert "return 0;" in guard
