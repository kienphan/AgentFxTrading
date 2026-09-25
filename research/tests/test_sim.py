import pytest

np = pytest.importorskip("numpy")

from research.bars import Bars  # noqa: E402
from research.sim import Trade, make_levels, place_order, run_account  # noqa: E402
from research.specs import SPECS, flowrsi_params  # noqa: E402
from research.ticks import Ticks  # noqa: E402

EUR = SPECS["EURUSD"]
X1 = dict(EnableMacroTmsFilter=False, EnableBreakEven=False, EnableTrailingStop=False, EnablePartialClose=False)


def mk_bars(h, l, c, event_tick=None):
    n = len(c)
    ev = np.arange(n) + 1 if event_tick is None else np.asarray(event_tick)
    return Bars(np.arange(n, dtype=np.int64) * 900_000, np.asarray(c, float), np.asarray(h, float),
                np.asarray(l, float), np.asarray(c, float), np.arange(n), ev, 15)


def test_levels_swing_and_atr_modes():
    bars = mk_bars([1.101] * 20, [1.099] * 20, [1.1] * 20)
    p = flowrsi_params("EURUSD", EnableMacroTmsFilter=False)
    sl, tp = make_levels(bars, EUR, p, "swing")(19, 1, 1.1, 1.1001)
    assert sl == pytest.approx(1.098) and tp == pytest.approx(1.1031)
    sl, tp = make_levels(bars, EUR, p, "atr")(19, 1, 1.1, 1.1001)
    assert sl == pytest.approx(1.097) and tp == pytest.approx(1.1046)
    sl, tp = make_levels(bars, EUR, p, "swing")(19, -1, 1.1, 1.1001)
    assert sl == pytest.approx(1.1021) and tp == pytest.approx(1.097)


def test_place_order_applies_the_floor_and_resizes_on_the_ask_distance():
    p = flowrsi_params("EURUSD")
    units, sl, tp = place_order(1, 1.09950, 1.10080, 1.10000, 1.10002, 2000.0, EUR, p)
    assert sl == pytest.approx(1.09850)
    assert tp == pytest.approx(1.10227)
    assert units == 7000


def test_place_order_refuses_a_stop_on_the_wrong_side():
    p = flowrsi_params("EURUSD", MinSlFloorPips=5.0)
    assert place_order(1, 1.10100, 1.10500, 1.10000, 1.10002, 2000.0, EUR, p) is None


def test_run_account_x1_one_position_at_a_time_and_spread_gate():
    bid = np.array([1.1, 1.1, 1.1005, 1.1010, 1.1020, 1.1035, 1.1, 1.1, 1.1, 1.1])
    ask = bid + 0.0001
    ask[6] = bid[6] + 0.0040                        # 40 pips > MaxSpreadPips 30
    t = Ticks(np.arange(10, dtype=np.int64) * 60_000, bid, ask)
    bars = mk_bars([1.1] * 3, [1.1] * 3, [1.1] * 3, event_tick=[1, 3, 6])
    p = flowrsi_params("EURUSD", **X1)
    levels = lambda i, side, b, a: (b - 0.0020, a + 0.0030)  # noqa: E731
    trades, refused = run_account(t, bars, np.array([1, 1, 1], np.int8), levels, EUR, p, "X1", 2000.0)
    assert refused == 0 and len(trades) == 1
    tr = trades[0]
    assert (tr.entry_tick, tr.exit_tick, tr.units, tr.deals) == (1, 5, 5000, 1)
    assert tr.entry == pytest.approx(1.1001)
    assert tr.net == pytest.approx((1.1035 - 1.1001) * 5000 - 5000 * EUR.comm_per_unit)
    assert tr.risk_usd == pytest.approx(10.0)


def test_x1_sell_takes_profit_at_the_tick_price():
    bid = np.array([1.1, 1.1, 1.1014, 1.0968])
    ask = np.array([1.10001, 1.1001, 1.1015, 1.0969])
    t = Ticks(np.arange(4, dtype=np.int64), bid, ask)
    bars = mk_bars([1.1], [1.1], [1.1], event_tick=[0])
    p = flowrsi_params("EURUSD", **X1)
    levels = lambda i, side, b, a: (1.10200, 1.09700)  # noqa: E731
    trades, _ = run_account(t, bars, np.array([-1], np.int8), levels, EUR, p, "X1", 2000.0)
    tr = trades[0]
    assert tr.entry == 1.1 and tr.exit_tick == 3
    assert tr.net == pytest.approx((1.1 - 1.0969) * tr.units - tr.units * EUR.comm_per_unit)


def test_signals_before_start_ms_are_warm_up_only():
    bid = np.full(5, 1.1)
    t = Ticks(np.array([0, 1, 2, 3, 4], np.int64) * 60_000, bid, bid + 0.0001)
    bars = mk_bars([1.1] * 2, [1.1] * 2, [1.1] * 2, event_tick=[1, 3])
    p = flowrsi_params("EURUSD", **X1)
    levels = lambda i, side, b, a: (b - 0.0020, a + 0.0030)  # noqa: E731
    trades, _ = run_account(t, bars, np.array([1, 1], np.int8), levels, EUR, p, "X1", 2000.0, start_ms=120_000)
    assert [tr.entry_tick for tr in trades] == [3]


from research.sim import _run_flowrsi  # noqa: E402


def _x2_ticks(bids):
    bid = np.array(bids)
    return Ticks(np.arange(len(bid), dtype=np.int64), bid, bid + 0.00001)


def test_x2_break_even_partial_tp_removal_and_trailing():
    t = _x2_ticks([1.09999, 1.10100, 1.10205, 1.10250, 1.10600, 1.10340])
    tr = Trade(1, 0, 0, 1.10000, 10000, 20.0, 10.0)
    _run_flowrsi(t, tr, 1.09800, 1.10300, EUR, flowrsi_params("EURUSD", EnableMacroTmsFilter=False))
    fee = 5000 * EUR.comm_per_unit
    # tick 2: +20.5 pips = 1R -> BE to entry + 1.17 pips and half closed at 1.10205
    # tick 3: 1.25R -> trailing arms, candidate clamps to zero-loss, TP removed (partial done)
    # tick 4: 3R -> stop trails to 1.10600 - 25 pips = 1.10350; tick 5 hits it at 1.10340
    assert tr.deals == 2 and tr.exit_tick == 5
    assert tr.net == pytest.approx((0.00205 * 5000 - fee) + (0.00340 * 5000 - fee))


def test_x2_min_lot_position_cannot_partial_and_keeps_its_tp():
    t = _x2_ticks([1.09999, 1.10205, 1.10250, 1.10310])
    tr = Trade(1, 0, 0, 1.10000, 1000, 20.0, 10.0)
    _run_flowrsi(t, tr, 1.09800, 1.10300, EUR, flowrsi_params("EURUSD", EnableMacroTmsFilter=False))
    assert tr.deals == 1 and tr.exit_tick == 3                  # TP 1.10300 hit at 1.10310
    assert tr.net == pytest.approx(0.00310 * 1000 - 1000 * EUR.comm_per_unit)


def test_x2_stop_out_before_any_trigger_is_a_full_loss():
    t = _x2_ticks([1.09999, 1.10050, 1.09790])
    tr = Trade(1, 0, 0, 1.10000, 10000, 20.0, 10.0)
    _run_flowrsi(t, tr, 1.09800, 1.10300, EUR, flowrsi_params("EURUSD", EnableMacroTmsFilter=False))
    assert tr.deals == 1 and tr.exit_tick == 2
    assert tr.net == pytest.approx(-0.00210 * 10000 - 10000 * EUR.comm_per_unit)


def test_x2_sell_break_even_moves_the_stop_below_entry():
    ask = np.array([1.10000, 1.09795, 1.10000])
    t = Ticks(np.arange(3, dtype=np.int64), ask - 0.00001, ask)
    tr = Trade(-1, 0, 0, 1.10000, 10000, 20.0, 10.0)
    _run_flowrsi(t, tr, 1.10200, 1.09700, EUR, flowrsi_params("EURUSD", EnableMacroTmsFilter=False))
    fee_pips = EUR.comm_per_unit / EUR.pip_value
    zero_loss = 1.10000 - (fee_pips + 0.5) * 0.0001
    fee = 5000 * EUR.comm_per_unit
    # tick 1: 20.5 pips -> BE + half closed at 1.09795; tick 2: ask 1.10000 >= zero-loss stop
    assert tr.deals == 2 and tr.exit_tick == 2
    assert tr.net == pytest.approx((0.00205 * 5000 - fee) + ((1.10000 - 1.10000) * -1 * 5000 - fee))
    assert zero_loss < 1.10000


def test_positions_held_over_a_rollover_pay_swap():
    from research.specs import SWAPS
    tue_16_ny = 1767733200000                          # Tue 2026-01-06 16:00 New York
    ts = np.array([0, 1, 2], np.int64) * 3_600_000 + tue_16_ny
    t = Ticks(ts, np.array([1.1, 1.1, 1.0968]), np.array([1.10001, 1.1001, 1.0969]))
    bars = mk_bars([1.1], [1.1], [1.1], event_tick=[0])
    p = flowrsi_params("EURUSD", **X1)
    levels = lambda i, side, b, a: (1.10200, 1.09700)  # noqa: E731
    trades, _ = run_account(t, bars, np.array([-1], np.int8), levels, EUR, p, "X1", 2000.0)
    tr = trades[0]                                     # sold at 16:00, TP hit at 18:00: one rollover
    swap = SWAPS["EURUSD"][1] * tr.units
    assert tr.net == pytest.approx((1.1 - 1.0969) * tr.units - tr.units * EUR.comm_per_unit + swap)
