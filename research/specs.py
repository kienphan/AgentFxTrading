"""Per-pair contract data, cost constants and FlowRSI parameters for the simulator.

Numbers come from backtest reports #27-#42: pip = |close - entry| / pips; min/step = observed deal
volumes; comm_per_unit = sum |commissions| / sum volume (round trip); usd_per_quote = gross /
(pips * pip * volume), which cTrader held constant over a run. ETHUSD's smallest deal was 0.04, so its
minimum is taken as the 0.01 step. Index tick sizes are approximate: they only feed minStopBuffer =
max(3 x spread, 10 x tick), where the spread term dominates.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from app.cbot_presets import PRESETS


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    pip: float
    tick_size: float
    min_units: float
    step_units: float
    comm_per_unit: float   # USD per unit, round trip
    usd_per_quote: float   # cTrader's quote -> USD factor in the 2026 reports

    @property
    def pip_value(self) -> float:
        """Symbol.PipValue: USD per pip for one unit."""
        return self.pip * self.usd_per_quote


_JPY = dict(pip=0.01, tick_size=0.001, min_units=1000, step_units=1000, usd_per_quote=0.0062963)
_FX = dict(pip=0.0001, tick_size=0.00001, min_units=1000, step_units=1000)
_INDEX = dict(pip=0.1, tick_size=0.1, min_units=0.1, step_units=0.1, comm_per_unit=0.0)

SPECS = {s.symbol: s for s in [
    SymbolSpec("AUDJPY", comm_per_unit=4.167e-5, **_JPY),
    SymbolSpec("EURJPY", comm_per_unit=6.850e-5, **_JPY),
    SymbolSpec("GBPJPY", comm_per_unit=8.000e-5, **_JPY),
    SymbolSpec("USDJPY", comm_per_unit=6.000e-5, **_JPY),
    SymbolSpec("AUDUSD", comm_per_unit=4.159e-5, usd_per_quote=1.0, **_FX),
    SymbolSpec("EURUSD", comm_per_unit=6.733e-5, usd_per_quote=1.0, **_FX),
    SymbolSpec("GBPUSD", comm_per_unit=8.000e-5, usd_per_quote=1.0, **_FX),
    SymbolSpec("USDCAD", comm_per_unit=6.000e-5, usd_per_quote=0.707707, **_FX),
    SymbolSpec("XAUUSD", pip=0.01, tick_size=0.01, min_units=1, step_units=1, comm_per_unit=0.2579, usd_per_quote=1.0),
    SymbolSpec("BTCUSD", pip=0.01, tick_size=0.01, min_units=0.01, step_units=0.01, comm_per_unit=0.0, usd_per_quote=1.0),
    SymbolSpec("ETHUSD", pip=0.01, tick_size=0.01, min_units=0.01, step_units=0.01, comm_per_unit=0.0, usd_per_quote=1.0),
    SymbolSpec("DE40", usd_per_quote=1.13765, **_INDEX),
    SymbolSpec("UK100", usd_per_quote=1.3218, **_INDEX),
    SymbolSpec("US30", usd_per_quote=1.0, **_INDEX),
    SymbolSpec("USTEC", usd_per_quote=1.0, **_INDEX),
]}

# Swap in USD per unit per rollover (long, short) and the New York weekday whose rollover counts three
# times, least-squares fitted on the 2026 macro-off reports #43-#56, #93 (rollover at 17:00 New York).
# Rates are held constant, so 2025 swaps are approximate; GetZeroLossStopLossPrice's negative-swap term
# is not modelled.
NY = ZoneInfo("America/New_York")
ROLLOVER_HOUR_NY = 17
SWAPS = {
    "AUDJPY": (4.358e-05, -6.976e-05, 2),
    "AUDUSD": (-2.86e-05, -3.401e-05, 2),
    "BTCUSD": (-38.61, 0.0, 4),
    "DE40": (-5.662, -1.103, 4),
    "ETHUSD": (-1.135, 0.0, 4),
    "EURJPY": (2.525e-05, -5.896e-05, 2),
    "EURUSD": (-7.856e-05, 1.487e-05, 2),
    "GBPJPY": (5.91e-05, -0.0001273, 2),
    "GBPUSD": (-4.193e-05, -2.92e-05, 2),
    "UK100": (-3.05, -0.1071, 4),
    "US30": (-12.14, -0.2188, 4),
    "USDCAD": (2.229e-05, -6.908e-05, 2),
    "USDJPY": (4.914e-05, -0.0001035, 2),
    "USTEC": (-7.11, -0.1238, 4),
    "XAUUSD": (-0.5771, 0.3971, 2),
}


def rollovers(entry_ms: int, exit_ms: int, triple_weekday: int) -> int:
    """Weekday 17:00 New York rollovers in (entry, exit], the triple weekday counting 3."""
    a = datetime.fromtimestamp(entry_ms / 1000, timezone.utc).astimezone(NY)
    b = datetime.fromtimestamp(exit_ms / 1000, timezone.utc).astimezone(NY)
    d = a.replace(hour=ROLLOVER_HOUR_NY, minute=0, second=0, microsecond=0)
    if d <= a:
        d += timedelta(days=1)
    n = 0
    while d <= b:
        if d.weekday() < 5:
            n += 3 if d.weekday() == triple_weekday else 1
        d += timedelta(days=1)
    return n


def swap_usd(symbol: str, side: int, units: float, entry_ms: int, exit_ms: int) -> float:
    long_rate, short_rate, triple = SWAPS[symbol]
    return (long_rate if side > 0 else short_rate) * units * rollovers(entry_ms, exit_ms, triple)


# [Parameter] DefaultValue in cBot/FlowRsiBot.cs; test_specs checks every entry against the source.
CBOT_DEFAULTS = {
    "FastRsiPeriod": 7, "SlowRsiPeriod": 14, "RsiCrossLookbackBars": 3,
    "RsiBullishCrossMin": 25.0, "RsiBullishCrossMax": 50.0, "RsiBearishCrossMin": 50.0, "RsiBearishCrossMax": 75.0,
    "EnableSmcFilter": True, "SwingLookback": 5, "EnableFvgDetection": True, "FvgMinPips": 2.0,
    "EquilibriumThreshold": 0.5, "EnableLiquiditySweepFilter": True, "EnableMacroTmsFilter": True,
    "RiskPercentage": 0.5, "MaxRiskPerTradeMoney": 50.0, "MaxMinLotRiskMultiple": 1.5,
    "AtrPeriod": 14, "AtrSlMultiplier": 1.5, "TargetRiskReward": 1.5, "MinSlFloorPips": 15.0, "MaxSpreadPips": 30.0,
    "EnableBreakEven": True, "BreakEvenTriggerRr": 1.0, "MinBreakEvenPips": 10.0, "BreakEvenExtraPips": 0.5,
    "EnableTrailingStop": True, "TrailingStopTriggerRr": 1.2, "TrailingStopDistancePips": 25.0,
    "EnablePartialClose": True, "PartialCloseRatio": 0.5, "RemoveTpOnTrailing": True,
}


def flowrsi_params(symbol: str, **overrides) -> dict:
    """cBot defaults, then the live preset for the symbol, then the overrides."""
    params = dict(CBOT_DEFAULTS)
    params.update(PRESETS[("flowrsi", symbol)]["params"])
    params.update(overrides)
    return params


def normalize_units(units: float, spec: SymbolSpec) -> float:
    """Symbol.NormalizeVolumeInUnits with its default RoundingMode.ToNearest."""
    return round(float(np.floor(units / spec.step_units + 0.5)) * spec.step_units, 8)


def size_units(equity: float, sl_pips: float, spec: SymbolSpec, p: dict) -> float:
    """CalculateDynamicVolumeInUnits (FlowRsiBot.cs:1812); 0.0 when a guardrail refuses the entry."""
    risk = min(equity * p["RiskPercentage"] / 100.0, p["MaxRiskPerTradeMoney"])
    units = max(normalize_units(risk / (sl_pips * spec.pip_value), spec), spec.min_units)
    final = units * sl_pips * spec.pip_value
    if final > p["MaxRiskPerTradeMoney"]:
        return 0.0
    if p["MaxMinLotRiskMultiple"] > 0 and risk > 0 and final > risk * p["MaxMinLotRiskMultiple"]:
        return 0.0
    return units
