import pytest

np = pytest.importorskip("numpy")

from research.indicators import adx, atr_sma, bollinger, ema, prior_max, prior_min, rsi  # noqa: E402


def test_ema_seeds_with_the_first_value():
    assert np.allclose(ema(np.array([1.0, 2.0, 3.0]), 3), [1.0, 1.5, 2.25])


def test_rsi_is_wilder_smoothing():
    out = rsi(np.array([10.0, 11.0, 10.0]), 2)
    assert out[0] == 100.0 and out[1] == 100.0
    assert out[2] == pytest.approx(100 - 100 / 1.5)


def test_rsi_extremes():
    assert rsi(np.arange(50, dtype=float), 14)[-1] == 100.0
    assert rsi(np.arange(50, 0, -1, dtype=float), 14)[-1] == pytest.approx(0.0)


def test_atr_is_a_simple_average_of_true_range():
    h, l, c = np.array([2.0, 3.0, 4.0]), np.array([1.0, 1.0, 2.0]), np.array([1.5, 2.5, 3.0])
    out = atr_sma(h, l, c, 2)
    assert np.isnan(out[0])
    assert out[1:].tolist() == [1.5, 2.0]


def test_prior_window_excludes_the_current_bar():
    x = np.array([1.0, 5.0, 2.0, 3.0])
    assert np.isnan(prior_max(x, 2)[:2]).all()
    assert prior_max(x, 2)[2:].tolist() == [5.0, 5.0]
    assert prior_min(x, 2)[2:].tolist() == [1.0, 2.0]


def test_bollinger_uses_population_std():
    c = np.array([1.0, 2.0, 3.0])
    lower, mid, upper = bollinger(c, 3, 2.0)
    assert mid[2] == 2.0
    assert upper[2] == pytest.approx(2.0 + 2 * np.std([1.0, 2.0, 3.0]))
    assert lower[2] == pytest.approx(2.0 - 2 * np.std([1.0, 2.0, 3.0]))


def test_adx_high_in_a_trend_low_in_a_range():
    i = np.arange(200, dtype=float)
    assert adx(i + 1.0, i, i + 0.5, 14)[-1] > 25
    c = np.where(np.arange(200) % 2 == 0, 1.0, 1.001)
    assert adx(c + 0.0005, c - 0.0005, c, 14)[-1] < 20
