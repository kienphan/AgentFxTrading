"""Entry signals on closed M15 bars: +1 buy, -1 sell, 0 none; index i = the bar that just closed."""
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

from research.bars import Bars
from research.indicators import adx, bollinger, ema, prior_max, prior_min, rsi
from research.specs import SymbolSpec

WARMUP_BARS = 20   # EvaluateStrategySignals returns while index < 20


@dataclass
class Market:
    m15: Bars
    h1: Bars
    h4: Bars


def _any_last(x: np.ndarray, lookback: int) -> np.ndarray:
    """out[i] = any(x[i-lookback+1 .. i])."""
    cs = np.cumsum(x.astype(np.int64))
    prev = np.r_[np.zeros(lookback, np.int64), cs[:-lookback]]
    return (cs - prev) > 0


def rsi_cross_signals(f: np.ndarray, s: np.ndarray, p: dict) -> Tuple[np.ndarray, np.ndarray]:
    """FlowRsiBot.cs:717-746: a fast/slow cross inside the zone within the lookback, confirmed now."""
    n = len(f)
    up, down = np.zeros(n, bool), np.zeros(n, bool)
    up[1:] = (f[:-1] <= s[:-1]) & (f[1:] > s[1:])
    down[1:] = (f[:-1] >= s[:-1]) & (f[1:] < s[1:])
    bull_x = up & (s >= p["RsiBullishCrossMin"]) & (s <= p["RsiBullishCrossMax"])
    bear_x = down & (s >= p["RsiBearishCrossMin"]) & (s <= p["RsiBearishCrossMax"])
    lookback = max(1, int(p["RsiCrossLookbackBars"]))
    return _any_last(bull_x, lookback) & (f > s), _any_last(bear_x, lookback) & (f < s)


def smc_flags(b: Bars, p: dict, pip: float) -> Dict[str, np.ndarray]:
    """FlowRsiBot.cs:750-788: premium/discount, 3-bar FVG and liquidity sweeps."""
    n = len(b)
    window = int(p["SwingLookback"]) * 3
    hi, lo = prior_max(b.h, window), prior_min(b.l, window)
    rng, eq = hi - lo, p["EquilibriumThreshold"]
    with np.errstate(invalid="ignore"):
        disc = (rng > 0) & (b.c <= lo + rng * eq)
        prem = (rng > 0) & (b.c >= lo + rng * (1.0 - eq))
    fvg_b, fvg_s = np.zeros(n, bool), np.zeros(n, bool)
    if p["EnableFvgDetection"]:
        gap = p["FvgMinPips"] * pip
        fvg_b[2:] = (b.l[2:] - b.h[:-2] >= gap) & (b.c[2:] >= b.h[:-2])
        fvg_s[2:] = (b.l[:-2] - b.h[2:] >= gap) & (b.c[2:] <= b.l[:-2])
    sw_l, sw_h = np.zeros(n, bool), np.zeros(n, bool)
    if p["EnableLiquiditySweepFilter"]:
        lookback = max(1, int(p["RsiCrossLookbackBars"]))
        for i in range(window, n):
            k0 = max(1, i - lookback + 1)
            sw_l[i] = bool(np.any((b.l[k0:i + 1] < lo[i]) & (b.c[k0:i + 1] > lo[i])))
            sw_h[i] = bool(np.any((b.h[k0:i + 1] > hi[i]) & (b.c[k0:i + 1] < hi[i])))
    return {"disc": disc, "prem": prem, "fvg_b": fvg_b, "fvg_s": fvg_s, "sw_l": sw_l, "sw_h": sw_h}


def legacy(m: Market, spec: SymbolSpec, p: dict) -> np.ndarray:
    """The live FlowRSI entry (steps 1-5) with the Macro TMS filter off."""
    if p.get("EnableMacroTmsFilter", True):
        raise ValueError("legacy() replicates FlowRSI with EnableMacroTmsFilter=false only")
    b = m.m15
    bull, bear = rsi_cross_signals(rsi(b.c, int(p["FastRsiPeriod"])), rsi(b.c, int(p["SlowRsiPeriod"])), p)
    buy, sell = bull.copy(), bear.copy()
    if p["EnableSmcFilter"]:
        f = smc_flags(b, p, spec.pip)
        buy &= f["disc"] | f["fvg_b"] | f["sw_l"]
        sell &= f["prem"] | f["fvg_s"] | f["sw_h"]
    sig = np.where(buy, 1, np.where(sell, -1, 0)).astype(np.int8)
    sig[:WARMUP_BARS] = 0
    return sig


HTF_EMA = 50


def htf_index(m15: Bars, htf: Bars) -> np.ndarray:
    """For each M15 bar, the last higher-timeframe bar already closed at the M15 close (-1 if none)."""
    return np.searchsorted(htf.close_time, m15.close_time, side="right") - 1


def htf_trend(htf: Bars, period: int = HTF_EMA) -> np.ndarray:
    """+1: close above a rising EMA; -1: below a falling one; 0 otherwise or before `period` bars."""
    e = ema(htf.c, period)
    rising = np.r_[False, e[1:] > e[:-1]]
    falling = np.r_[False, e[1:] < e[:-1]]
    out = np.where((htf.c > e) & rising, 1, np.where((htf.c < e) & falling, -1, 0)).astype(np.int8)
    out[:period] = 0
    return out


def trend_on_m15(m15: Bars, htf: Bars) -> np.ndarray:
    k = htf_index(m15, htf)
    trend = htf_trend(htf)
    return np.where(k >= 0, trend[np.maximum(k, 0)], 0).astype(np.int8)


def pullback(r: np.ndarray, trend: np.ndarray, lo: float = 40.0, hi: float = 60.0) -> np.ndarray:
    """E2: with the trend, RSI closes back above `lo` (buy) / below `hi` (sell)."""
    sig = np.zeros(len(r), np.int8)
    buy = (trend[1:] > 0) & (r[:-1] < lo) & (r[1:] >= lo)
    sell = (trend[1:] < 0) & (r[:-1] > hi) & (r[1:] <= hi)
    sig[1:] = np.where(buy, 1, np.where(sell, -1, 0))
    return sig


def rsi2_extreme(r2: np.ndarray, trend=None, lo: float = 10.0, hi: float = 90.0) -> np.ndarray:
    """E3: RSI(2) below `lo` buys, above `hi` sells; optionally only with the trend."""
    buy, sell = r2 < lo, r2 > hi
    if trend is not None:
        buy &= trend > 0
        sell &= trend < 0
    return np.where(buy, 1, np.where(sell, -1, 0)).astype(np.int8)


def bb_fade(c: np.ndarray, lower: np.ndarray, upper: np.ndarray, adx_arr=None, adx_max: float = 20.0) -> np.ndarray:
    """E4: a close outside the band, then a close back inside -> trade back toward the middle."""
    sig = np.zeros(len(c), np.int8)
    with np.errstate(invalid="ignore"):
        buy = (c[:-1] < lower[:-1]) & (c[1:] > lower[1:])
        sell = (c[:-1] > upper[:-1]) & (c[1:] < upper[1:])
        if adx_arr is not None:
            calm = adx_arr[1:] < adx_max
            buy &= calm
            sell &= calm
    sig[1:] = np.where(buy, 1, np.where(sell, -1, 0))
    return sig


def donchian_breakout(c: np.ndarray, hh: np.ndarray, ll: np.ndarray, trend: np.ndarray) -> np.ndarray:
    """E5: close beyond the prior N-bar high/low in the trend's direction."""
    with np.errstate(invalid="ignore"):
        buy = (c > hh) & (trend > 0)
        sell = (c < ll) & (trend < 0)
    return np.where(buy, 1, np.where(sell, -1, 0)).astype(np.int8)


def reverse_extreme(sig: np.ndarray, slow_rsi: np.ndarray, lo: float = 30.0, hi: float = 70.0) -> np.ndarray:
    """E1b: fade only the legacy crosses taken with the slow RSI below `lo` / above `hi`."""
    keep = ((sig > 0) & (slow_rsi < lo)) | ((sig < 0) & (slow_rsi > hi))
    return np.where(keep, -sig, 0).astype(np.int8)


def random_like(sig: np.ndarray, seed: int) -> np.ndarray:
    """C2: random entries at the same rate as `sig`, random direction."""
    rng = np.random.default_rng(seed)
    rate = np.count_nonzero(sig) / max(len(sig), 1)
    fire = rng.random(len(sig)) < rate
    side = rng.choice(np.array([-1, 1], np.int8), len(sig))
    return np.where(fire, side, 0).astype(np.int8)


NAMES = ("C0", "C1", "C2", "E1a", "E1b", "E2a", "E2b", "E3a", "E3b", "E4a", "E4b", "E5a", "E5b")
_LEGACY_KEYS = ("FastRsiPeriod", "SlowRsiPeriod", "RsiCrossLookbackBars", "RsiBullishCrossMin", "RsiBullishCrossMax",
                "RsiBearishCrossMin", "RsiBearishCrossMax", "EnableSmcFilter", "SwingLookback", "EnableFvgDetection",
                "FvgMinPips", "EquilibriumThreshold", "EnableLiquiditySweepFilter")


def build(name: str, m: Market, spec: SymbolSpec, p: dict, seed=None, cache=None) -> np.ndarray:
    """The entry signal of config `name` (spec section 2). `cache` shares the legacy signal across configs."""
    cache = {} if cache is None else cache
    key = tuple(p[k] for k in _LEGACY_KEYS)

    def base():
        if key not in cache:
            cache[key] = legacy(m, spec, p)
        return cache[key]

    b = m.m15
    if name in ("C0", "C1"):
        sig = base()
    elif name == "C2":
        sig = random_like(base(), seed)
    elif name == "E1a":
        sig = -base()
    elif name == "E1b":
        sig = reverse_extreme(base(), rsi(b.c, int(p["SlowRsiPeriod"])))
    elif name in ("E2a", "E2b"):
        sig = pullback(rsi(b.c, 14), trend_on_m15(b, m.h1 if name == "E2a" else m.h4))
    elif name == "E3a":
        sig = rsi2_extreme(rsi(b.c, 2), trend_on_m15(b, m.h1))
    elif name == "E3b":
        sig = rsi2_extreme(rsi(b.c, 2), None)
    elif name in ("E4a", "E4b"):
        lower, _, upper = bollinger(b.c, 20, 2.0)
        sig = bb_fade(b.c, lower, upper, adx(b.h, b.l, b.c, 14) if name == "E4a" else None)
    elif name in ("E5a", "E5b"):
        n = 20 if name == "E5a" else 48
        sig = donchian_breakout(b.c, prior_max(b.h, n), prior_min(b.l, n), trend_on_m15(b, m.h4))
    else:
        raise ValueError(f"unknown config {name}")
    sig = sig.astype(np.int8).copy()
    sig[:WARMUP_BARS] = 0
    return sig
