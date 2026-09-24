"""Daily loss limits for three scopes: the account, a strategy, and one container (bot).

Each limit is the dollar loss a scope may reach in a day before its new entries are
refused. The day's loss is what was closed today (UTC) plus what the open positions would
add if they stopped out, which the bots report as sl_pnl. Counting the open positions lets
a limit trip while they are still running: on 2026-09-23 the closed-only account limit
tripped with 11 positions open and the day ended 40% past it.

The scopes answer different questions. The account limit protects the capital. A strategy
limit keeps one strategy's bad day from halting the test of the others (FlowRSI's losses
blocked the TMS bots on 2026-09-23). The container limit is a fuse for a single bot that
misbehaves, e.g. one that sizes far past its risk budget.
"""

import math
from typing import Dict, Iterable, List, Optional, Tuple

STRATEGIES = ("flowrsi", "tms", "judas")

# (scope, target, max daily loss in $). "*" means every account / every container.
DEFAULT_LIMITS: Tuple[Tuple[str, str, float], ...] = (
    ("account", "*", 200.0),
    ("strategy", "flowrsi", 100.0),
    ("strategy", "tms", 60.0),
    ("strategy", "judas", 60.0),
    ("container", "*", 30.0),
)


def strategy_of(bot_id: Optional[str]) -> Optional[str]:
    """The strategy a bot runs, by the same name tests as is_flow_rsi_bot/is_judas_sweep_bot.

    Everything that is neither FlowRSI nor Judas is a session (TMS/ORB) bot.
    """
    name = (bot_id or "").lower()
    if not name:
        return None
    if "flowrsi" in name or "flow_rsi" in name or "nestedrsi" in name:
        return "flowrsi"
    if "judas" in name or "asian" in name or "sweep" in name:
        return "judas"
    return "tms"


def init_schema(conn) -> None:
    """Create the risk_limits table and add any default limit it does not have yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_limits (
            scope TEXT NOT NULL,
            target TEXT NOT NULL,
            max_daily_loss REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (scope, target)
        )
    """)
    existing = {(r[0], r[1]) for r in conn.execute("SELECT scope, target FROM risk_limits").fetchall()}
    for scope, target, amount in DEFAULT_LIMITS:
        if (scope, target) not in existing:
            conn.execute(
                "INSERT INTO risk_limits (scope, target, max_daily_loss, enabled) VALUES (?, ?, ?, 1)",
                (scope, target, amount),
            )


def load(conn) -> List[Dict]:
    """All limits, in the order of DEFAULT_LIMITS."""
    rows = conn.execute("SELECT scope, target, max_daily_loss, enabled FROM risk_limits").fetchall()
    order = {(scope, target): i for i, (scope, target, _) in enumerate(DEFAULT_LIMITS)}
    limits = [
        {"scope": r[0], "target": r[1], "max_daily_loss": float(r[2]), "enabled": bool(r[3])}
        for r in rows
    ]
    return sorted(limits, key=lambda r: order.get((r["scope"], r["target"]), len(order)))


def save(conn, updates: Iterable[Dict]) -> None:
    """Apply limit edits, all or nothing: ValueError on the first invalid one, before any write.

    Each update names an existing (scope, target) and may set max_daily_loss (a positive
    number of dollars) and/or enabled (a bool). Switching a layer off is done with enabled,
    so a zero or negative amount is refused rather than read as "no limit".
    """
    known = {(r["scope"], r["target"]) for r in load(conn)}
    changes = []
    for update in updates:
        key = (update.get("scope"), update.get("target"))
        if key not in known:
            raise ValueError(f"Unknown limit {key[0]}/{key[1]}")
        change = {}
        if update.get("max_daily_loss") is not None:
            try:
                amount = float(update["max_daily_loss"])
            except (TypeError, ValueError):
                raise ValueError(f"{key[0]}/{key[1]}: max_daily_loss must be a number") from None
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError(f"{key[0]}/{key[1]}: max_daily_loss must be greater than 0")
            change["max_daily_loss"] = amount
        if update.get("enabled") is not None:
            if not isinstance(update["enabled"], bool):
                raise ValueError(f"{key[0]}/{key[1]}: enabled must be true or false")
            change["enabled"] = 1 if update["enabled"] else 0
        if change:
            changes.append((key, change))

    for (scope, target), change in changes:
        columns = ", ".join(f"{column} = ?" for column in change)
        conn.execute(
            f"UPDATE risk_limits SET {columns}, updated_at = datetime('now') WHERE scope = ? AND target = ?",
            (*change.values(), scope, target),
        )


def _zero() -> Dict[str, float]:
    return {"closed": 0.0, "open_risk": 0.0, "total": 0.0}


def usage_by_scope(closed: Iterable[Tuple[str, Optional[float]]],
                   open_risk: Dict[str, float]) -> Dict:
    """The day's loss of one account, of each strategy on it, and of each of its bots.

    closed:    (bot_id, pnl) for every position closed today.
    open_risk: bot_id -> what its open position would add at its stop (negative = a loss,
               positive = profit already locked by a stop past break-even).
    """
    usage = {"account": _zero(), "strategy": {s: _zero() for s in STRATEGIES}, "container": {}}

    def add(bot_id: str, field: str, amount: float) -> None:
        buckets = [usage["account"], usage["container"].setdefault(bot_id, _zero())]
        strategy = strategy_of(bot_id)
        if strategy:
            buckets.append(usage["strategy"].setdefault(strategy, _zero()))
        for bucket in buckets:
            bucket[field] += amount
            bucket["total"] += amount

    for bot_id, pnl in closed:
        add(bot_id, "closed", float(pnl or 0.0))
    for bot_id, risk in open_risk.items():
        add(bot_id, "open_risk", float(risk))

    for bucket in [usage["account"], *usage["strategy"].values(), *usage["container"].values()]:
        for field in bucket:
            bucket[field] = round(bucket[field], 2)
    return usage


def breached(limits: List[Dict], usage: Dict, bot_id: Optional[str]) -> Optional[str]:
    """The reason to refuse bot_id a new entry, or None.

    Checked narrowest first (container, strategy, account) so the reason names the scope
    that actually ran out. Without a bot_id only the account limit can be applied.
    """
    by_key = {(r["scope"], r["target"]): r for r in limits}
    checks = []
    if bot_id:
        checks.append(("container", "*", usage["container"].get(bot_id), f"container {bot_id}"))
        strategy = strategy_of(bot_id)
        if strategy:
            checks.append(("strategy", strategy, usage["strategy"].get(strategy), f"strategy {strategy}"))
    checks.append(("account", "*", usage["account"], "account"))

    for scope, target, used, label in checks:
        limit = by_key.get((scope, target))
        if not limit or not limit["enabled"] or not used:
            continue
        if used["total"] <= -limit["max_daily_loss"]:
            return (
                f"Daily loss limit ({label}): {used['total']:.2f} $ / -{limit['max_daily_loss']:.2f} $ "
                f"(closed {used['closed']:.2f}, open risk {used['open_risk']:.2f})"
            )
    return None
