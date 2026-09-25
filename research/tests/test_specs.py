import re
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from research.specs import CBOT_DEFAULTS, SPECS, flowrsi_params, normalize_units, size_units  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _cbot_defaults():
    src = (ROOT / "cBot" / "FlowRsiBot.cs").read_text()
    pairs = re.findall(r"\[Parameter\([^\n]*?DefaultValue = ([^,)\n]+)[^\n]*\n\s*public \w+ (\w+)", src)
    return {name: value.strip() for value, name in pairs}


def test_cbot_defaults_match_the_cs_source():
    parsed = _cbot_defaults()
    for key, value in CBOT_DEFAULTS.items():
        assert key in parsed, key
        if isinstance(value, bool):
            assert parsed[key] == ("true" if value else "false"), key
        else:
            assert float(parsed[key]) == float(value), key


def test_all_fifteen_pairs_have_specs():
    assert len(SPECS) == 15
    assert SPECS["USDJPY"].pip_value == pytest.approx(0.01 * 0.0062963)


def test_flowrsi_params_layer_defaults_preset_and_overrides():
    p = flowrsi_params("XAUUSD")
    assert p["MinSlFloorPips"] == 1500.0 and p["TrailingStopDistancePips"] == 800.0
    assert p["PartialCloseRatio"] == 0.5
    assert flowrsi_params("XAUUSD", MinSlFloorPips=500.0)["MinSlFloorPips"] == 500.0
    assert flowrsi_params("EURUSD")["MinSlFloorPips"] == 15.0


def test_normalize_rounds_to_the_nearest_step():
    eur = SPECS["EURUSD"]
    assert normalize_units(6666.7, eur) == 7000
    assert normalize_units(6400, eur) == 6000
    assert normalize_units(0.123, SPECS["BTCUSD"]) == pytest.approx(0.12)


def test_size_units_matches_the_cbot_risk_engine():
    p = flowrsi_params("EURUSD")
    assert size_units(2000.0, 15.0, SPECS["EURUSD"], p) == 7000      # $10 / 15 pips -> 6667 -> 7000


def test_xau_min_lot_guardrail_at_2000():
    p, xau = flowrsi_params("XAUUSD"), SPECS["XAUUSD"]
    assert size_units(2000.0, 1500.0, xau, p) == 1                  # $15 == 1.5 x $10: allowed
    assert size_units(2000.0, 1600.0, xau, p) == 0.0                # $16 > $15: refused
    assert size_units(1990.0, 1500.0, xau, p) == 0.0                # 1.5 x $9.95 < $15: refused


from research.specs import SWAPS, rollovers, swap_usd  # noqa: E402

NY_MS = {  # 2026-01-06 is a Tuesday; New York is UTC-5 in January
    "tue_16": 1767733200000,   # Tue 2026-01-06 16:00 NY
    "thu_18": 1767913200000,   # Thu 2026-01-08 18:00 NY
    "fri_16": 1767992400000,   # Fri 2026-01-09 16:00 NY
    "mon_16": 1768251600000,   # Mon 2026-01-12 16:00 NY
}


def test_rollovers_count_the_triple_day_three_times():
    assert rollovers(NY_MS["tue_16"], NY_MS["thu_18"], triple_weekday=2) == 1 + 3 + 1   # Tue, Wed x3, Thu
    assert rollovers(NY_MS["tue_16"], NY_MS["tue_16"] + 3_600_000, triple_weekday=2) == 1
    assert rollovers(NY_MS["tue_16"], NY_MS["tue_16"] + 1_800_000, triple_weekday=2) == 0


def test_weekend_has_no_rollover_but_friday_can_be_triple():
    assert rollovers(NY_MS["fri_16"], NY_MS["mon_16"], triple_weekday=4) == 3
    assert rollovers(NY_MS["fri_16"], NY_MS["mon_16"], triple_weekday=2) == 1


def test_swap_usd_uses_the_side_rate():
    long_rate, short_rate, triple = SWAPS["AUDJPY"]
    assert swap_usd("AUDJPY", 1, 10000, NY_MS["tue_16"], NY_MS["thu_18"]) == pytest.approx(5 * long_rate * 10000)
    assert swap_usd("AUDJPY", -1, 10000, NY_MS["tue_16"], NY_MS["thu_18"]) == pytest.approx(5 * short_rate * 10000)
    assert triple == 2
