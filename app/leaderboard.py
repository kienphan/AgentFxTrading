"""
Bot Quantitative Performance Leaderboard & Ranking System for AgentFxTrading
=============================================================================
Computes multi-dimensional performance rankings (Composite Quant Score,
Win Rate %, Profit Factor, Net PnL, Tier Badges: Tier S/A/B/C) across all cBots.

Two rankings come out of the same trades:
- the USD ranking scores Net PnL in account currency, so a bot trading bigger lots scores
  higher for the same edge;
- the lot-neutral ranking scores every closed trade over the risk it took (lots x stop
  distance), so position size (often arbitrary on demo accounts) drops out.
Both can be narrowed to a rolling look-back window (LEADERBOARD_PERIODS), and both pull a
small sample's score toward the neutral 50 (FULL_SAMPLE_TRADES).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from app.db import get_db_connection as _get_unified_db
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"

# Closed trades a score needs before it counts in full. Below that the composite is pulled toward
# the neutral 50 in proportion (3 trades keep 30% of their distance from it): a short run of
# winners has no loss and no drawdown yet, so its PF and Return/DD read as unbounded and score the
# top of both curves; unweighted, three lucky trades would outrank a long, proven record.
FULL_SAMPLE_TRADES = 10

# Rolling look-back windows for the period filter, in days (None = all time). Rolling rather
# than calendar periods: on a Monday morning "this week" would hold almost no trades, and a bot
# whose code just changed should be judged on its last N days whatever the weekday.
LEADERBOARD_PERIODS: Dict[str, Optional[int]] = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "6m": 182,
    "1y": 365,
    "all": None,
}


def get_db_connection(db_path: Optional[Union[Path, str]] = None):
    return _get_unified_db(db_path)


def period_start(period: str, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """UTC start of a look-back window, in the positions table's 'YYYY-MM-DD HH:MM:SS' format.

    None for "all" and for an unknown period, which both cover all time.
    """
    days = LEADERBOARD_PERIODS.get(period)
    if days is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def compute_profit_factor(gross_profit: float, gross_loss: float, total_trades: int) -> Optional[float]:
    """Gross profit over gross loss. None means profit with no loss yet, an unbounded PF.

    It used to fall back to the gross profit itself, which tied the value to the money amount:
    three winners worth $0.80 in total read as PF 0.80 and scored as a losing system.
    """
    if gross_loss > 0:
        return round(gross_profit / gross_loss, 2)
    if gross_profit > 0:
        return None
    return 1.0 if total_trades == 0 else 0.0


def _profit_factor_score(profit_factor: Optional[float]) -> float:
    """Non-linear 10-100 curve over PF benchmarks; an unbounded PF (None) scores the top."""
    if profit_factor is None or profit_factor >= 3.0:
        return 100.0
    if profit_factor >= 2.0:
        return 85.0 + (profit_factor - 2.0) * 15.0
    if profit_factor >= 1.2:
        return 70.0 + (profit_factor - 1.2) * 18.75
    if profit_factor >= 1.0:
        return 50.0 + (profit_factor - 1.0) * 100.0
    return max(10.0, profit_factor * 50.0)


def _activity_score(total_trades: int) -> float:
    """Consistency & activity: rewards a statistically meaningful sample, up to 20 trades."""
    return min(100.0, 40.0 + min(total_trades * 3.0, 60.0))


def _sample_weighted(raw_score: float, total_trades: int) -> float:
    """Pull a composite toward the neutral 50 while the sample is under FULL_SAMPLE_TRADES."""
    confidence = min(1.0, total_trades / FULL_SAMPLE_TRADES)
    return 50.0 + (raw_score - 50.0) * confidence


def _classify_tier(composite_score: float, win_rate: float, total_trades: int) -> tuple[str, str, str]:
    """Tier badge, label and color for a composite score."""
    if total_trades == 0:
        # Nothing closed in the window: no evidence either way, so no tier (and ranked last)
        return "UNRATED", "⏳ No trades", "#64748b"
    if (composite_score >= 80.0 and total_trades >= 3) or (win_rate >= 75.0 and total_trades >= 5):
        return "TIER_S", "👑 Tier S (Elite)", "#38bdf8"  # Sky blue / Diamond
    if composite_score >= 68.0:
        return "TIER_A", "🥇 Tier A (Strong)", "#f59e0b"  # Amber / Gold
    if composite_score >= 50.0:
        return "TIER_B", "🥈 Tier B (Moderate)", "#94a3b8"  # Slate / Silver
    return "TIER_C", "⚠️ Tier C (Review)", "#f87171"  # Red / Warning


def calculate_quant_score(
    win_rate: float,
    profit_factor: Optional[float],
    net_pnl: float,
    total_trades: int
) -> tuple[float, str, str, str]:
    """
    Computes Composite Quant Score (0.0 - 100.0) and Tier classification:
    - Win Rate Score (30%): Scaled min(100.0, win_rate * 1.25)
    - Profit Factor Score (30%): Non-linear curve based on PF benchmarks
    - PnL Performance Score (20%): Normalized against baseline
    - Consistency/Activity Score (20%): Rewards statistically significant sample size
    The weighted sum is then pulled toward 50 below FULL_SAMPLE_TRADES closed trades.
    """
    # 1. Win Rate Score (30%)
    score_winrate = min(100.0, max(0.0, win_rate * 1.25))

    # 2. Profit Factor Score (30%)
    score_pf = _profit_factor_score(profit_factor)

    # 3. PnL Performance Score (20%)
    if net_pnl > 0:
        score_pnl = min(100.0, 50.0 + (net_pnl / 100.0) * 50.0)
    else:
        score_pnl = max(10.0, 50.0 - (abs(net_pnl) / 100.0) * 40.0)

    # 4. Consistency & Activity Score (20%)
    score_activity = _activity_score(total_trades)

    composite_score = round(_sample_weighted(
        0.30 * score_winrate + 0.30 * score_pf + 0.20 * score_pnl + 0.20 * score_activity,
        total_trades
    ), 1)
    tier_badge, tier_label, tier_color = _classify_tier(composite_score, win_rate, total_trades)
    return composite_score, tier_badge, tier_label, tier_color


def calculate_lot_neutral_score(
    win_rate: float,
    profit_factor: Optional[float],
    return_dd: Optional[float],
    net_units: float,
    total_trades: int,
) -> tuple[float, str, str, str]:
    """
    Composite Quant Score (0.0 - 100.0) with position size taken out:
    - Win Rate Score (30%): as in calculate_quant_score
    - Profit Factor Score (30%): same curve, fed a PF computed on size-neutral results
      (see _size_neutral_units)
    - Return/Drawdown Score (20%): replaces Net PnL in USD. Net result over the max drawdown of
      the size-neutral equity curve: a ratio of two figures in the same unit, so neither the
      position size nor the symbol's value per lot survives it. None = no drawdown yet.
    - Consistency/Activity Score (20%): as in calculate_quant_score
    The weighted sum is then pulled toward 50 below FULL_SAMPLE_TRADES closed trades.
    """
    score_winrate = min(100.0, max(0.0, win_rate * 1.25))
    score_pf = _profit_factor_score(profit_factor)

    if return_dd is None:
        # No drawdown yet: all profit, or nothing but break-evens
        score_rdd = 100.0 if net_units > 0 else 50.0
    elif return_dd > 0:
        score_rdd = min(100.0, 50.0 + return_dd * 50.0 / 3.0)  # 3x the drawdown scores the top
    else:
        score_rdd = max(10.0, 50.0 + return_dd * 40.0)

    score_activity = _activity_score(total_trades)

    composite_score = round(_sample_weighted(
        0.30 * score_winrate + 0.30 * score_pf + 0.20 * score_rdd + 0.20 * score_activity,
        total_trades
    ), 1)
    tier_badge, tier_label, tier_color = _classify_tier(composite_score, win_rate, total_trades)
    return composite_score, tier_badge, tier_label, tier_color


def _size_neutral_units(trades: List[Dict[str, Any]]) -> Tuple[str, List[float]]:
    """Each closed trade's result with its position size taken out, in the given order, plus
    the basis used.

    "risk": P&L / (lots x stop distance in pips). The bots size by risk (lots = risk money / stop
    distance), so P&L per lot alone would keep the stop distance in: a wide-stop trade, opened
    on fewer lots, would weigh several times a tight-stop one risking the same money. Over the
    risk it is the trade's R-multiple times the symbol's pip value per lot, a constant for one
    bot's symbol that cancels in every ratio scored here.
    "lot": P&L per lot, the fallback when any trade lacks a stop distance (rows from before the
    bots reported one), so one bot's trades never mix the two units.

    `pnl` on a closed row carries every partial-close slice and `volume` is restored to the
    opening size, so both bases cover the whole trade. A row without a volume cannot be
    normalised and is left out.
    """
    sized = [t for t in trades if float(t.get("volume") or 0.0) > 0]
    basis = "risk" if sized and all(float(t.get("sl_pips") or 0.0) > 0 for t in sized) else "lot"
    units = [
        float(t.get("pnl") or 0.0)
        / (float(t["volume"]) * (float(t["sl_pips"]) if basis == "risk" else 1.0))
        for t in sized
    ]
    return basis, units


def _oldest_first(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Closed trades in the order they closed.

    Exit times only have second resolution (a close-all stamps several trades with one) and the
    DB returns ties in no fixed order, so the row id breaks them: otherwise the equity curve, its
    drawdown and the score could change from one refresh to the next with no new trade.
    """
    return sorted(trades, key=lambda t: (str(t.get("exit_time") or t.get("entry_time") or ""), t.get("id") or 0))


def _size_neutral_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """PF, payoff and Return/DD on size-neutral results for one bot's closed trades, oldest first.

    Win/loss counts are left to the caller: dividing by a positive size keeps every trade's sign,
    so they equal the USD figures.
    """
    basis, units = _size_neutral_units(trades)
    win_units = [u for u in units if u > 0]
    loss_units = [u for u in units if u < 0]
    gross_profit = sum(win_units)
    gross_loss = abs(sum(loss_units))

    # Max drawdown of the equity curve, measured from the running peak (starting at 0)
    equity = peak = max_dd = 0.0
    for u in units:
        equity += u
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    if win_units and loss_units:
        payoff: Optional[float] = round((gross_profit / len(win_units)) / (gross_loss / len(loss_units)), 2)
    elif win_units:
        payoff = None  # no loss to compare against
    else:
        payoff = 0.0

    return {
        "basis": basis,
        "profit_factor": compute_profit_factor(gross_profit, gross_loss, len(units)),
        "payoff_ratio": payoff,
        "net_units": round(equity, 2),
        "max_dd_units": round(max_dd, 2),
        "return_dd": round(equity / max_dd, 2) if max_dd > 0 else None,
    }


def _rank(rows: List[Dict[str, Any]], tiebreak: str) -> Optional[Dict[str, Any]]:
    """Sort in place (bots with closed trades first, then score, then `tiebreak`), number the
    ranks, and return the top bot that has closed trades, if any."""
    rows.sort(key=lambda x: (x["total_trades"] > 0, x["composite_score"], x[tiebreak]), reverse=True)
    for idx, r in enumerate(rows, start=1):
        r["rank"] = idx
    return rows[0] if rows and rows[0]["total_trades"] > 0 else None


def compute_bot_leaderboard(
    account_id: str = "all",
    db_path: Optional[Path] = None,
    account_type: Optional[str] = None,
    period: str = "all",
) -> Dict[str, Any]:
    """
    Analyzes historical trade outcomes and active positions across all cBots
    to compute ranking scores, win rates, profit factors, and tier badges.

    ``account_type`` scopes the ranking to the trading mode ("live" or "demo" = the
    configured accounts of that mode; None = every configured account). ``account_id``
    narrows further to a single account and "all" ranks everything; passing "live" or
    "demo" as the account_id stays supported as shorthand for the type filter.

    ``period`` (a LEADERBOARD_PERIODS key) keeps what happened inside that rolling window: the
    trades closed in it and the positions opened in it that are still open. An older open
    position's floating P&L mostly built up before the window, so it stays out of the window's
    Net PnL and score. The result carries the USD ranking under "rankings" and the lot-neutral
    one under "lot_neutral".
    """
    if account_type not in ("live", "demo"):
        account_type = account_id if account_id in ("live", "demo") else None
    if period not in LEADERBOARD_PERIODS:
        period = "all"
    since = period_start(period)
    conn = get_db_connection(db_path)
    try:
        # Build account filter clause(s): mode scope first, then optional account narrowing
        filters: List[str] = []
        params: List[Any] = []
        if account_type:
            filters.append("account_id IN (SELECT account_id FROM accounts WHERE account_type = ? AND is_configured = 1)")
            params.append(account_type)
        if account_id and account_id not in ("all", "live", "demo"):
            filters.append("account_id = ?")
            params.append(account_id)
        account_filter = "".join(f" AND {clause}" for clause in filters)

        # 1. Fetch closed trades (closed inside the look-back window, if any). Spelled out rather
        # than COALESCE(exit_time, entry_time) so each branch can use its column's index.
        closed_filter, closed_params = "", list(params)
        open_filter, open_params = "", list(params)
        if since:
            closed_filter = " AND (exit_time >= ? OR (exit_time IS NULL AND entry_time >= ?))"
            closed_params += [since, since]
            open_filter = " AND entry_time >= ?"
            open_params.append(since)
        query_closed = f"""
            SELECT id, bot_id, symbol, side, volume, entry_price, exit_price,
                   pnl, sl_pips, entry_time, exit_time, account_id
            FROM positions
            WHERE status = 'closed'{account_filter}{closed_filter}
            ORDER BY exit_time DESC
        """
        cursor = conn.execute(query_closed, tuple(closed_params))
        closed_trades = [dict(r) for r in cursor.fetchall()]

        # 2. Fetch active positions (opened inside the look-back window, if any)
        query_open = f"""
            SELECT id, bot_id, symbol, side, volume, entry_price,
                   pnl, entry_time, account_id
            FROM positions
            WHERE status = 'open'{account_filter}{open_filter}
            ORDER BY entry_time DESC
        """
        cursor = conn.execute(query_open, tuple(open_params))
        open_positions = [dict(r) for r in cursor.fetchall()]

        # 3. Fetch any registered bot names from cbot_configs or distinct bot_ids
        known_bots = set()
        try:
            cfg_cur = conn.execute("SELECT name FROM cbot_configs")
            for r in cfg_cur.fetchall():
                known_bots.add(r[0])
        except Exception:
            pass

        for t in closed_trades:
            known_bots.add(t["bot_id"])
        for p in open_positions:
            known_bots.add(p["bot_id"])

        bot_rankings: List[Dict[str, Any]] = []
        lot_neutral_rankings: List[Dict[str, Any]] = []

        for b_id in sorted(known_bots):
            # Trades for this bot
            b_trades = [t for t in closed_trades if str(t["bot_id"]) == str(b_id)]
            b_open = [p for p in open_positions if str(p["bot_id"]) == str(b_id)]

            total_trades = len(b_trades)
            wins = [t for t in b_trades if (t.get("pnl") or 0.0) > 0.0]
            losses = [t for t in b_trades if (t.get("pnl") or 0.0) < 0.0]
            breakevens = [t for t in b_trades if (t.get("pnl") or 0.0) == 0.0]

            total_wins = len(wins)
            total_losses = len(losses)
            win_rate = round((total_wins / total_trades * 100.0), 1) if total_trades > 0 else 0.0

            closed_pnl_usd = round(sum(float(t.get("pnl") or 0.0) for t in b_trades), 2)
            floating_pnl_usd = round(sum(float(p.get("pnl") or 0.0) for p in b_open), 2)
            total_pnl_usd = round(closed_pnl_usd + floating_pnl_usd, 2)

            # Profit Factor
            gross_profit = sum(float(t.get("pnl") or 0.0) for t in wins)
            gross_loss = abs(sum(float(t.get("pnl") or 0.0) for t in losses))
            profit_factor = compute_profit_factor(gross_profit, gross_loss, total_trades)

            # List symbols traded
            symbols = list(set([t["symbol"] for t in b_trades if t.get("symbol")] + [p["symbol"] for p in b_open if p.get("symbol")]))
            symbol_display = ", ".join(sorted(symbols)) if symbols else "N/A"

            # Composite Quant Score & Tier
            score, tier_badge, tier_label, tier_color = calculate_quant_score(
                win_rate=win_rate,
                profit_factor=profit_factor,
                net_pnl=total_pnl_usd,
                total_trades=total_trades
            )

            # Friendlier bot label
            bot_name_clean = str(b_id).replace("_", " ").title()

            bot_rankings.append({
                "bot_id": b_id,
                "bot_name": bot_name_clean,
                "symbols": symbols,
                "symbol_display": symbol_display,
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "total_breakevens": len(breakevens),
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "closed_pnl_usd": closed_pnl_usd,
                "floating_pnl_usd": floating_pnl_usd,
                "total_pnl_usd": total_pnl_usd,
                "open_positions_count": len(b_open),
                "composite_score": score,
                "tier_badge": tier_badge,
                "tier_label": tier_label,
                "tier_color": tier_color
            })

            # Lot-neutral view of the same closed trades (open positions carry no final result).
            # The drawdown walks the equity curve, so it needs them oldest first.
            stats = _size_neutral_stats(_oldest_first(b_trades))
            ln_score, ln_badge, ln_label, ln_color = calculate_lot_neutral_score(
                win_rate=win_rate,
                profit_factor=stats["profit_factor"],
                return_dd=stats["return_dd"],
                net_units=stats["net_units"],
                total_trades=total_trades,
            )
            lot_neutral_rankings.append({
                "bot_id": b_id,
                "bot_name": bot_name_clean,
                "symbols": symbols,
                "symbol_display": symbol_display,
                "total_trades": total_trades,
                "total_wins": total_wins,
                "total_losses": total_losses,
                "total_breakevens": len(breakevens),
                "win_rate": win_rate,
                **stats,
                "composite_score": ln_score,
                "tier_badge": ln_badge,
                "tier_label": ln_label,
                "tier_color": ln_color,
            })

        # Sort Rankings: bots with closed trades first, then composite_score DESC, then the
        # tiebreak DESC (USD: total_pnl_usd, lot-neutral: win_rate)
        top_performer = _rank(bot_rankings, "total_pnl_usd")
        lot_neutral_top = _rank(lot_neutral_rankings, "win_rate")

        # Fleet Aggregates
        fleet_total_trades = sum(r["total_trades"] for r in bot_rankings)
        fleet_total_wins = sum(r["total_wins"] for r in bot_rankings)
        fleet_win_rate = round((fleet_total_wins / fleet_total_trades * 100.0), 1) if fleet_total_trades > 0 else 0.0
        fleet_total_pnl_usd = round(sum(r["total_pnl_usd"] for r in bot_rankings), 2)

        return {
            "calculated_at": datetime.datetime.now().isoformat(),
            "account_id": account_id,
            "account_type": account_type,
            "period": period,
            "period_start": since,
            "full_sample_trades": FULL_SAMPLE_TRADES,
            "total_bots": len(bot_rankings),
            "fleet_total_trades": fleet_total_trades,
            "fleet_win_rate": fleet_win_rate,
            "fleet_total_pnl_usd": fleet_total_pnl_usd,
            "top_performer": top_performer,
            "rankings": bot_rankings,
            "lot_neutral": {
                "top_performer": lot_neutral_top,
                "rankings": lot_neutral_rankings,
            },
        }
    finally:
        conn.close()
