"""
Bot Quantitative Performance Leaderboard & Ranking System for AgentFxTrading
=============================================================================
Computes multi-dimensional performance rankings (Composite Quant Score,
Win Rate %, Profit Factor, Net PnL, Tier Badges: Tier S/A/B/C) across all cBots.
"""

from __future__ import annotations

import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "portfolio.db"


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DB_PATH
    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def calculate_quant_score(
    win_rate: float,
    profit_factor: float,
    net_pnl: float,
    total_trades: int
) -> tuple[float, str, str, str]:
    """
    Computes Composite Quant Score (0.0 - 100.0) and Tier classification:
    - Win Rate Score (30%): Scaled min(100.0, win_rate * 1.25)
    - Profit Factor Score (30%): Non-linear curve based on PF benchmarks
    - PnL Performance Score (20%): Normalized against baseline
    - Consistency/Activity Score (20%): Rewards statistically significant sample size
    """
    # 1. Win Rate Score (30%)
    score_winrate = min(100.0, max(0.0, win_rate * 1.25))

    # 2. Profit Factor Score (30%)
    if profit_factor >= 3.0:
        score_pf = 100.0
    elif profit_factor >= 2.0:
        score_pf = 85.0 + (profit_factor - 2.0) * 15.0
    elif profit_factor >= 1.2:
        score_pf = 70.0 + (profit_factor - 1.2) * 18.75
    elif profit_factor >= 1.0:
        score_pf = 50.0 + (profit_factor - 1.0) * 100.0
    else:
        score_pf = max(10.0, profit_factor * 50.0)

    # 3. PnL Performance Score (20%)
    if net_pnl > 0:
        score_pnl = min(100.0, 50.0 + (net_pnl / 100.0) * 50.0)
    else:
        score_pnl = max(10.0, 50.0 - (abs(net_pnl) / 100.0) * 40.0)

    # 4. Consistency & Activity Score (20%)
    # Rewards trade count up to 20 trades
    score_activity = min(100.0, 40.0 + min(total_trades * 3.0, 60.0))

    composite_score = round(
        0.30 * score_winrate + 0.30 * score_pf + 0.20 * score_pnl + 0.20 * score_activity,
        1
    )

    # Tier Badge Classification
    if (composite_score >= 80.0 and total_trades >= 3) or (win_rate >= 75.0 and total_trades >= 5):
        tier_badge = "TIER_S"
        tier_label = "👑 Tier S (Elite)"
        tier_color = "#38bdf8"  # Sky blue / Diamond
    elif composite_score >= 68.0:
        tier_badge = "TIER_A"
        tier_label = "🥇 Tier A (Strong)"
        tier_color = "#f59e0b"  # Amber / Gold
    elif composite_score >= 50.0:
        tier_badge = "TIER_B"
        tier_label = "🥈 Tier B (Moderate)"
        tier_color = "#94a3b8"  # Slate / Silver
    else:
        tier_badge = "TIER_C"
        tier_label = "⚠️ Tier C (Review)"
        tier_color = "#f87171"  # Red / Warning

    return composite_score, tier_badge, tier_label, tier_color


def compute_bot_leaderboard(
    account_id: str = "all",
    db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Analyzes historical trade outcomes and active positions across all cBots
    to compute ranking scores, win rates, profit factors, and tier badges.
    """
    conn = get_db_connection(db_path)
    try:
        # Build account filter clause
        account_filter = ""
        params: List[Any] = []
        if account_id and account_id != "all":
            if account_id in ("demo", "live"):
                account_filter = " AND account_id IN (SELECT account_id FROM accounts WHERE account_type = ? AND is_configured = 1)"
                params.append(account_id)
            else:
                account_filter = " AND account_id = ?"
                params.append(account_id)

        # 1. Fetch closed trades
        query_closed = f"""
            SELECT id, bot_id, symbol, side, volume, entry_price, exit_price,
                   pnl, entry_time, exit_time, account_id
            FROM positions
            WHERE status = 'closed'{account_filter}
            ORDER BY exit_time DESC
        """
        cursor = conn.execute(query_closed, tuple(params))
        closed_trades = [dict(r) for r in cursor.fetchall()]

        # 2. Fetch active positions
        query_open = f"""
            SELECT id, bot_id, symbol, side, volume, entry_price,
                   pnl, entry_time, account_id
            FROM positions
            WHERE status = 'open'{account_filter}
            ORDER BY entry_time DESC
        """
        cursor = conn.execute(query_open, tuple(params))
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
            if gross_loss > 0:
                profit_factor = round(gross_profit / gross_loss, 2)
            elif gross_profit > 0:
                profit_factor = round(gross_profit, 2)
            else:
                profit_factor = 1.0 if total_trades == 0 else 0.0

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

        # Sort Rankings: Primary by composite_score DESC, Secondary by total_pnl_usd DESC
        bot_rankings.sort(key=lambda x: (x["composite_score"], x["total_pnl_usd"]), reverse=True)

        for idx, r in enumerate(bot_rankings, start=1):
            r["rank"] = idx

        # Fleet Aggregates
        fleet_total_trades = sum(r["total_trades"] for r in bot_rankings)
        fleet_total_wins = sum(r["total_wins"] for r in bot_rankings)
        fleet_win_rate = round((fleet_total_wins / fleet_total_trades * 100.0), 1) if fleet_total_trades > 0 else 0.0
        fleet_total_pnl_usd = round(sum(r["total_pnl_usd"] for r in bot_rankings), 2)
        top_performer = bot_rankings[0] if bot_rankings else None

        return {
            "calculated_at": datetime.datetime.now().isoformat(),
            "account_id": account_id,
            "total_bots": len(bot_rankings),
            "fleet_total_trades": fleet_total_trades,
            "fleet_win_rate": fleet_win_rate,
            "fleet_total_pnl_usd": fleet_total_pnl_usd,
            "top_performer": top_performer,
            "rankings": bot_rankings
        }
    finally:
        conn.close()
