"""
Unit tests for Gate 2.6 (choppy regime) override in evaluate_cycle_gate.

Regression: 2026-09-09 US30 dropped hard (OR low 52398.5 -> 52361.7) with an
aligned BEARISH TMS, but the deterministic choppy gate blocked the entry because
the regime label still counted 5 failed breakouts from earlier chop. A fresh,
momentum-confirmed breakout must now pass through to the LLM.
"""

from app.server import (
    MarketSnapshot,
    OrbData,
    MarketRegimeInfo,
    SessionInfo,
    TmsSignals,
    BarData,
    evaluate_cycle_gate,
    CHOPPY_OVERRIDE_MAX_BARS_SINCE_BREAKOUT,
)


def _us30_snapshot(bars_since_breakout: int = 1, direction: str = "down",
                   below_ema: bool = True, slope: float = -1.5,
                   ha_turned_green: bool = False, regime: str = "choppy",
                   or_flips: int = 5) -> MarketSnapshot:
    return MarketSnapshot(
        bot_id="us30_m15",
        symbol="US30",
        timeframe="Minute15",
        ask=52361.7,
        bid=52361.2,
        bars=[BarData(time="2026-09-09T15:15:00+00:00")],
        session=SessionInfo(session_name="newyork", phase="active", is_trading_time=True),
        tms=TmsSignals(bias="BEARISH", bars_since_cross=7),
        chart_tms=TmsSignals(
            bias="BEARISH",
            price_below_ema=below_ema,
            green_tf_slope=slope,
            ha_turned_green=ha_turned_green,
            exit_long=False,
            exit_short=False,
        ),
        market=MarketRegimeInfo(regime=regime, or_flips=or_flips, er_session=0.2),
        orb=OrbData(
            or_high=52468.0,
            or_low=52398.5,
            or_complete=True,
            breakout_direction=direction,
            breakout_price=52361.7,
            breakout_distance_pips=368.0,
            bars_since_breakout=bars_since_breakout,
            in_entry_window=bars_since_breakout <= 5,
            is_decisive=True,
        ),
    )


def test_fresh_momentum_confirmed_breakout_overrides_choppy_gate():
    """The 2026-09-09 US30 down-breakout case must reach the LLM."""
    assert evaluate_cycle_gate(_us30_snapshot()) is None


def test_choppy_gate_still_blocks_without_momentum_alignment():
    decision = evaluate_cycle_gate(_us30_snapshot(below_ema=False, slope=1.5))
    assert decision is not None
    assert decision.action == "HOLD"
    assert "CHOPPY" in decision.reason
    assert "no fresh momentum-confirmed expansion" in decision.reason


def test_choppy_gate_still_blocks_stale_breakout():
    """A breakout older than the override window is not 'fresh'."""
    decision = evaluate_cycle_gate(_us30_snapshot(bars_since_breakout=CHOPPY_OVERRIDE_MAX_BARS_SINCE_BREAKOUT + 2))
    assert decision is not None
    assert decision.action == "HOLD"


def test_choppy_gate_still_blocks_when_regime_is_choppy_and_counter_ha():
    decision = evaluate_cycle_gate(_us30_snapshot(ha_turned_green=True))
    assert decision is not None
    assert decision.action == "HOLD"


def test_non_choppy_regime_unaffected():
    assert evaluate_cycle_gate(_us30_snapshot(regime="trending", or_flips=0)) is None


def test_choppy_gate_blocks_extreme_flip_count():
    """2026-09-10 USDJPY 14:30 case: 9 failed breakouts = chop trap, override must not fire."""
    decision = evaluate_cycle_gate(_us30_snapshot(or_flips=9))
    assert decision is not None
    assert decision.action == "HOLD"
    assert "CHOPPY" in decision.reason


def test_choppy_override_requires_min_atr_expansion():
    """Tiny breakout (6.9p) vs ATR (16.4p) is not an expansion -> blocked."""
    from app.server import CHOPPY_OVERRIDE_MIN_BREAKOUT_ATR

    snap = _us30_snapshot()
    snap.atr_pips = 16.4
    snap.orb.breakout_distance_pips = 6.9
    decision = evaluate_cycle_gate(snap)
    assert decision is not None and decision.action == "HOLD"

    snap.orb.breakout_distance_pips = CHOPPY_OVERRIDE_MIN_BREAKOUT_ATR * 16.4 + 1.0
    assert evaluate_cycle_gate(snap) is None
