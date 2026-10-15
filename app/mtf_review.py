"""Forward-test review for the MTF bot, computed from trades/mtf_journal.jsonl.

    python -m app.mtf_review [path]

A trade is a decision -> fill -> close chain sharing one client_order_id. R is the net
money result over the money the fill was sized to risk, so costs are inside it.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.mtf_agent import _journal_path


def load(path) -> List[Dict[str, Any]]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _r(fill: Dict[str, Any], close: Dict[str, Any]) -> Optional[float]:
    risk_money = (fill.get("balance") or 0) * (fill.get("risk_pct") or 0) / 100.0
    if risk_money > 0 and close.get("net") is not None:
        return close["net"] / risk_money
    entry, stop, exit_ = fill.get("fill_entry"), fill.get("stop"), close.get("fill_exit")
    if entry and stop and exit_ and entry != stop:
        side = 1 if str(fill.get("side", "")).upper() == "BUY" else -1
        return side * (exit_ - entry) / abs(entry - stop)
    return None


def _verdict(n: int) -> str:
    if n < 30:
        return f"noise: {n} trades, under 30, no conclusion"
    if n < 100:
        return f"weak evidence: {n} trades, under 100"
    return f"usable sample: {n} trades"


def review(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    decisions, fills, closes = {}, {}, []
    all_decisions = []
    for rec in records:
        event, cid = rec.get("event"), rec.get("client_order_id") or ""
        if event == "decision":
            all_decisions.append(rec)
            if cid:
                decisions[cid] = rec
        elif event == "fill" and cid:
            fills[cid] = rec
        elif event == "close":
            closes.append(rec)

    rs, reasons, slippage, stop_cost = [], Counter(), [], []
    closed_ids, orphans = set(), 0
    for close in closes:
        cid = close.get("client_order_id") or ""
        fill = fills.get(cid)
        if fill is None:
            orphans += 1
            continue
        r = _r(fill, close)
        if r is None:
            orphans += 1
            continue
        closed_ids.add(cid)
        rs.append(r)
        reasons[close.get("exit_reason") or "unknown"] += 1
        if close.get("exit_reason") == "stop_loss":
            stop_cost.append(abs(r))
        planned = (decisions.get(cid) or {}).get("intended", {}).get("entry")
        if planned and fill.get("fill_entry"):
            side = 1 if str(fill.get("side", "")).upper() == "BUY" else -1
            slippage.append(side * (fill["fill_entry"] - planned))

    equity, peak, max_dd, streak, max_streak = 0.0, 0.0, 0.0, 0, 0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        streak = streak + 1 if r < 0 else 0
        max_streak = max(max_streak, streak)

    n = len(rs)
    waits = Counter(d.get("gate_reason") or "unknown" for d in all_decisions if not d.get("approved"))
    return {
        "trades": n,
        "verdict": _verdict(n),
        "win_rate": round(100.0 * sum(r > 0 for r in rs) / n, 1) if n else None,
        "expectancy_r": round(sum(rs) / n, 3) if n else None,
        "total_r": round(sum(rs), 2),
        "max_drawdown_r": round(max_dd, 2),
        "max_loss_streak": max_streak,
        "exit_reasons": dict(reasons),
        "manual_exit_rate": round(100.0 * reasons["manual"] / n, 1) if n else None,
        "avg_entry_slippage": round(sum(slippage) / len(slippage), 5) if slippage else None,
        "avg_actual_vs_intended_risk": round(sum(stop_cost) / len(stop_cost), 2) if stop_cost else None,
        "open_trades": len(set(fills) - closed_ids),
        "orphan_closes": orphans,
        "decisions": {
            "total": len(all_decisions),
            "approved": sum(1 for d in all_decisions if d.get("approved")),
            "wait": sum(waits.values()),
        },
        "top_wait_reasons": [[reason, count] for reason, count in waits.most_common(5)],
    }


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else _journal_path()
    if not path.exists():
        sys.exit(f"No journal at {path}")
    print(json.dumps(review(load(path)), indent=2, ensure_ascii=False))
