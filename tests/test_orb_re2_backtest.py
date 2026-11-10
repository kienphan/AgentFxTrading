"""
ORB-RE2 research harness: the rule invariants the study depends on.

The study numbers are only worth quoting if the engine enforces the Pine rules exactly, so these
tests pin them on synthetic bars where every outcome is known by construction: the OR window, the
tick break (and its refusal inside the window), the daily trend gate, the ATR stop and R target,
the session flatten, one-trade-per-session and the spread side.

They also pin the two helper functions the whole thing rests on — Wilder's ATR and the Pine-seeded
EMA — because an off-by-one in either would move every number in the study.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.orb_re2.backtest_orb_re2 import (  # noqa: E402
    OrbRe2Config, backtest, config_for, daily_bars, default_point_size, load_csv,
    moving_average_series, summarize, wilder_atr,
)

# --- synthetic bar helpers -----------------------------------------------------------------

def frame_from(rows) -> pd.DataFrame:
    """``rows`` = (utc timestamp string, open, high, low, close) -> M1 frame."""
    index = pd.to_datetime([r[0] for r in rows], utc=True)
    frame = pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close"], index=index)
    frame["volume"] = 0.0
    return frame


def warmup_rows(days: int = 20, price: float = 100.0, range_: float = 1.0, first: date = date(2024, 1, 1)):
    """Flat sessions whose true range is exactly ``range_`` every day, so ATR14 == ``range_``.

    No gaps (each close equals the next open), so TR = high-low on every bar after the first.
    The bar sits at 12:00 so it never lands in an opening-range window: warm-up sessions are
    skipped, and only the trade day under test can arm.
    """
    rows = []
    for i in range(days):
        d = first + timedelta(days=i)
        rows.append((f"{d} 12:00", price, price + range_ / 2, price - range_ / 2, price))
    return rows


TRADE_DAY = date(2024, 1, 21)

BASE_UTC = dict(session_tz="UTC", session_open="09:00", or_minutes=5,
                entry_cutoff="15:50", eod_flatten="16:00", point_size=0.01,
                tick_buffer_points=0.1, sl_atr_mult=0.965, tp_r_multiple=2.0)


def run(rows, **over):
    cfg = OrbRe2Config(**{**BASE_UTC, **over})
    return backtest(frame_from(rows), cfg)


def opening_range_rows(day: date, extra_rows):
    """OR = [99, 101.5]: three bars inside the window, then the caller's bars after it."""
    rows = [
        (f"{day} 09:00", 100.0, 101.0, 99.0, 100.0),
        (f"{day} 09:01", 100.0, 101.5, 99.5, 100.0),
        (f"{day} 09:04", 100.0, 100.5, 99.5, 100.0),
    ]
    return rows + extra_rows


# --- helpers the study rests on ------------------------------------------------------------

def test_the_ema_is_seeded_with_an_sma_like_pines_ta_ema():
    closes = [float(i) for i in range(1, 21)]
    out = moving_average_series(closes, "ema", 10)
    assert out[8] is None                                  # not enough history yet
    assert out[9] == pytest.approx(sum(closes[:10]) / 10)  # SMA(10) seed
    alpha = 2.0 / 11.0
    expected = out[9]
    for value in closes[10:]:
        expected = (value - expected) * alpha + expected
    assert out[-1] == pytest.approx(expected)


def test_the_sma_and_wma_match_their_definitions():
    closes = [float(i) for i in range(1, 11)]
    assert moving_average_series(closes, "sma", 3)[-1] == pytest.approx((8 + 9 + 10) / 3)
    assert moving_average_series(closes, "wma", 3)[-1] == pytest.approx((8 * 1 + 9 * 2 + 10 * 3) / 6)


def test_wilder_atr_uses_the_first_period_trs_as_its_seed():
    daily = pd.DataFrame({"high": [101.0] * 20, "low": [99.0] * 20, "close": [100.0] * 20},
                         index=[date(2024, 1, 1) + timedelta(days=i) for i in range(20)])
    atr = wilder_atr(daily, 14)
    assert pd.isna(atr.iloc[13])                # the seed needs `period` TRs (indices 1..14)
    assert atr.iloc[14] == pytest.approx(2.0)   # every TR is 2.0
    assert atr.iloc[19] == pytest.approx(2.0)


def test_daily_bars_aggregate_the_sessions_own_high_low_close():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 12:00", 100.0, 103.0, 98.0, 102.0)])
    daily = daily_bars(frame_from(rows), "UTC")
    day = daily.loc[TRADE_DAY]
    assert (day["high"], day["low"], day["close"]) == (103.0, 98.0, 102.0)


def test_point_sizes_follow_the_quote_currency():
    assert default_point_size("USDJPY") == 0.01
    assert default_point_size("XAUUSD") == 0.01
    assert default_point_size("EURUSD") == 0.0001


def test_config_for_moves_the_entry_cutoff_with_the_flatten_time():
    cfg = config_for("US30")
    assert (cfg.session_tz, cfg.session_open, cfg.eod_flatten) == ("America/New_York", "09:30", "16:00")
    assert cfg.entry_cutoff == "15:50"          # ten minutes before the flatten, always
    assert config_for("US30", eod_flatten="12:00").entry_cutoff == "11:50"


# --- the opening range ---------------------------------------------------------------------

def test_nothing_enters_inside_the_opening_range_window():
    # A bar inside the window cannot trade: the range is not final until 09:05. This bar makes a
    # new OR high and would look like a breakout if the engine scanned the window.
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:02", 100.0, 105.0, 100.0, 104.0),
        (f"{TRADE_DAY} 10:00", 104.0, 106.0, 104.0, 105.0),
    ])
    trades, _ = run(warmup_rows() + rows)
    assert len(trades) == 1
    assert trades[0].entry_time.strftime("%H:%M") == "10:00"        # never inside 09:00-09:05
    assert trades[0].entry_price == pytest.approx(105.0 + 0.001)    # the window's own high, not 101.5


def test_a_session_with_no_bars_in_the_window_is_skipped():
    rows = [(f"{TRADE_DAY} 12:00", 100.0, 104.0, 98.0, 103.0)]
    trades, diag = run(warmup_rows() + rows)
    assert trades == [] and diag["no_or"] >= 1


def test_a_range_narrower_than_the_floor_skips_the_session():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 10:00", 100.0, 104.0, 98.0, 103.0)])
    trades, diag = run(warmup_rows() + rows, min_or_width_points=1000.0)
    assert trades == [] and diag["narrow_or"] == 1


# --- the tick break ------------------------------------------------------------------------

def test_the_entry_is_a_break_of_the_or_boundary_plus_the_buffer():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)])
    trades, _ = run(warmup_rows() + rows)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.side == "LONG"
    assert trade.entry_price == pytest.approx(101.5 + 0.1 * 0.01)   # OR high + buffer
    assert trade.reason == "EOD"                                    # neither bracket was reached


def test_a_touch_that_does_not_clear_the_buffer_does_not_fill():
    # high == OR high exactly, with a 0.1-point buffer: no break.
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 101.5, 100.0, 101.0)])
    trades, _ = run(warmup_rows() + rows)
    assert trades == []


def test_the_short_side_breaks_the_or_low():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 100.5, 98.0, 98.5)])
    trades, _ = run(warmup_rows() + rows)
    assert len(trades) == 1 and trades[0].side == "SHORT"


def test_both_boundaries_in_one_bar_resolve_to_the_nearer_side():
    # A single M1 bar sweeps both levels. The side closer to the bar's open is assumed first:
    # open 99.5 is nearer the 99 low than the 101.5 high, so this is a SHORT.
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 99.5, 102.0, 98.5, 101.0)])
    trades, _ = run(warmup_rows() + rows)
    assert len(trades) == 1 and trades[0].side == "SHORT"


def test_a_gap_through_the_level_fills_at_the_open_not_the_level():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 105.0, 106.0, 104.0, 105.5)])
    trades, _ = run(warmup_rows() + rows)
    assert trades[0].entry_price == pytest.approx(105.0)     # gapped beyond 101.5: filled at the open


# --- the daily trend gate ------------------------------------------------------------------

def test_the_trend_filter_blocks_the_side_that_disagrees_with_the_daily_ma():
    # Warm up at 200 so the daily EMA10 sits far above the 101.5 level: the long is refused and
    # the short is allowed, so the session's trade is the short.
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 100.5, 98.0, 98.5)])
    trades, diag = run(warmup_rows(price=200.0) + rows)
    assert len(trades) == 1 and trades[0].side == "SHORT"
    # the long break never appeared, so nothing was even refused on that side
    assert diag["trend_blocked"] == 0


def test_turning_the_filter_off_lets_a_blocked_long_through():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)])
    blocked, _ = run(warmup_rows(price=200.0) + rows)
    allowed, _ = run(warmup_rows(price=200.0) + rows, trend_filter="off")
    assert blocked == []
    assert len(allowed) == 1 and allowed[0].side == "LONG"


def test_the_ma_cross_mode_uses_the_fast_and_slow_averages():
    flat, _ = run(warmup_rows(price=100.0) + opening_range_rows(
        TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)]), trend_filter="ma_cross")
    # a flat series has fast == slow, so the neutral buffer allows neither side
    assert flat == []


def test_a_missing_ma_allows_the_side_rather_than_blocking_it():
    # No warm-up at all: the MA is undefined, and the reference only refuses on a missing ATR.
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)])
    trades, _ = run(rows)
    assert trades == []                    # refused for the missing ATR, not the missing MA
    # with ATR history but a trend_filter that cannot be computed, the side is allowed
    cfg = OrbRe2Config(**{**BASE_UTC, "trend_filter": "ma_cross", "ma_fast": 5000})
    trades, _ = backtest(frame_from(warmup_rows() + rows), cfg)
    assert len(trades) == 1


# --- risk: the ATR stop and the R target ---------------------------------------------------

def test_the_stop_is_the_atr_multiple_and_the_target_is_two_r():
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:05", 100.0, 102.0, 100.0, 101.8),
        (f"{TRADE_DAY} 11:00", 101.8, 104.0, 101.5, 103.9),
    ])
    trades, _ = run(warmup_rows() + rows)
    trade = trades[0]
    entry = 101.5 + 0.1 * 0.01
    assert trade.r_points == pytest.approx(0.965 / 0.01)              # 0.965 x ATR(1.0), in points
    assert trade.sl_price == pytest.approx(entry - 0.965)
    assert trade.tp_price == pytest.approx(entry + 2 * 0.965)
    assert trade.reason == "TP" and trade.r_multiple == pytest.approx(2.0)


def test_the_stop_is_checked_before_the_target_when_one_bar_spans_both():
    # One bar reaches the stop and the target. The pessimistic reading (stop first) is the one
    # that never flatters the result.
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:05", 100.0, 102.0, 100.0, 101.8),
        (f"{TRADE_DAY} 11:00", 101.8, 106.0, 99.0, 105.0),
    ])
    trades, _ = run(warmup_rows() + rows)
    assert trades[0].reason == "SL" and trades[0].r_multiple == pytest.approx(-1.0)


def test_a_missing_daily_atr_refuses_the_entry():
    # Fewer than ATR-period+1 daily bars: no stop distance, so no trade at all.
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)])
    trades, diag = run(warmup_rows(days=5) + rows)
    assert trades == [] and diag["no_atr"] == 1


# --- session discipline --------------------------------------------------------------------

def test_only_one_trade_per_session_by_default():
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:05", 100.0, 102.0, 100.0, 101.8),   # long, runs to EOD
        (f"{TRADE_DAY} 12:00", 101.8, 104.0, 101.0, 103.0),   # a second break, must be ignored
    ])
    trades, _ = run(warmup_rows() + rows)
    assert len(trades) == 1


def test_the_session_is_flattened_at_the_flatten_time():
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:05", 100.0, 102.0, 100.0, 101.8),
        (f"{TRADE_DAY} 15:59", 101.8, 102.0, 101.5, 101.9),
        (f"{TRADE_DAY} 16:30", 101.9, 103.0, 101.0, 102.5),   # after the flatten: never used
    ])
    trades, _ = run(warmup_rows() + rows)
    assert trades[0].reason == "EOD"
    assert trades[0].exit_time.strftime("%H:%M") == "15:59"


def test_nothing_opens_at_or_after_the_entry_cutoff():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 15:55", 100.0, 104.0, 100.0, 103.0)])
    trades, _ = run(warmup_rows() + rows)
    assert trades == []


def test_the_direction_switch_can_restrict_the_bot_to_one_side():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 100.5, 98.0, 98.5)])
    assert run(warmup_rows() + rows, direction="long")[0] == []
    assert len(run(warmup_rows() + rows, direction="short")[0]) == 1


# --- cost ----------------------------------------------------------------------------------

def test_the_spread_is_paid_once_on_the_side_that_crosses_it():
    rows = opening_range_rows(TRADE_DAY, [(f"{TRADE_DAY} 09:05", 100.0, 103.0, 100.0, 102.0)])
    free, _ = run(warmup_rows() + rows)
    costly, _ = run(warmup_rows() + rows, spread_points=1.0)
    # a long buys the ask: the entry moves up by the full spread, the exit does not
    assert costly[0].entry_price == pytest.approx(free[0].entry_price + 0.01)
    assert costly[0].exit_price == pytest.approx(free[0].exit_price)
    assert costly[0].points == pytest.approx(free[0].points - 1.0)


# --- metrics -------------------------------------------------------------------------------

def test_summarize_reports_r_multiples_and_the_drawdown_in_points():
    rows = opening_range_rows(TRADE_DAY, [
        (f"{TRADE_DAY} 09:05", 100.0, 102.0, 100.0, 101.8),
        (f"{TRADE_DAY} 11:00", 101.8, 104.0, 101.5, 103.9),
    ])
    trades, _ = run(warmup_rows() + rows)
    summary = summarize(trades)
    assert summary["trades"] == 1
    assert summary["win_rate_pct"] == 100.0
    assert summary["net_r"] == pytest.approx(2.0, abs=0.05)
    assert summary["profit_factor"] == float("inf")
    assert summary["max_dd_points"] == 0.0


def test_summarize_of_no_trades_is_a_zero_row_not_a_crash():
    summary = summarize([])
    assert summary["trades"] == 0 and summary["profit_factor"] == 0.0


def test_an_unknown_trend_filter_is_rejected_at_construction():
    with pytest.raises(ValueError):
        OrbRe2Config(trend_filter="magic")


# --- the index path, which has no cached bars here but must still be correct -----------------

def test_index_cfds_use_a_tenth_of_a_point_as_their_pip():
    # app/cbot_presets.py: "index 0.1, XAU/BTC/ETH/JPY 0.01, forex 0.0001". Getting this wrong
    # scales every reported point and every R in an index study by 1000x.
    for symbol in ("US30", "US500", "USTEC", "DE40", "UK100"):
        assert default_point_size(symbol) == 0.1
    assert default_point_size("USDJPY") == 0.01
    assert default_point_size("EURUSD") == 0.0001


def test_each_requested_index_is_anchored_to_its_own_cash_open():
    assert (config_for("DE40").session_tz, config_for("DE40").session_open) == ("Europe/Berlin", "09:00")
    assert (config_for("UK100").session_tz, config_for("UK100").session_open) == ("Europe/London", "08:00")
    for symbol in ("US30", "US500", "USTEC"):
        cfg = config_for(symbol)
        assert (cfg.session_tz, cfg.session_open) == ("America/New_York", "09:30")
        assert cfg.entry_cutoff == "15:50" and cfg.eod_flatten == "16:00"


def test_an_index_run_sizes_its_stop_in_tenths_of_a_point():
    # The same ATR of 300 index points is a 289.5-point stop = 2895 pips at 0.1.
    cfg = config_for("US30", sl_atr_mult=0.965)
    assert cfg.point_size == 0.1
    assert cfg.spread_points == 0.0            # config_for does not invent a cost


# --- the CSV loader, so vendor data can be tested without a cTrader cache --------------------

def test_a_csv_with_epoch_seconds_is_read_as_utc(tmp_path):
    path = tmp_path / "us30.csv"
    path.write_text("time,open,high,low,close,volume\n"
                    "1700000000,35000.0,35010.0,34990.0,35005.0,120\n"
                    "1700000060,35005.0,35020.0,35000.0,35015.0,90\n", encoding="utf-8")
    frame = load_csv(path)
    assert len(frame) == 2
    assert str(frame.index[0]) == "2023-11-14 22:13:20+00:00"
    assert frame["high"].iloc[1] == 35020.0


def test_a_csv_with_naive_local_time_is_localised_then_converted(tmp_path):
    path = tmp_path / "de40.csv"
    path.write_text("Date,Open,High,Low,Close\n"
                    "2024-03-01 09:00:00,17700.0,17720.0,17690.0,17710.0\n", encoding="utf-8")
    # the same wall clock is 08:00 UTC in Berlin in March (CET), which is what the engine wants
    frame = load_csv(path, tz="Europe/Berlin")
    assert str(frame.index[0]) == "2024-03-01 08:00:00+00:00"


def test_a_csv_without_ohlc_columns_fails_loudly(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("time,price\n1700000000,35000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="open/high/low/close"):
        load_csv(path)
