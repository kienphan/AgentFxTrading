"""
Unit tests for Judas sweep management guards:
- server-side ADJUST validation (wrong-side SL/TP, premature profit-lock)
- portfolio one-sweep-per-Asian-boundary dedupe count
"""

import logging
from datetime import datetime, timezone

from app.portfolio import PortfolioManager
from app.server import (
    MarketSnapshot,
    PositionInfo,
    StrategyData,
    validate_judas_adjust_decision,
    JUDAS_ADJUST_MIN_TP_PROGRESS,
)


def _snapshot_with_position(side: str = "BUY", entry: float = 29500.4,
                            tp: float = 29601.5, bid: float = 29512.5,
                            ask: float = 29513.5) -> MarketSnapshot:
    return MarketSnapshot(
        bot_id="cbot-ustec-judas",
        symbol="USTEC",
        timeframe="Minute15",
        ask=ask,
        bid=bid,
        position=PositionInfo(side=side, entry_price=entry, tp_price=tp),
        strategy=StrategyData(asian_high=29601.5, asian_low=29475.7),
    )


def test_adjust_wrong_side_sl_buy_rejected():
    """SL above the market on a BUY must be downgraded to HOLD (the 21:45 incident)."""
    snap = _snapshot_with_position(side="BUY", bid=29503.3, ask=29504.3)
    decision = {"action": "ADJUST", "new_sl_price": 29537.4, "reason": "trail to swing"}
    reason = validate_judas_adjust_decision(snap, decision)
    assert reason is not None and "bid" in reason


def test_adjust_wrong_side_sl_sell_rejected():
    snap = _snapshot_with_position(side="SELL", entry=29601.5, tp=29475.7,
                                   bid=29590.0, ask=29591.0)
    decision = {"action": "ADJUST", "new_sl_price": 29580.0, "reason": "trail"}
    reason = validate_judas_adjust_decision(snap, decision)
    assert reason is not None and "ask" in reason


def test_adjust_wrong_side_tp_rejected():
    snap = _snapshot_with_position(side="BUY", bid=29512.5, ask=29513.5)
    decision = {"action": "ADJUST", "new_tp_price": 29512.0, "reason": "tp"}
    reason = validate_judas_adjust_decision(snap, decision)
    assert reason is not None and "tp" in reason.lower()


def test_adjust_premature_profit_lock_rejected():
    """SL moved above entry (profit lock) at ~1% TP progress must be rejected."""
    snap = _snapshot_with_position(side="BUY", entry=29500.4, tp=29601.5,
                                   bid=29512.5, ask=29513.5)  # ~12 / 101 pts
    decision = {"action": "ADJUST", "new_sl_price": 29503.0, "reason": "BE"}
    reason = validate_judas_adjust_decision(snap, decision)
    assert reason is not None and "premature" in reason


def test_adjust_valid_below_market_sl_allowed():
    """Tightening SL below entry / below market stays allowed at any profit."""
    snap = _snapshot_with_position(side="BUY", entry=29500.4, tp=29601.5,
                                   bid=29503.3, ask=29504.3)
    decision = {"action": "ADJUST", "new_sl_price": 29499.0, "reason": "swing"}
    assert validate_judas_adjust_decision(snap, decision) is None


def test_adjust_profit_lock_after_40pct_allowed():
    snap = _snapshot_with_position(side="BUY", entry=29500.4, tp=29601.5,
                                   bid=29545.0, ask=29546.0)  # > 40% of 101 pts
    decision = {"action": "ADJUST", "new_sl_price": 29510.0, "reason": "BE"}
    assert validate_judas_adjust_decision(snap, decision) is None


def test_adjust_flat_position_rejected():
    snap = _snapshot_with_position()
    snap.position = None
    decision = {"action": "ADJUST", "new_sl_price": 29500.0, "reason": "x"}
    reason = validate_judas_adjust_decision(snap, decision)
    assert reason is not None and "FLAT" in reason


def test_count_positions_opened_on(tmp_path, caplog):
    """Portfolio dedupe count is side/date aware."""
    pm = PortfolioManager(db_path=str(tmp_path / "test_portfolio.db"))
    pm.register_position(
        bot_id="cbot-ustec-judas", symbol="USTEC", side="Buy", volume=0.1,
        entry_price=29500.4, sl_pips=405.0, tp_pips=1011.0, account_id="live-6094347",
    )
    today = datetime.now(timezone.utc).date()
    assert pm.count_positions_opened_on("cbot-ustec-judas", "USTEC", "BUY", "live-6094347", today) == 1
    assert pm.count_positions_opened_on("cbot-ustec-judas", "USTEC", "SELL", "live-6094347", today) == 0
    assert pm.count_positions_opened_on("cbot-ustec-judas", "USTEC", "BUY", "live-9999999", today) == 0
