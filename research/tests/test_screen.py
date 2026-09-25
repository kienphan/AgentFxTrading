import pytest

np = pytest.importorskip("numpy")

from research.screen import PAIRS, c2_means, params_for, summarize, verdict  # noqa: E402


def _rows(config, pattern_by_period):
    rows = []
    for symbol in PAIRS:
        for period, pattern in pattern_by_period.items():
            for k in range(16):
                rows.append({"config": config, "symbol": symbol, "period": period, "r": pattern[k % 2]})
    return rows


C2 = {"P1": -0.1, "P2": -0.1, "P3": -0.1}


def test_a_consistent_edge_passes():
    s = summarize(_rows("E9/X1", {"P1": (1.0, -0.5), "P2": (1.0, -0.5), "P3": (1.0, -0.5)}))
    assert verdict(s["E9/X1"], C2) == []


def test_one_losing_period_fails():
    s = summarize(_rows("E9/X1", {"P1": (1.0, -0.5), "P2": (1.0, -0.5), "P3": (-1.0, 0.5)}))
    fails = verdict(s["E9/X1"], C2)
    assert any(f.startswith("P3") for f in fails)


def test_too_few_positions_fail():
    rows = [r for r in _rows("E9/X1", {"P1": (1.0, -0.5), "P2": (1.0, -0.5), "P3": (1.0, -0.5)})][::4]
    assert any("positions/pair/month" in f for f in verdict(summarize(rows)["E9/X1"], C2))


def test_c2_mean_is_averaged_over_seeds():
    rows = _rows("C2/X1/s0", {"P1": (0.0, -0.2), "P2": (0.0, -0.2), "P3": (0.0, -0.2)}) + \
           _rows("C2/X1/s1", {"P1": (0.0, 0.0), "P2": (0.0, 0.0), "P3": (0.0, 0.0)})
    assert c2_means(summarize(rows), "X1")["P1"] == pytest.approx(-0.05)


def test_params_for_applies_the_spec_overrides():
    assert params_for("XAUUSD", "C0", "X2")["MinSlFloorPips"] == 1500.0
    assert params_for("XAUUSD", "E2a", "X2")["MinSlFloorPips"] == 500.0
    assert params_for("GBPJPY", "C1", "X2")["MinSlFloorPips"] == 30.0
    assert params_for("EURUSD", "C1", "X2")["MinSlFloorPips"] == 25.0
    x1 = params_for("EURUSD", "E3b", "X1")
    assert not x1["EnableBreakEven"] and not x1["EnableTrailingStop"] and not x1["EnablePartialClose"]
    assert all(not params_for(s, "E1a", "X2")["EnableMacroTmsFilter"] for s in PAIRS)
