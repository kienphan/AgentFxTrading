import pytest

np = pytest.importorskip("numpy")

from research.calibrate import ctrader_positions, match  # noqa: E402
from research.sim import Trade  # noqa: E402


def test_ctrader_positions_group_partial_deals():
    report = {"history": {"items": [
        {"entryTime": 1000, "direction": "buy", "entryPrice": 1.1, "closeTime": 5000, "volume": 5000, "net": 10.0, "balance": 2010.0},
        {"entryTime": 1000, "direction": "buy", "entryPrice": 1.1, "closeTime": 9000, "volume": 5000, "net": 5.0, "balance": 2015.0},
        {"entryTime": 20000, "direction": "sell", "entryPrice": 1.2, "closeTime": 30000, "volume": 7000, "net": -10.075, "balance": 2004.925},
    ]}}
    pos = ctrader_positions(report)
    assert [(p[0], p[1], p[2]) for p in pos] == [(1000, 1, 10000), (20000, -1, 7000)]
    assert pos[0][4] == pytest.approx(15.0 / (0.005 * 2000.0))
    assert pos[1][4] == pytest.approx(-10.075 / (0.005 * 2015.0))


def test_match_needs_same_side_within_one_bar():
    ct = [(0, 1, 1000, 5.0, 0.5), (3_600_000, -1, 1000, -10.0, -1.0)]
    sim = [Trade(1, 0, 60_000, 1.1, 1000, 20, 10), Trade(-1, 0, 3_600_000 + 1_000_000, 1.1, 1000, 20, 10)]
    pairs = match(ct, sim)
    assert len(pairs) == 1 and pairs[0][0] == ct[0]
