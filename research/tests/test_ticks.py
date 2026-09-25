import gzip
from datetime import date

import pytest

np = pytest.importorskip("numpy")

from research.ticks import DATA_DIR, day_ms, load_ticks  # noqa: E402


def _write(root, symbol, day, rows):
    d = root / symbol / "t1"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day:%Y%m%d}.zticks").write_bytes(gzip.compress(np.asarray(rows, dtype="<i8").tobytes()))


def test_forward_fills_unchanged_sides_across_days(tmp_path):
    _write(tmp_path, "EURUSD", date(2026, 1, 1), [[1000, 117000, 117010], [2000, 0, 117020]])
    _write(tmp_path, "EURUSD", date(2026, 1, 2), [[86_401_000, 117005, 0]])
    t = load_ticks("EURUSD", date(2026, 1, 1), date(2026, 1, 2), data_dir=tmp_path)
    assert t.ts.tolist() == [1000, 2000, 86_401_000]
    assert np.allclose(t.bid, [1.17, 1.17, 1.17005])
    assert np.allclose(t.ask, [1.1701, 1.1702, 1.1702])


def test_drops_ticks_until_both_sides_are_known(tmp_path):
    _write(tmp_path, "EURUSD", date(2026, 1, 1), [[1000, 117000, 0], [2000, 0, 117010]])
    t = load_ticks("EURUSD", date(2026, 1, 1), date(2026, 1, 1), data_dir=tmp_path)
    assert t.ts.tolist() == [2000]
    assert len(t) == 1


def test_missing_days_give_empty_ticks(tmp_path):
    t = load_ticks("EURUSD", date(2026, 1, 1), date(2026, 1, 3), data_dir=tmp_path)
    assert len(t) == 0


def test_day_ms_is_utc_midnight():
    assert day_ms(date(2026, 1, 1)) == 1767225600000


REAL = DATA_DIR / "EURUSD" / "t1" / "20260105.zticks"


@pytest.mark.skipif(not REAL.exists(), reason="run research/pull_cache.sh first (Task 8)")
def test_real_day_is_sorted_with_ask_above_bid():
    t = load_ticks("EURUSD", date(2026, 1, 5), date(2026, 1, 5))
    assert len(t) > 10_000
    assert np.all(np.diff(t.ts) >= 0)
    assert np.mean(t.ask >= t.bid) > 0.999
    assert 1.0 < t.bid.mean() < 1.4
