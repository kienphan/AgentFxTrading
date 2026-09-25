"""Gate: the simulator must replay C0 + X2 the way cTrader ran jobs #43-#56 ($2000) and #93 (XAU, $10k)."""
import argparse
import json
import math
import sys
from datetime import date, datetime, timezone

from research.bars import build_bars
from research.signals import Market, legacy
from research.sim import make_levels, run_account
from research.specs import SPECS, flowrsi_params, size_units
from research.ticks import DATA_DIR, day_ms, load_ticks

JOBS = {43: "AUDJPY", 44: "AUDUSD", 45: "BTCUSD", 46: "DE40", 47: "ETHUSD", 48: "EURJPY", 49: "EURUSD",
        50: "GBPJPY", 51: "GBPUSD", 52: "UK100", 53: "US30", 54: "USDCAD", 55: "USDJPY", 56: "USTEC",
        93: "XAUUSD"}
START, END, WARMUP_FROM = date(2026, 1, 1), date(2026, 9, 24), date(2025, 12, 25)
BAR_MS = 15 * 60_000


def ctrader_positions(report: dict):
    """History deals grouped into positions: (entry_ms, side, units, net, r, balance_before),
    r = net / 0.5 % of the balance before."""
    groups = {}
    for d in sorted(report["history"]["items"], key=lambda d: d["closeTime"]):
        key = (d["entryTime"], d["direction"], d["entryPrice"])
        g = groups.setdefault(key, {"units": 0.0, "net": 0.0, "before": d["balance"] - d["net"]})
        g["units"] += d["volume"]
        g["net"] += d["net"]
    return [(int(k[0]), 1 if k[1] == "buy" else -1, g["units"], g["net"], g["net"] / (0.005 * g["before"]),
             g["before"]) for k, g in sorted(groups.items())]


def match(ct, sim, tol_ms: int = BAR_MS):
    used, pairs = set(), []
    for row in ct:
        best = None
        for k, tr in enumerate(sim):
            if k in used or tr.side != row[1]:
                continue
            dt = abs(tr.entry_ts - row[0])
            if dt <= tol_ms and (best is None or dt < best[0]):
                best = (dt, k)
        if best is not None:
            used.add(best[1])
            pairs.append((row, sim[best[1]]))
    return pairs


def replay(job: int, symbol: str):
    report = json.loads((DATA_DIR / "reports" / f"{job}.json").read_text())
    balance = float(report["main"]["startingCapital"])
    over = {"EnableMacroTmsFilter": False}
    if job == 93:
        over["MaxRiskPerTradeMoney"] = 1000.0
    spec, p = SPECS[symbol], flowrsi_params(symbol, **over)
    t = load_ticks(symbol, WARMUP_FROM, END)
    m = Market(build_bars(t, 15), build_bars(t, 60), build_bars(t, 240))
    trades, refused = run_account(t, m.m15, legacy(m, spec, p), make_levels(m.m15, spec, p, "swing"),
                                  spec, p, "X2", balance, day_ms(START))
    return ctrader_positions(report), trades, refused, spec, p


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stamp(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="print unmatched cTrader positions of this symbol")
    ap.add_argument("--only", nargs="*", help="symbols to replay (default: all 15)")
    args = ap.parse_args(argv)
    ok, ct_r, sim_r, vol_ok, vol_n, vol_eq_ok = True, [], [], 0, 0, 0
    for job, symbol in JOBS.items():
        if args.only and symbol not in args.only:
            continue
        ct, sim, refused, spec, p = replay(job, symbol)
        pairs = match(ct, sim)
        rate = len(pairs) / len(ct) if ct else 1.0
        vol_n += len(pairs)
        vol_ok += sum(1 for row, tr in pairs if math.isclose(row[2], tr.units, rel_tol=1e-9, abs_tol=1e-9))
        # The same stop sized at cTrader's own balance: the sizing logic without the equity path.
        vol_eq_ok += sum(1 for row, tr in pairs
                         if math.isclose(row[2], size_units(row[5], tr.sl_pips, spec, p), rel_tol=1e-9, abs_tol=1e-9))
        ct_r += [row[4] for row in ct]
        sim_r += [tr.r for tr in sim]
        ok &= rate >= 0.90
        print(f"#{job} {symbol:7s} cTrader {len(ct):4d}  sim {len(sim):4d}  matched {rate:6.1%}  "
              f"mR {_mean([r[4] for r in ct]):+.3f} vs {_mean([tr.r for tr in sim]):+.3f}  refused {refused}")
        if args.show == symbol:
            matched = {id(row) for row, _ in pairs}
            for row in [r for r in ct if id(r) not in matched][:15]:
                near = min(sim, key=lambda tr: abs(tr.entry_ts - row[0]), default=None)
                print(f"   unmatched {_stamp(row[0])} side {row[1]:+d} units {row[2]:g}   nearest sim "
                      + (f"{_stamp(near.entry_ts)} side {near.side:+d} units {near.units:g}" if near else "-"))
    # The volume gate sizes each matched stop at cTrader's own balance (user decision 2026-09-25): a few
    # extra or missing positions shift the simulated equity, and one volume step then flips the rest.
    vol_rate = vol_eq_ok / vol_n if vol_n else 0.0
    diff = _mean(sim_r) - _mean(ct_r)
    ok &= vol_rate >= 0.95 and abs(diff) <= 0.03
    print(f"volume on the simulated equity path: {vol_ok / vol_n if vol_n else 0.0:.1%} (information)")
    print(f"pooled: volume match at cTrader's balance {vol_rate:.1%}  mR cTrader {_mean(ct_r):+.3f} sim {_mean(sim_r):+.3f} (diff {diff:+.3f})")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
