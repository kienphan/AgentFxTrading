import os
from unittest.mock import patch
import pytest
from app.llm_client import (
    _clean_env,
    _clean_env_float,
    _clean_env_int,
    OpenAICompatibleClient,
    AnthropicClient,
    create_llm_client,
    JSONResponseParser,
)


def test_clean_env_helpers(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT_1", "45.5 # comment")
    monkeypatch.setenv("TEST_FLOAT_2", '"60.0"')
    monkeypatch.setenv("TEST_FLOAT_BAD", "invalid")
    monkeypatch.setenv("TEST_INT_1", "5 // comment")
    monkeypatch.setenv("TEST_INT_2", "'10'")
    monkeypatch.setenv("TEST_INT_BAD", "not_a_number")

    assert _clean_env_float("TEST_FLOAT_1", 30.0) == 45.5
    assert _clean_env_float("TEST_FLOAT_2", 30.0) == 60.0
    assert _clean_env_float("TEST_FLOAT_BAD", 30.0) == 30.0
    assert _clean_env_float("NON_EXISTENT_VAR", 30.0) == 30.0

    assert _clean_env_int("TEST_INT_1", 2) == 5
    assert _clean_env_int("TEST_INT_2", 2) == 10
    assert _clean_env_int("TEST_INT_BAD", 2) == 2
    assert _clean_env_int("NON_EXISTENT_INT", 2) == 2


def test_openai_compatible_client_timeout_defaults(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "75.0")
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT", "25.0")
    monkeypatch.setenv("LLM_MAX_RETRIES", "4")

    client = OpenAICompatibleClient(api_key="test-key", base_url="https://api.example.com/v1")
    assert client.timeout.read == 75.0
    assert client.timeout.connect == 25.0
    assert client.max_retries == 4
    assert client.client.max_retries == 4


def test_openai_compatible_client_explicit_overrides():
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        timeout=120.0,
        connect_timeout=45.0,
        max_retries=5,
    )
    assert client.timeout.read == 120.0
    assert client.timeout.connect == 45.0
    assert client.max_retries == 5
    assert client.client.max_retries == 5


def test_anthropic_client_timeout_defaults(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "80.0")
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT", "20.0")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")

    client = AnthropicClient(api_key="test-key")
    assert client.timeout.read == 80.0
    assert client.timeout.connect == 20.0
    assert client.max_retries == 3
    assert client.client.max_retries == 3


def test_create_llm_client_factory(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-qwen-key")
    monkeypatch.setenv("LLM_TIMEOUT", "95.0")
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT", "35.0")

    client = create_llm_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.timeout.read == 95.0
    assert client.timeout.connect == 35.0


def test_json_response_parser():
    raw_json = '{"action": "BUY", "volume_lots": 0.05}'
    assert JSONResponseParser.parse(raw_json) == {"action": "BUY", "volume_lots": 0.05}

    markdown_json = '```json\n{"action": "SELL", "volume_lots": 0.1}\n```'
    assert JSONResponseParser.parse(markdown_json) == {"action": "SELL", "volume_lots": 0.1}

    embedded_json = 'Here is the decision: {"action": "HOLD", "confidence": 90.0} hope this helps.'
    assert JSONResponseParser.parse(embedded_json) == {"action": "HOLD", "confidence": 90.0}

    # Truncated or unclosed JSON recovery
    truncated_json = '{"action": "BUY", "volume_lots": 0.05, "reason": "Sweep detected'
    parsed_trunc = JSONResponseParser.parse(truncated_json)
    assert parsed_trunc["action"] == "BUY"
    assert parsed_trunc["volume_lots"] == 0.05


# --- thinking mode ------------------------------------------------------------------------
# qwen3.7-flash thinks by default. Measured on the VPS 2026-09-23 with the real prompts: a
# FlowRSI /trade call spent 1,254 of 1,378 output tokens on hidden reasoning (18.5 s vs 1.9 s
# with thinking off), a Judas call 3,194 of 3,369 (39.3 s vs 2.9 s) - and timed out 4/5 times.

def test_only_qwen_exposes_a_thinking_switch():
    qwen = create_llm_client("qwen")
    assert qwen.thinking_options(False) == {"extra_body": {"enable_thinking": False}}
    assert qwen.thinking_options(True) == {"extra_body": {"enable_thinking": True}}
    # OpenAI rejects unknown body fields, so providers without the switch send nothing
    assert create_llm_client("openai").thinking_options(False) == {}
    assert AnthropicClient(api_key="k").thinking_options(False) == {}


def _flowrsi_entry_snapshot():
    from app.server import MarketSnapshot
    return MarketSnapshot(
        bot_id="cbot-demo-demo-gbpusd-all-flowrsi", symbol="GBPUSD", timeframe="Minute15",
        bid=1.32747, ask=1.32750, account_number="12345", account_label="demo",
        account_balance=1850.0, account_margin=40.0, pip_size=0.0001,
        fast_rsi=38.2, slow_rsi=36.9, rsi_cross_signal="Bullish_Cross", is_discount=True,
        candidate_action="BUY", technical_sl_price=1.32599, technical_tp_price=1.32995,
        technical_risk_reward=1.6,
    )


async def _trade_kwargs(monkeypatch, tmp_path):
    import json
    from unittest.mock import AsyncMock
    import app.server as server_mod
    import app.news_service as news_mod
    from app.portfolio import PortfolioManager

    client = OpenAICompatibleClient(api_key="k", base_url="https://example.invalid/v1", thinking_switch=True)
    client.chat = AsyncMock(return_value=json.dumps({"action": "HOLD", "confidence": 60, "reason": "x"}))
    monkeypatch.setattr(server_mod, "llm_client", client)
    monkeypatch.setattr(server_mod, "portfolio_manager", PortfolioManager(db_path=str(tmp_path / "t.db")))
    monkeypatch.setattr(news_mod, "is_news_blackout_active", AsyncMock(return_value=(False, None, 0)))
    await server_mod.trade_decision(_flowrsi_entry_snapshot())
    assert client.chat.await_count == 1
    return client.chat.await_args.kwargs


@pytest.mark.anyio
async def test_trade_calls_switch_thinking_off_by_default(monkeypatch, tmp_path):
    kwargs = await _trade_kwargs(monkeypatch, tmp_path)
    assert kwargs.get("extra_body") == {"enable_thinking": False}


@pytest.mark.anyio
async def test_trade_thinking_can_be_switched_back_on(monkeypatch, tmp_path):
    import app.server as server_mod
    monkeypatch.setattr(server_mod, "TRADE_LLM_ENABLE_THINKING", True)
    kwargs = await _trade_kwargs(monkeypatch, tmp_path)
    assert kwargs.get("extra_body") == {"enable_thinking": True}
