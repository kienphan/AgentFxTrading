"""
cTrader backtest report (`--report-json`) -> the Backtest page's summary and chart data.

The report's per-hour/weekday sections only count winning and losing trades, so P&L by hour and by
weekday is computed here from history.items, bucketed by UTC entry time.
"""
from datetime import datetime, timezone
from typing import Dict, List

MAX_EQUITY_POINTS = 1000


def _r(value, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _side(stats: Dict, key: str, side: str = "all"):
    value = (stats.get(key) or {}).get(side)
    return value if value is not None else 0


def summarize(report: Dict) -> Dict:
    main, stats, equity = report["main"], report["tradeStatistics"], report["equity"]
    nets = [float(t["net"]) for t in (report.get("history") or {}).get("items", [])]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    total = int(_side(stats, "totalTrades"))
    won = int(_side(stats, "winningTrades"))
    return {
        "net_profit": _r(_side(stats, "netProfit")),
        "roi_pct": _r(main.get("roi")),
        "starting_capital": _r(main.get("startingCapital")),
        "ending_equity": _r(main.get("endingEquity")),
        "profit_factor": _r(_side(stats, "profitFactor")),
        "total_trades": total,
        "winning_trades": won,
        "losing_trades": int(_side(stats, "losingTrades")),
        "win_rate": _r(won * 100.0 / total) if total else 0.0,
        "avg_trade": _r(_side(stats, "averageTrade")),
        "avg_win": _r(sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": _r(sum(losses) / len(losses)) if losses else 0.0,
        "max_equity_dd_pct": _r(equity.get("maxEquityDrawdownPercent")),
        "max_equity_dd": _r(equity.get("maxEquityDrawdownAbsolute")),
        "max_balance_dd_pct": _r(equity.get("maxBalanceDrawdownPercent")),
        "commissions": _r(_side(stats, "commissions")),
        "swaps": _r(_side(stats, "swaps")),
        "largest_win": _r(_side(stats, "largestWinningTrade")),
        "largest_loss": _r(_side(stats, "largestLosingTrade")),
        "max_consecutive_losses": int(_side(stats, "maxConsecutiveLosingTrades")),
        "long_net": _r(_side(stats, "netProfit", "long")),
        "short_net": _r(_side(stats, "netProfit", "short")),
        "long_trades": int(_side(stats, "totalTrades", "long")),
        "short_trades": int(_side(stats, "totalTrades", "short")),
    }


def downsample(points: List, limit: int = MAX_EQUITY_POINTS) -> List:
    """At most `limit` evenly spaced points, always keeping the first and the last."""
    if len(points) <= limit:
        return list(points)
    step = (len(points) - 1) / (limit - 1)
    return [points[i] for i in sorted({round(i * step) for i in range(limit)})]


def _utc(ms) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _minute(ms) -> str:
    return _utc(ms).strftime("%Y-%m-%d %H:%M")


def ui_payload(report: Dict) -> Dict:
    items = (report.get("history") or {}).get("items", [])
    by_hour = [{"net": 0.0, "count": 0} for _ in range(24)]
    by_weekday = [{"net": 0.0, "count": 0} for _ in range(7)]
    trades = []
    for t in sorted(items, key=lambda item: item["entryTime"]):
        entry = _utc(t["entryTime"])
        for bucket in (by_hour[entry.hour], by_weekday[entry.weekday()]):
            bucket["net"] += float(t["net"])
            bucket["count"] += 1
        trades.append({
            "id": t.get("id"), "direction": t.get("direction"),
            "entry_time": _minute(t["entryTime"]), "close_time": _minute(t["closeTime"]),
            "entry_price": t.get("entryPrice"), "close_price": t.get("closePrice"),
            "lots": t.get("quantity"), "pips": _r(t.get("pips"), 1), "net": _r(t["net"]),
            "commissions": _r(t.get("commissions")), "swaps": _r(t.get("swaps")),
        })
    for bucket in by_hour + by_weekday:
        bucket["net"] = _r(bucket["net"])
    equity = [{"t": p["timestamp"], "balance": _r(p["balance"]), "equity": _r(p["minEquity"])}
              for p in downsample((report.get("equity") or {}).get("points", []))]
    parameters = {p["propertyName"]: p.get("value") for p in report.get("parameters", [])}
    return {"equity": equity, "trades": trades, "pnl_by_hour": by_hour, "pnl_by_weekday": by_weekday,
            "parameters": parameters}
