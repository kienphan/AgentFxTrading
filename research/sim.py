"""One pair's signals through FlowRsiBot's order placement and exits, tick by tick."""
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np

from research.bars import Bars
from research.indicators import atr_sma, prior_max, prior_min
from research.specs import SymbolSpec, normalize_units, size_units, swap_usd
from research.ticks import Ticks

Levels = Callable[[int, int, float, float], Tuple[float, float]]


@dataclass
class Trade:
    side: int            # +1 buy, -1 sell
    entry_tick: int
    entry_ts: int
    entry: float
    units: float
    sl_pips: float       # initial stop distance from the fill price (_initialSlDistances)
    risk_usd: float      # RiskPercentage of equity at entry: the R unit
    exit_tick: int = -1
    net: float = 0.0
    deals: int = 0

    @property
    def r(self) -> float:
        return self.net / self.risk_usd


def make_levels(bars: Bars, spec: SymbolSpec, p: dict, sl_mode: str) -> Levels:
    """EvaluateStrategySignals step 6 (FlowRsiBot.cs:833-906): technical SL/TP at the bar-close tick.
    sl_mode "swing" = Technical_Swing (the presets), "atr" = ATR_Multiplier (exit X1)."""
    atr = atr_sma(bars.h, bars.l, bars.c, int(p["AtrPeriod"]))
    lookback = int(p["SwingLookback"]) * 3
    hi, lo = prior_max(bars.h, lookback), prior_min(bars.l, lookback)
    pip = spec.pip
    floor = p["MinSlFloorPips"] if p["MinSlFloorPips"] > 0 else 15.0
    mult, rr = p["AtrSlMultiplier"], p["TargetRiskReward"]

    def levels(i: int, side: int, bid: float, ask: float) -> Tuple[float, float]:
        a = atr[i]
        min_sl = max(floor, a / pip)
        if side > 0:
            swing = lo[i] - 0.5 * a if 0 < lo[i] < bid else bid - mult * a
            sl = bid - mult * a if sl_mode == "atr" else swing
            if (bid - sl) / pip < min_sl:
                sl = bid - min_sl * pip
            return sl, ask + (bid - sl) * rr
        swing = hi[i] + 0.5 * a if hi[i] > ask else ask + mult * a
        sl = ask + mult * a if sl_mode == "atr" else swing
        if (sl - ask) / pip < min_sl:
            sl = ask + min_sl * pip
        return sl, bid - (sl - ask) * rr

    return levels


def place_order(side: int, tech_sl: float, tech_tp: float, bid: float, ask: float, equity: float,
                spec: SymbolSpec, p: dict) -> Optional[Tuple[float, float, float]]:
    """ExecuteTechnicalOrder (FlowRsiBot.cs:1718-1788): (units, sl, tp), or None when refused."""
    pip = spec.pip
    price = ask if side > 0 else bid
    sl, tp = tech_sl, tech_tp
    sl_pips = abs(price - sl) / pip
    floor = p["MinSlFloorPips"] if p["MinSlFloorPips"] > 0 else 15.0
    if sl_pips < floor:
        sl_pips = floor
        sl = bid - floor * pip if side > 0 else ask + floor * pip
        min_tp = floor * p["TargetRiskReward"] * pip
        if side > 0 and tp - ask < min_tp:
            tp = ask + min_tp
        elif side < 0 and bid - tp < min_tp:
            tp = bid - min_tp
    units = size_units(equity, sl_pips, spec, p)
    if (side > 0 and sl >= bid) or (side < 0 and sl <= ask):
        return None
    buffer = max((ask - bid) * 3, spec.tick_size * 10)
    if side > 0 and sl >= bid - buffer:
        sl = bid - buffer - 2 * pip
    elif side < 0 and sl <= ask + buffer:
        sl = ask + buffer + 2 * pip
    clamped = abs(price - sl) / pip
    if abs(clamped - sl_pips) > 0.01:
        units = size_units(equity, clamped, spec, p)
    if units <= 0:
        return None
    return units, sl, tp


def _first_hit(price: np.ndarray, start: int, lo: float, hi: float) -> int:
    """First index >= start with price <= lo or price >= hi; len(price) if none."""
    n, size = len(price), 4096
    while start < n:
        seg = price[start:start + size]
        hit = np.flatnonzero((seg <= lo) | (seg >= hi))
        if hit.size:
            return start + int(hit[0])
        start += size
        size *= 2
    return n


def _close(t: Ticks, tr: Trade, j: int, price: float, units: float, spec: SymbolSpec) -> None:
    """One closing deal: gross, round-trip commission and the swap of the rollovers it was held over."""
    tr.net += (price - tr.entry) * tr.side * units * spec.usd_per_quote - spec.comm_per_unit * units
    tr.net += swap_usd(spec.symbol, tr.side, units, tr.entry_ts, int(t.ts[j]))
    tr.deals += 1
    tr.exit_tick = j


def _run_fixed(t: Ticks, tr: Trade, sl: float, tp: float, spec: SymbolSpec) -> None:
    """Exit X1: only the broker's SL/TP; a hit fills at that tick's closing-side price."""
    if tr.side > 0:
        j = _first_hit(t.bid, tr.entry_tick + 1, sl, tp)
    else:
        j = _first_hit(t.ask, tr.entry_tick + 1, tp, sl)
    j = min(j, len(t) - 1)
    _close(t, tr, j, t.bid[j] if tr.side > 0 else t.ask[j], tr.units, spec)


def run_account(t: Ticks, bars: Bars, sig: np.ndarray, levels: Levels, spec: SymbolSpec, p: dict,
                exit_mode: str, balance: float, start_ms: Optional[int] = None) -> Tuple[List[Trade], int]:
    """sig[i] in {-1, 0, 1} for closed bar i. One position at a time (MaxPositions=1); a signal whose
    close tick falls inside an open position is dropped, as OnBarClosed does with hasOpenPos."""
    trades: List[Trade] = []
    refused = 0
    equity = balance
    busy_until = -1
    for i in np.flatnonzero(sig):
        e = int(bars.event_tick[i])
        if e >= len(t) or e < busy_until:
            continue
        if start_ms is not None and t.ts[e] < start_ms:
            continue
        b, a = float(t.bid[e]), float(t.ask[e])
        if (a - b) / spec.pip > p["MaxSpreadPips"]:
            continue
        side = int(sig[i])
        tech_sl, tech_tp = levels(int(i), side, b, a)
        order = place_order(side, tech_sl, tech_tp, b, a, equity, spec, p)
        if order is None:
            refused += 1
            continue
        units, sl, tp = order
        entry = a if side > 0 else b
        tr = Trade(side, e, int(t.ts[e]), entry, units, abs(entry - sl) / spec.pip,
                   equity * p["RiskPercentage"] / 100.0)
        if exit_mode == "X1":
            _run_fixed(t, tr, sl, tp, spec)
        else:
            _run_flowrsi(t, tr, sl, tp, spec, p)
        equity += tr.net
        busy_until = tr.exit_tick
        trades.append(tr)
    return trades, refused


def _modify_sl(side: int, sl: float, target: float, bid: float, ask: float, buffer: float, thr: float):
    """SafeModifyPosition for the BreakEven move: the new stop, or None if nothing would change."""
    final = target
    if (side > 0 and final < sl) or (side < 0 and final > sl):
        final = sl
    if (side > 0 and final >= bid - buffer) or (side < 0 and final <= ask + buffer):
        final = sl
    return final if abs(final - sl) > thr else None


def _modify_trail(side: int, sl: float, tp: Optional[float], cand: float, remove_tp: bool,
                  bid: float, ask: float, buffer: float, thr: float):
    """SafeModifyPosition for the trailing stop: (sl, tp, changed)."""
    final_sl = cand
    if (side > 0 and cand < sl) or (side < 0 and cand > sl):
        final_sl = sl
    if (side > 0 and final_sl >= bid - buffer) or (side < 0 and final_sl <= ask + buffer):
        final_sl = sl
    final_tp = None if remove_tp else tp
    if abs(final_sl - sl) <= thr and (final_tp is None) == (tp is None):
        return sl, tp, False
    return final_sl, final_tp, True


def _run_flowrsi(t: Ticks, tr: Trade, sl: float, tp: Optional[float], spec: SymbolSpec, p: dict) -> None:
    """Exit X2: broker SL/TP, then ManageExits (FlowRsiBot.cs:1846-2003) on every tick."""
    pip, side, entry = spec.pip, tr.side, tr.entry
    units, init_sl = tr.units, tr.sl_pips
    fee_pips = spec.comm_per_unit / spec.pip_value            # |Commissions| x 2 per unit, in pips
    zero_loss = entry + side * (fee_pips + max(0.0, p["BreakEvenExtraPips"])) * pip
    thr = 0.5 * pip
    be_on, trail_on = p["EnableBreakEven"], p["EnableTrailingStop"]
    min_be = p["MinBreakEvenPips"] if p["MinBreakEvenPips"] > 0 else 10.0
    be_done = partial_done = False
    bid, ask, n = t.bid, t.ask, len(t)

    def trail_trigger(cur_tp):
        trig = p["TrailingStopTriggerRr"]
        if cur_tp is not None:
            tp_rr = abs(cur_tp - entry) / pip / init_sl
            if tp_rr > 0 and trig >= tp_rr:
                trig = tp_rr * 0.8
        return trig

    # Until BE or trailing can first trigger, only the broker's SL/TP can act: jump there.
    first = min(max(p["BreakEvenTriggerRr"] * init_sl, min_be) if be_on else np.inf,
                trail_trigger(tp) * init_sl if trail_on else np.inf)
    if side > 0:
        j = _first_hit(bid, tr.entry_tick + 1, sl, min(tp, entry + first * pip))
    else:
        j = _first_hit(ask, tr.entry_tick + 1, max(tp, entry - first * pip), sl)

    while j < n:
        b, a = float(bid[j]), float(ask[j])
        px = b if side > 0 else a
        if (side > 0 and (px <= sl or (tp is not None and px >= tp))) or \
           (side < 0 and (px >= sl or (tp is not None and px <= tp))):
            _close(t, tr, j, px, units, spec)
            return
        pnl = (b - entry) / pip if side > 0 else (entry - a) / pip
        rr = pnl / init_sl
        buffer = max((a - b) * 3, spec.tick_size * 10)

        if be_on and not be_done and rr >= p["BreakEvenTriggerRr"] and pnl >= min_be \
                and (zero_loss > sl if side > 0 else zero_loss < sl):
            new_sl = _modify_sl(side, sl, zero_loss, b, a, buffer, thr)
            if new_sl is not None:
                sl, be_done = new_sl, True
                ratio = p["PartialCloseRatio"]
                if p["EnablePartialClose"] and 0 < ratio < 1 and not partial_done:
                    vol = normalize_units(units * ratio, spec)
                    if vol >= spec.min_units and units - vol >= spec.min_units:
                        _close(t, tr, j, px, vol, spec)
                        units -= vol
                        partial_done = True

        if trail_on and rr >= trail_trigger(tp):
            dist = max(p["TrailingStopDistancePips"], init_sl * (0.6 if rr >= 2.5 else 1.0))
            step = max(1.0, dist * 0.1) * pip
            remove_tp = p["RemoveTpOnTrailing"] and partial_done
            if side > 0:
                cand = max(b - dist * pip, zero_loss)
                go = (cand >= sl + step or (remove_tp and tp is not None)) and cand < b
            else:
                cand = min(a + dist * pip, zero_loss)
                go = (cand <= sl - step or (remove_tp and tp is not None)) and cand > a
            if go:
                sl, tp, changed = _modify_trail(side, sl, tp, cand, remove_tp, b, a, buffer, thr)
                if changed and side * (sl - entry) / pip >= fee_pips:
                    be_done = True
        j += 1

    j = n - 1
    _close(t, tr, j, float(bid[j]) if side > 0 else float(ask[j]), units, spec)
