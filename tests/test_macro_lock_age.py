"""
Regression guard for daily-check 2026-09-24 #4: an old H1 cross-lock kept gating FlowRSI.

The macro TMS bias is the last *confirmed* RSI/Red cross, held until the opposite cross is
confirmed, with no age limit. On 2026-09-23 GBPUSD and EURUSD stayed BULLISH for 31 -> 40 H1
bars, AUDUSD for 22 -> 48 and XAUUSD for 25 -> 33, while all four fell: the gate let the
losing BUYs through and blocked the SELLs that went with the trend. FlowRSI index BUYs
went 3 wins in 12 on 2026-09-24.

A lock older than MaxMacroLockAgeBars (24 macro bars) no longer describes the trend. FlowRsiBot
then takes the H1 EMA50 bias (close beyond the EMA, EMA sloping the same way) and says so in
tms.bias_source = "ema". The server takes the bot's EMA bias, but never a cross-lock older than
MACRO_LOCK_MAX_AGE_BARS, from the snapshot or borrowed from another bot, and never publishes an
EMA bias as a cross-lock for other bots to borrow.
"""

import re
from pathlib import Path

import pytest

from app.server import (
    MACRO_LOCK_MAX_AGE_BARS,
    MarketSnapshot,
    TmsSignals,
    _TMS_CROSS_LOCK_REGISTRY,
    get_tms_cross_lock,
    trade_decision,
    update_tms_cross_lock,
)

FLOWRSI = Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs"


@pytest.fixture(autouse=True)
def _clear_cross_lock_registry():
    _TMS_CROSS_LOCK_REGISTRY.clear()
    yield
    _TMS_CROSS_LOCK_REGISTRY.clear()


def _snap(tms=None, **extra):
    return MarketSnapshot(
        bot_id="cbot-demo-demo-gbpusd-all-flowrsi", symbol="GBPUSD", timeframe="Minute15",
        bid=1.3250, ask=1.3251, tms=tms, **extra,
    )


def test_limit_is_24_macro_bars():
    assert MACRO_LOCK_MAX_AGE_BARS == 24


def test_own_lock_within_the_limit_gates():
    assert get_tms_cross_lock(_snap(TmsSignals(bias="BULLISH", bars_since_cross=24)))[:2] == ("BULLISH", 24)


def test_own_lock_past_the_limit_leaves_the_gate_open():
    """An old .algo still sends its raw lock; 2026-09-23 GBPUSD was 31-40 bars old."""
    bias, _, _ = get_tms_cross_lock(_snap(TmsSignals(bias="BULLISH", bars_since_cross=31)))
    assert bias == "NEUTRAL"


def test_bots_ema_bias_is_used_whatever_the_lock_age():
    bias, age, source = get_tms_cross_lock(
        _snap(TmsSignals(bias="BEARISH", bars_since_cross=40, bias_source="ema")))
    assert (bias, age) == ("BEARISH", 40)
    assert "EMA" in source


def test_borrowed_lock_past_the_limit_is_not_used():
    update_tms_cross_lock("GBPUSD", "BULLISH", bars_since_cross=31, source="session bot")
    assert get_tms_cross_lock(_snap())[0] == "NEUTRAL"


def test_borrowed_lock_within_the_limit_is_used():
    update_tms_cross_lock("GBPUSD", "BEARISH", bars_since_cross=5, source="session bot")
    assert get_tms_cross_lock(_snap()) == ("BEARISH", 5, "session bot")


@pytest.mark.anyio
async def test_ema_bias_is_not_published_as_a_cross_lock():
    await trade_decision(_snap(TmsSignals(bias="BEARISH", bars_since_cross=40, bias_source="ema"),
                               candidate_action="NONE"))
    assert "GBPUSD" not in _TMS_CROSS_LOCK_REGISTRY


@pytest.mark.anyio
async def test_stale_ema_gate_blocks_a_counter_trend_buy(monkeypatch):
    import app.server as server_mod

    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("the pre-LLM gate must stop a BUY against the EMA bias")

    monkeypatch.setattr(server_mod.llm_client, "chat", _fail_if_called)
    decision = await trade_decision(_snap(
        TmsSignals(bias="BEARISH", bars_since_cross=40, bias_source="ema"),
        candidate_action="BUY", rsi_cross_signal="Bullish_Cross", fast_rsi=35.0, slow_rsi=32.0,
        technical_sl_price=1.3220, technical_tp_price=1.3300, is_discount=True,
    ))
    assert decision.action == "HOLD"
    assert "BEARISH" in decision.reason


# ---- FlowRsiBot source ----

def _src() -> str:
    return FLOWRSI.read_text(encoding="utf-8")


def _method_body(signature: str) -> str:
    src = _src()
    start = src.index(signature)
    open_idx = src.index("{", start)
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces in {signature!r}")


@pytest.mark.parametrize("name,default", [("MaxMacroLockAgeBars", "24"), ("MacroEmaPeriod", "50")])
def test_flowrsi_declares_the_parameters(name, default):
    m = re.search(r"\[Parameter\(([^\]]*)\)\]\s*public int " + name + r"\b", _src())
    assert m, f"FlowRsiBot has no {name} parameter"
    assert f"DefaultValue = {default}" in m.group(1)


def test_flowrsi_builds_the_ema_on_macro_bars():
    assert re.search(r"Indicators\.ExponentialMovingAverage\(\s*_macroBars\.ClosePrices\s*,\s*MacroEmaPeriod\s*\)", _src())


def test_flowrsi_replaces_a_stale_lock_by_the_ema_bias():
    body = _method_body("private TmsSignals CalculateMacroTmsSignals(")
    assert "MaxMacroLockAgeBars" in body
    assert "MacroEmaBias(" in body
    assert 'bias_source = biasSource' in body
    assert '"ema"' in body


def test_flowrsi_sends_bias_source():
    body = _method_body("public class TmsSignals")
    assert "bias_source" in body


def test_ema_bias_needs_price_and_slope_to_agree():
    body = _method_body("private string MacroEmaBias(")
    assert "close > ema && ema > emaBefore" in body
    assert "close < ema && ema < emaBefore" in body
