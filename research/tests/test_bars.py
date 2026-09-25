from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

from research.bars import build_bars, decode_zbars  # noqa: E402
from research.ticks import DATA_DIR, PRICE_SCALE, Ticks, load_ticks  # noqa: E402


def test_bars_bucket_bid_prices_and_mark_the_close_event_tick():
    ts = np.array([0, 60_000, 899_999, 900_000, 1_000_000, 2_700_000], np.int64)
    bid = np.array([1.0, 1.2, 0.9, 1.1, 1.3, 1.05])
    b = build_bars(Ticks(ts, bid, bid + 0.0001), 15)
    assert b.open_time.tolist() == [0, 900_000, 2_700_000]
    assert b.o.tolist() == [1.0, 1.1, 1.05]
    assert b.h.tolist() == [1.2, 1.3, 1.05]
    assert b.l.tolist() == [0.9, 1.1, 1.05]
    assert b.c.tolist() == [0.9, 1.3, 1.05]
    assert b.first_tick.tolist() == [0, 3, 5]
    assert b.event_tick.tolist() == [3, 5, 6]
    assert b.close_time.tolist() == [900_000, 1_800_000, 3_600_000]


def test_h1_bars_align_to_the_utc_hour():
    ts = np.array([0, 3_599_999, 3_600_000], np.int64)
    bid = np.array([1.0, 2.0, 3.0])
    b = build_bars(Ticks(ts, bid, bid), 60)
    assert b.open_time.tolist() == [0, 3_600_000]
    assert b.c.tolist() == [2.0, 3.0]


def _zbar_months():
    for m15 in sorted(DATA_DIR.glob("*/m15")):
        for path in sorted(m15.glob("2026*.zbars")):
            yield m15.parent.name, path


@pytest.mark.skipif(next(_zbar_months(), None) is None, reason="run research/pull_cache.sh first (Task 8)")
def test_m15_bars_match_ctrader_zbars():
    """If no bar times are in common, print zb[:3, 0] against b.open_time[:3]: the .zbars stamp may be
    seconds or the close time. Convert the stamp; do not loosen the price tolerance."""
    checked = 0
    for symbol, path in _zbar_months():
        zb = decode_zbars(path)
        if len(zb) == 0:
            continue
        zb = zb[np.unique(zb[:, 0], return_index=True)[1]]
        year, month = int(path.stem[:4]), int(path.stem[4:])
        start = date(year, month, 1)
        end = min(date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1), date(2026, 9, 24))
        t = load_ticks(symbol, start, end)
        if len(t) == 0:
            continue
        b = build_bars(t, 15)
        common, zi, bi = np.intersect1d(zb[:, 0], b.open_time, return_indices=True)
        assert len(common) >= 0.95 * len(zb), f"{symbol} {path.stem}: {len(common)}/{len(zb)} bar times in common"
        for col, built in ((1, b.o), (2, b.h), (3, b.l), (4, b.c)):
            assert np.allclose(zb[zi, col] / PRICE_SCALE, built[bi], atol=0.5 / PRICE_SCALE), f"{symbol} {path.stem} col {col}"
        checked += 1
        if checked == 3:
            break
    assert checked > 0
