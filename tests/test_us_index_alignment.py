"""
Unit tests for US Equity Indices (US30, USTEC, US500) Correlation & Directional Alignment Filter.
Ensures that the trading hub enforces macro alignment across all US index bots:
- Prevents conflicting opposite-direction trades (e.g. BUY USTEC while US30 is SELL).
- Bypasses LLM call via deterministic Cycle Gate when opposing US index position exists.
- Enforces hard block via PortfolioManager.check_risk and post-decision Risk Guard.
"""

import pytest
from datetime import datetime, timezone
from app.portfolio import is_us_index, PortfolioManager, PortfolioConfig
from app.accounts import AccountRegistry
from app.server import (
    MarketSnapshot,
    PositionInfo,
    TmsSignals,
    OrbData,
    MarketRegimeInfo,
    SessionInfo,
    evaluate_cycle_gate,
    check_us_index_conflict,
    BarData,
)

@pytest.fixture
def clean_pm():
    """Provide the isolated test PortfolioManager instance and clean positions table."""
    from app.portfolio import get_portfolio_manager
    pm = get_portfolio_manager()
    conn = pm._get_conn()
    conn.execute("DELETE FROM positions WHERE account_id = 'test-acc'")
    if hasattr(conn, "commit"):
        conn.commit()
    conn.close()
    
    yield pm
    
    conn = pm._get_conn()
    conn.execute("DELETE FROM positions WHERE account_id = 'test-acc'")
    if hasattr(conn, "commit"):
        conn.commit()
    conn.close()
    


def test_is_us_index_detection():
    # Valid US index symbols & aliases
    assert is_us_index("US30") is True
    assert is_us_index("us30") is True
    assert is_us_index("USTEC") is True
    assert is_us_index("ustec_m15") is True
    assert is_us_index("US500") is True
    assert is_us_index("NAS100") is True
    assert is_us_index("SPX500") is True
    assert is_us_index("DJ30") is True

    # Non-US index symbols
    assert is_us_index("DE40") is False
    assert is_us_index("UK100") is False
    assert is_us_index("JP225") is False
    assert is_us_index("HK50") is False
    assert is_us_index("XAUUSD") is False
    assert is_us_index("EURUSD") is False
    assert is_us_index("BTCUSD") is False
    assert is_us_index("") is False
    assert is_us_index(None) is False


def test_check_risk_allows_first_us_index(clean_pm):
    # When no positions are open, both BUY and SELL are permitted
    allowed, reason = clean_pm.check_risk("USTEC", "BUY", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is True
    assert reason == "OK"

    allowed, reason = clean_pm.check_risk("US30", "SELL", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is True
    assert reason == "OK"


def test_check_risk_blocks_opposing_us_index(clean_pm):
    # Open a BUY position on USTEC
    clean_pm.register_position(
        bot_id="ustec_m15",
        symbol="USTEC",
        side="Buy",
        volume=0.1,
        entry_price=29100.0,
        sl_pips=1000.0,
        tp_pips=1500.0,
        account_id="test-acc"
    )

    # 1. Same-direction US index trade (BUY US30) -> Allowed
    allowed, reason = clean_pm.check_risk("US30", "BUY", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is True
    assert reason == "OK"

    # 2. Opposing-direction US index trade (SELL US30) -> BLOCKED
    allowed, reason = clean_pm.check_risk("US30", "SELL", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is False
    assert "US Index alignment conflict" in reason
    assert "cannot open SELL US30" in reason
    assert "USTEC" in reason
    assert "BUY" in reason

    # 3. Opposing-direction US500 trade (SELL US500) -> BLOCKED
    allowed, reason = clean_pm.check_risk("US500", "SELL", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is False
    assert "US Index alignment conflict" in reason

    # 4. Non-US index trade (SELL DE40 or SELL EURUSD) -> Allowed (not a US index)
    allowed, reason = clean_pm.check_risk("DE40", "SELL", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is True

    allowed, reason = clean_pm.check_risk("EURUSD", "SELL", 0.1, account_balance=10000.0, account_id="test-acc")
    assert allowed is True


def test_evaluate_cycle_gate_us_index_alignment(clean_pm, monkeypatch):
    import app.server as server_mod
    monkeypatch.setattr(server_mod, "get_portfolio_manager", lambda: clean_pm)

    # Open a BUY position on USTEC
    clean_pm.register_position(
        bot_id="ustec_m15",
        symbol="USTEC",
        side="Buy",
        volume=0.1,
        entry_price=29100.0,
        sl_pips=1000.0,
        tp_pips=1500.0,
        account_id="test-acc"
    )

    # Prepare US30 snapshot with BEARISH TMS + DOWN breakout (opposing setup)
    us30_bear_snap = MarketSnapshot(
        bot_id="us30_m15",
        symbol="US30",
        timeframe="Minute15",
        ask=52000.0,
        bid=51995.0,
        account_number="12345",
        account_label="test-acc",
        tms=TmsSignals(bias="BEARISH", bars_since_cross=3),
        orb=OrbData(breakout_direction="down", is_decisive=True, bars_since_breakout=0),
        session=SessionInfo(is_trading_time=True, phase="mid", session_name="newyork_index"),
        market=MarketRegimeInfo(regime="trending", or_flips=0),
        bars=[BarData(time="2026-09-17 15:00:00")]
     )
    decision = evaluate_cycle_gate(us30_bear_snap, account_id="test-acc")
    assert decision is not None
    assert decision.action == "HOLD"
    assert "US Index Alignment Guard" in decision.reason
    assert "USTEC (BUY)" in decision.reason

    # Prepare US30 snapshot with BULLISH TMS + UP breakout (aligned setup)
    us30_bull_snap = MarketSnapshot(
        bot_id="us30_m15",
        symbol="US30",
        timeframe="Minute15",
        ask=52100.0,
        bid=52095.0,
        account_number="12345",
        account_label="test-acc",
        tms=TmsSignals(bias="BULLISH", bars_since_cross=3),
        orb=OrbData(breakout_direction="up", is_decisive=True, bars_since_breakout=0),
        session=SessionInfo(is_trading_time=True, phase="mid", session_name="newyork_index"),
        market=MarketRegimeInfo(regime="trending", or_flips=0),
        bars=[BarData(time="2026-09-17 15:00:00")]
     )

    # Should not be gated by US Index Alignment Guard
    decision_bull = evaluate_cycle_gate(us30_bull_snap, account_id="test-acc")
    if decision_bull is not None:
        assert "US Index Alignment Guard" not in decision_bull.reason


@pytest.mark.anyio
async def test_post_decision_risk_guard_blocks_opposing_us_index(clean_pm, monkeypatch):
    import app.server as server_mod
    monkeypatch.setattr(server_mod, "get_portfolio_manager", lambda: clean_pm)
    monkeypatch.setattr(server_mod, "portfolio_manager", clean_pm)

    us30_snap = MarketSnapshot(
        bot_id="us30_m15",
        symbol="US30",
        timeframe="Minute15",
        ask=52000.0,
        bid=51995.0,
        account_number="12345",
        account_label="demo",
        tms=TmsSignals(bias="BEARISH", bars_since_cross=3),
        orb=OrbData(breakout_direction="down", is_decisive=True, bars_since_breakout=0),
        session=SessionInfo(is_trading_time=True, phase="mid", session_name="newyork_index"),
        market=MarketRegimeInfo(regime="trending", or_flips=0),
    )
    acc_id = server_mod._resolve_account(us30_snap)

    # Open a BUY position on USTEC on the same resolved account
    clean_pm.register_position(
        bot_id="ustec_m15",
        symbol="USTEC",
        side="Buy",
        volume=0.1,
        entry_price=29100.0,
        sl_pips=1000.0,
        tp_pips=1500.0,
        account_id=acc_id
    )

    # Mock LLM client to propose a SELL on US30
    from app.llm_client import LLMClient

    class MockLLMClient(LLMClient):
        async def chat(self, messages, **kwargs):
            import json
            return json.dumps({
                "action": "SELL",
                "volume_lots": 0.0,
                "sl_pips": 1000,
                "tp_pips": 1500,
                "confidence": 85.0,
                "reason": "Technical breakdown on M15"
            })

    monkeypatch.setattr(server_mod, "llm_client", MockLLMClient())
    # Bypass cycle gate so it evaluates the LLM path
    monkeypatch.setattr(server_mod, "evaluate_cycle_gate", lambda snap, account_id=None: None)

    decision = await server_mod.trade_decision(us30_snap)
    assert decision.action == "HOLD"
    assert "[Risk Guard]" in decision.reason
    assert "US Index alignment conflict" in decision.reason
    assert "USTEC" in decision.reason
    assert "BUY" in decision.reason


@pytest.mark.anyio
async def test_pre_llm_risk_check_does_not_assume_buy(tmp_path, monkeypatch):
    """
    The capacity check before the LLM call passed side="BUY" for every bot. With USTEC
    SELL open, an aligned FlowRSI SELL candidate on US30 was refused as "cannot open BUY
    US30" and never reached the model.
    """
    import json
    from unittest.mock import AsyncMock
    import app.server as server_mod
    import app.news_service as news_mod

    pm = PortfolioManager(db_path=str(tmp_path / "pre_llm.db"))
    monkeypatch.setattr(server_mod, "portfolio_manager", pm)
    monkeypatch.setattr(news_mod, "is_news_blackout_active", AsyncMock(return_value=(False, None, 0)))
    chat = AsyncMock(return_value=json.dumps({"action": "SELL", "confidence": 85.0, "reason": "aligned"}))
    monkeypatch.setattr(server_mod.llm_client, "chat", chat)

    snap = MarketSnapshot(
        bot_id="cbot-demo-demo-us30-all-flowrsi",
        symbol="US30",
        timeframe="Minute15",
        ask=51946.2,
        bid=51945.0,
        account_number="12345",
        account_label="demo",
        account_balance=2000.0,
        account_margin=40.0,
        fast_rsi=40.0,
        slow_rsi=51.04,
        rsi_cross_signal="Bearish_Cross",
        is_premium=True,
        candidate_action="SELL",
        technical_sl_price=52020.0,
        technical_tp_price=51830.0,
        technical_risk_reward=1.5,
    )
    pm.register_position(
        bot_id="cbot-demo-demo-ustec-all-flowrsi", symbol="USTEC", side="Sell", volume=0.1,
        entry_price=30730.0, sl_pips=400.0, tp_pips=600.0,
        account_id=server_mod._resolve_account(snap),
    )

    decision = await server_mod.trade_decision(snap)

    assert chat.await_count == 1, "an aligned SELL was refused before the model was asked"
    assert decision.action == "SELL"
