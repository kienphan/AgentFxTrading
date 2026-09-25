"""Screen every entry config over 15 pairs x 2025/2026 on ticks and apply the pre-registered pass bar."""
import argparse
import csv
import math
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from research.bars import build_bars
from research.signals import Market, build
from research.sim import make_levels, run_account
from research.specs import SPECS, flowrsi_params
from research.ticks import day_ms, load_ticks

RESULTS = Path(__file__).resolve().parent / "results"
PAIRS = ["AUDJPY", "AUDUSD", "BTCUSD", "DE40", "ETHUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD",
         "UK100", "US30", "USDCAD", "USDJPY", "USTEC", "XAUUSD"]
JPY = {"AUDJPY", "EURJPY", "GBPJPY", "USDJPY"}
FX = {"AUDUSD", "EURUSD", "GBPUSD", "USDCAD"}
YEARS = {2025: (date(2025, 1, 1), date(2025, 12, 31)), 2026: (date(2026, 1, 1), date(2026, 9, 24))}
P3_START_MS = day_ms(date(2026, 6, 1))
PERIODS = ("P1", "P2", "P3")
MONTHS = {"P1": 12.0, "P2": 5.0, "P3": 116 / 30.44}
BALANCE = 2000.0
CONTROLS = ("C0", "C1")
CANDIDATES = ("E1a", "E1b", "E2a", "E2b", "E3a", "E3b", "E4a", "E4b", "E5a", "E5b")
EXITS = ("X1", "X2")
SEEDS = range(20)


def params_for(symbol: str, name: str, exit_mode: str) -> dict:
    over = {"EnableMacroTmsFilter": False}
    if exit_mode == "X1":
        over.update(EnableBreakEven=False, EnableTrailingStop=False, EnablePartialClose=False)
    if name == "C1" and symbol in JPY:
        over["MinSlFloorPips"] = 30.0
    elif name == "C1" and symbol in FX:
        over["MinSlFloorPips"] = 25.0
    if symbol == "XAUUSD" and name != "C0":
        over["MinSlFloorPips"] = 500.0
    return flowrsi_params(symbol, **over)


def _period(entry_ms: int, year: int) -> str:
    if year == 2025:
        return "P1"
    return "P2" if entry_ms < P3_START_MS else "P3"


def run_pair(symbol: str):
    spec = SPECS[symbol]
    trades, refusals = [], []
    for year, (start, end) in YEARS.items():
        t = load_ticks(symbol, start - timedelta(days=7), end)
        if len(t) == 0:
            print(f"WARNING {symbol} {year}: no ticks")
            continue
        m = Market(build_bars(t, 15), build_bars(t, 60), build_bars(t, 240))
        cache = {}
        jobs = [(n, x, None) for n in CONTROLS + CANDIDATES for x in EXITS] + \
               [("C2", x, s) for x in EXITS for s in SEEDS]
        for name, exit_mode, seed in jobs:
            p = params_for(symbol, name, exit_mode)
            sig = build(name, m, spec, p, seed=seed, cache=cache)
            levels = make_levels(m.m15, spec, p, "atr" if exit_mode == "X1" else "swing")
            done, refused = run_account(t, m.m15, sig, levels, spec, p, exit_mode, BALANCE, day_ms(start))
            config = f"{name}/{exit_mode}" + (f"/s{seed}" if seed is not None else "")
            trades += [{"config": config, "symbol": symbol, "period": _period(tr.entry_ts, year),
                        "entry_ts": tr.entry_ts, "side": tr.side, "units": tr.units, "r": tr.r,
                        "net": tr.net, "deals": tr.deals} for tr in done]
            refusals.append({"config": config, "symbol": symbol, "year": year, "refused": refused})
        print(f"{symbol} {year}: done", flush=True)
    return trades, refusals


def mean_t(rs):
    n = len(rs)
    if n < 2:
        return n, (rs[0] if n else 0.0), 0.0
    m = sum(rs) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1))
    return n, m, (m / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def summarize(rows):
    by_config = {}
    for row in rows:
        by_config.setdefault(row["config"], []).append(row)
    out = {}
    for config, rs in by_config.items():
        s = {P: mean_t([x["r"] for x in rs if x["period"] == P]) for P in PERIODS}
        per_pair = {}
        for x in rs:
            per_pair.setdefault(x["symbol"], []).append(x["r"])
        s["pairs_positive"] = sum(1 for v in per_pair.values() if sum(v) / len(v) > 0)
        s["positions"] = len(rs)
        out[config] = s
    return out


def c2_means(summary, exit_mode: str):
    seeds = [s for k, s in summary.items() if k.startswith(f"C2/{exit_mode}/")]
    return {P: sum(s[P][1] for s in seeds) / len(seeds) for P in PERIODS}


def verdict(s, c2):
    fails = []
    for P in PERIODS:
        _, m, t = s[P]
        if not (m > 0 and t >= 2):
            fails.append(f"{P} mean {m:+.3f} t {t:+.1f}")
        if m <= c2[P]:
            fails.append(f"{P} not above C2 ({c2[P]:+.3f})")
    if s["pairs_positive"] < 9:
        fails.append(f"{s['pairs_positive']}/15 pairs positive")
    rate = s["positions"] / (len(PAIRS) * sum(MONTHS.values()))
    if rate < 2:
        fails.append(f"{rate:.2f} positions/pair/month")
    return fails


def _write_csv(path: Path, rows):
    if not rows:
        return
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def report(summary, refusals) -> str:
    lines = ["# FlowRSI entry screening", "",
             "| Config | P1 mR (t) | P2 mR (t) | P3 mR (t) | +pairs | pos/pair/mo | Verdict |",
             "|---|---|---|---|---|---|---|"]
    winners = []
    for exit_mode in EXITS:
        c2 = c2_means(summary, exit_mode)
        lines.append(f"| C2/{exit_mode} (seed mean) | " + " | ".join(f"{c2[P]:+.3f}" for P in PERIODS) + " | | | control |")
        for name in CONTROLS + CANDIDATES:
            config = f"{name}/{exit_mode}"
            s = summary.get(config)
            if s is None:
                continue
            fails = verdict(s, c2)
            rate = s["positions"] / (len(PAIRS) * sum(MONTHS.values()))
            cells = " | ".join(f"{s[P][1]:+.3f} ({s[P][2]:+.1f})" for P in PERIODS)
            status = "control" if name in CONTROLS else ("PASS" if not fails else "fail: " + "; ".join(fails))
            lines.append(f"| {config} | {cells} | {s['pairs_positive']}/15 | {rate:.2f} | {status} |")
            if name not in CONTROLS and not fails:
                winners.append((min(s[P][2] for P in PERIODS), config))
    lines += ["", "## Winner", ""]
    lines.append(f"{max(winners)[1]} (lowest t across P1–P3 = {max(winners)[0]:.2f})" if winners
                 else "None: no candidate passed the pre-registered bar.")
    lines += ["", "## Refused entries (sizing guardrails), XAUUSD", ""]
    for r in refusals:
        if r["symbol"] == "XAUUSD" and "/s" not in r["config"]:
            lines.append(f"- {r['config']} {r['year']}: {r['refused']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="*", default=PAIRS)
    ap.add_argument("--jobs", type=int, default=2, help="parallel pairs; ~3 GB RAM each for BTCUSD")
    args = ap.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    with ProcessPoolExecutor(args.jobs) as ex:
        results = list(ex.map(run_pair, args.pairs))
    trades = [row for tr, _ in results for row in tr]
    refusals = [row for _, rf in results for row in rf]
    _write_csv(RESULTS / "screen_trades.csv", trades)
    _write_csv(RESULTS / "screen_refusals.csv", refusals)
    text = report(summarize(trades), refusals)
    (RESULTS / "screen_report.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
