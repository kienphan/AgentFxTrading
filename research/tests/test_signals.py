import pytest

np = pytest.importorskip("numpy")

from research.bars import Bars  # noqa: E402
from research.signals import rsi_cross_signals, smc_flags  # noqa: E402

P = {"RsiCrossLookbackBars": 3, "RsiBullishCrossMin": 25.0, "RsiBullishCrossMax": 50.0,
     "RsiBearishCrossMin": 50.0, "RsiBearishCrossMax": 75.0, "SwingLookback": 1, "EquilibriumThreshold": 0.5,
     "EnableFvgDetection": True, "FvgMinPips": 2.0, "EnableLiquiditySweepFilter": True}


def mk_bars(h, l, c):
    n = len(c)
    return Bars(np.arange(n, dtype=np.int64) * 900_000, np.asarray(c, float), np.asarray(h, float),
                np.asarray(l, float), np.asarray(c, float), np.arange(n), np.arange(n) + 1, 15)


def test_rsi_cross_stays_valid_for_the_lookback_while_confirmed():
    f = np.array([40.0, 30.0, 45.0, 46.0, 47.0, 48.0])
    s = np.array([42.0, 40.0, 41.0, 42.0, 43.0, 44.0])
    bull, bear = rsi_cross_signals(f, s, P)
    assert bull.tolist() == [False, False, True, True, True, False]
    assert not bear.any()


def test_rsi_cross_outside_the_zone_is_ignored():
    f = np.array([20.0, 15.0, 22.0])
    s = np.array([21.0, 18.0, 20.0])                      # slow RSI 20 < 25 at the cross
    bull, _ = rsi_cross_signals(f, s, P)
    assert not bull.any()


def test_smc_discount_and_sell_side_sweep():
    b = mk_bars(h=[1.02, 1.03, 1.04, 1.05, 1.04, 1.03],
                l=[1.00, 1.00, 1.01, 1.02, 1.01, 0.995],
                c=[1.01, 1.02, 1.03, 1.04, 1.02, 1.015])
    f = smc_flags(b, P, 0.0001)
    assert f["disc"][5] and not f["prem"][5]
    assert f["sw_l"][5] and not f["sw_h"][5]
    assert not f["fvg_b"][5] and not f["fvg_s"][5]


def test_smc_bullish_fvg():
    b = mk_bars(h=[1.0000, 1.0010, 1.0040], l=[0.9990, 1.0000, 1.0003], c=[0.9995, 1.0008, 1.0035])
    assert smc_flags(b, P, 0.0001)["fvg_b"][2]


from research.signals import (bb_fade, donchian_breakout, htf_index, htf_trend, pullback,  # noqa: E402
                              random_like, reverse_extreme, rsi2_extreme)


def test_htf_index_uses_only_closed_higher_bars():
    m15 = Bars(np.arange(6, dtype=np.int64) * 900_000, *(np.ones(6),) * 4, np.arange(6), np.arange(6) + 1, 15)
    h1 = Bars(np.array([0, 3_600_000], np.int64), *(np.ones(2),) * 4, np.arange(2), np.arange(2) + 1, 60)
    assert htf_index(m15, h1).tolist() == [-1, -1, -1, 0, 0, 0]


def test_htf_trend_needs_price_and_slope_and_warm_up():
    n = 120
    up = Bars(np.arange(n, dtype=np.int64) * 3_600_000, *(np.arange(n, dtype=float),) * 4, np.arange(n), np.arange(n) + 1, 60)
    tr = htf_trend(up, 50)
    assert (tr[:50] == 0).all() and tr[-1] == 1


def test_pullback_reentry_through_40_and_60():
    r = np.array([45.0, 38.0, 41.0, 50.0, 62.0, 58.0])
    trend = np.array([1, 1, 1, 1, -1, -1], np.int8)
    assert pullback(r, trend).tolist() == [0, 0, 1, 0, 0, -1]


def test_rsi2_extreme_with_and_without_trend():
    r2 = np.array([5.0, 50.0, 95.0])
    assert rsi2_extreme(r2, np.array([1, 1, 1], np.int8)).tolist() == [1, 0, 0]
    assert rsi2_extreme(r2, None).tolist() == [1, 0, -1]


def test_bb_fade_and_adx_gate():
    c = np.array([1.0, 0.8, 0.95, 1.2, 1.05])
    lower, upper = np.full(5, 0.9), np.full(5, 1.1)
    assert bb_fade(c, lower, upper).tolist() == [0, 0, 1, 0, -1]
    assert bb_fade(c, lower, upper, np.full(5, 30.0)).tolist() == [0] * 5


def test_donchian_breakout_with_trend():
    c = np.array([1.0, 2.0, 3.0])
    hh, ll = np.array([np.nan, 1.5, 2.5]), np.array([np.nan, 0.5, 0.5])
    assert donchian_breakout(c, hh, ll, np.array([1, 1, -1], np.int8)).tolist() == [0, 1, 0]


def test_reverse_extreme_flips_only_deep_crosses():
    sig = np.array([1, 1, -1, -1], np.int8)
    assert reverse_extreme(sig, np.array([28.0, 35.0, 72.0, 60.0])).tolist() == [-1, 0, 1, 0]


def test_random_like_keeps_the_signal_rate_and_is_seeded():
    sig = np.zeros(10_000, np.int8)
    sig[::10] = 1
    a, b = random_like(sig, 7), random_like(sig, 7)
    assert (a == b).all()
    assert 800 < np.count_nonzero(a) < 1200
    assert set(np.unique(a)) <= {-1, 0, 1}
