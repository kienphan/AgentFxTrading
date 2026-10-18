"""
Regression guard for the FlowRsiBot SL/TP unit bug (2026-09-22 demo incident).

cTrader's ``ExecuteMarketOrder(tradeType, symbol, volume, label, stopLossPips,
takeProfitPips, comment)`` takes DISTANCES IN PIPS. FlowRsiBot used to hand it the
absolute ``slPrice`` / ``tpPrice`` levels, so the broker received e.g.
"SL: 1.1 pips" on EURUSD (stopped out by spread noise within seconds, +-$0.xx) and
"SL: 4332 pips" on XAUUSD (= $43 risk instead of the intended $9).

The Python suite has no .NET toolchain, so these tests pin the source-level
contract of the order call in ``ExecuteTechnicalOrder``.
"""

import re
from pathlib import Path

CS_PATH = Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs"


def _execute_technical_order_body() -> str:
    src = CS_PATH.read_text(encoding="utf-8")
    start = src.index("private void ExecuteTechnicalOrder(")
    end = src.index("private double CalculateDynamicVolumeInUnits(", start)
    return src[start:end]


def _market_order_args() -> list[str]:
    body = _execute_technical_order_body()
    match = re.search(r"var result = ExecuteMarketOrder\((.*?)\);", body, re.S)
    assert match, "ExecuteMarketOrder call not found in ExecuteTechnicalOrder"
    return [arg.strip() for arg in match.group(1).split(",")]


def test_market_order_does_not_pass_absolute_prices_as_pips():
    """The 5th/6th ExecuteMarketOrder args are pip distances, never the price levels."""
    args = _market_order_args()
    sl_arg, tp_arg = args[4], args[5]
    assert sl_arg != "slPrice", (
        "ExecuteMarketOrder stopLossPips received the absolute slPrice - "
        "cTrader treats it as a pip distance (EURUSD -> 1.1 pips, XAUUSD -> 4332 pips)"
    )
    assert tp_arg != "tpPrice", (
        "ExecuteMarketOrder takeProfitPips received the absolute tpPrice"
    )


def test_market_order_sl_tp_are_derived_from_final_levels_in_pips():
    """SL/TP pip args must be locals computed as a price distance divided by Symbol.PipSize,
    declared after the broker-boundary adjustment so they reflect the final levels."""
    body = _execute_technical_order_body()
    args = _market_order_args()
    boundary_check = body.index("Pre-flight broker boundary checks")
    for name in (args[4], args[5]):
        decl = re.search(rf"double\s+{re.escape(name)}\s*=\s*([^;]+);", body)
        assert decl, f"{name} must be a local computed inside ExecuteTechnicalOrder"
        assert "/ Symbol.PipSize" in decl.group(1), (
            f"{name} must be a pip distance (price delta / Symbol.PipSize), got: {decl.group(1)}"
        )
        assert decl.start() > boundary_check, (
            f"{name} must be computed from the final SL/TP levels, after the boundary checks"
        )


def test_fixed_lots_and_max_allowed_lots_parameters_declared():
    """FlowRsiBot must declare FixedLots and MaxAllowedLots to support fixed sizing caps."""
    src = CS_PATH.read_text(encoding="utf-8")
    assert 'public double FixedLots { get; set; }' in src
    assert 'public double MaxAllowedLots { get; set; }' in src


def test_calculate_dynamic_volume_handles_fixed_lots_and_max_allowed_lots():
    """CalculateDynamicVolumeInUnits must check FixedLots and cap at MaxAllowedLots."""
    src = CS_PATH.read_text(encoding="utf-8")
    start = src.index("private double CalculateDynamicVolumeInUnits(")
    end = src.index("private void ManageExits()", start)
    body = src[start:end]
    assert "if (FixedLots > 0)" in body
    assert "if (MaxAllowedLots > 0)" in body
    assert "Symbol.NormalizeVolumeInUnits(FixedLots * Symbol.LotSize)" in body
    assert "Symbol.NormalizeVolumeInUnits(MaxAllowedLots * Symbol.LotSize)" in body
