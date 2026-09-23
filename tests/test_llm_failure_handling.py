"""
What /trade does when the LLM call fails (non-2xx, timeout, hang).

Before: the Agent Logs line was a bare ``str(e)`` with no status code, every fallback
reason said "LLM timeout" whatever actually happened, the fallback never reached the
dashboard's decision feed, and a hung provider could hold a cBot for 90 s x 4 tries --
longer than FlowRsiBot's own 60 s HTTP timeout, so the fallback arrived to nobody.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from app.llm_client import OpenAICompatibleClient, describe_llm_error
from app.server import MarketSnapshot, trade_decision


def _status_error(status: int, request_id: str = "req_abc123") -> openai.APIStatusError:
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx.Response(
        status, request=request, headers={"x-request-id": request_id},
        json={"error": {"message": "upstream exploded"}},
    )
    cls = {401: openai.AuthenticationError, 429: openai.RateLimitError}.get(status, openai.InternalServerError)
    return cls(f"Error code: {status} - upstream exploded", response=response, body=None)


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        request_id="req-fail-1",
        bot_id="cbot-demo-demo-ethusd-all-flowrsi",
        symbol="ETHUSD",
        timeframe="Minute15",
        ask=2746.25,
        bid=2743.29,
        pip_size=0.01,
        fast_rsi=49.14,
        slow_rsi=50.10,
        rsi_cross_signal="Bearish_Cross",
        is_premium=True,
        candidate_action="SELL",
        technical_sl_price=2772.80,
        technical_tp_price=2701.49,
        technical_risk_reward=1.5,
    )


def test_describe_names_the_http_status_and_request_id():
    desc = describe_llm_error(_status_error(500))
    assert desc.startswith("HTTP 500 InternalServerError")
    assert "req_id=req_abc123" in desc
    assert "upstream exploded" in desc

    assert describe_llm_error(_status_error(401)).startswith("HTTP 401 AuthenticationError")
    assert describe_llm_error(_status_error(429)).startswith("HTTP 429 RateLimitError")


def test_describe_separates_timeouts_from_other_failures():
    timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://llm.example"))
    assert describe_llm_error(timeout).startswith("timeout (APITimeoutError)")
    assert describe_llm_error(asyncio.TimeoutError()).startswith("timeout")
    assert describe_llm_error(ValueError("bad json")) == "ValueError: bad json"


@pytest.mark.anyio
async def test_non_2xx_falls_back_and_says_why(monkeypatch, caplog):
    import app.server as server_mod
    from app.dashboard import get_latest_ai_decisions

    monkeypatch.setattr(server_mod.llm_client, "chat", AsyncMock(side_effect=_status_error(503)))

    with caplog.at_level(logging.INFO):
        decision = await trade_decision(_snapshot())

    assert decision.action == "HOLD"
    assert "[SAFETY FALLBACK] LLM call failed (HTTP 503" in decision.reason
    assert "LLM timeout" not in decision.reason

    # The account-tagged line is the one Agent Logs keeps after its demo/live filter.
    error_lines = [r.getMessage() for r in caplog.records if "LLM Error" in r.getMessage()]
    assert error_lines and "/cbot-demo-demo-ethusd-all-flowrsi] LLM Error: HTTP 503" in error_lines[-1]

    latest = get_latest_ai_decisions(limit=1)[0]
    assert latest["request_id"] == "req-fail-1"
    assert latest["is_fallback"] is True


@pytest.mark.anyio
async def test_trade_passes_its_own_timeout_and_retry_budget(monkeypatch):
    import app.server as server_mod

    captured = {}

    async def _capture(messages, **kwargs):
        captured.update(kwargs)
        return json.dumps({"action": "HOLD", "confidence": 60.0, "reason": "no setup"})

    monkeypatch.setattr(server_mod.llm_client, "chat", _capture)
    await trade_decision(_snapshot())

    assert captured["timeout"] == server_mod.TRADE_LLM_TIMEOUT
    assert captured["max_retries"] == server_mod.TRADE_LLM_MAX_RETRIES
    # Must answer inside FlowRsiBot's 60 s HttpClient timeout.
    assert server_mod.TRADE_LLM_DEADLINE < 60


@pytest.mark.anyio
async def test_a_hung_provider_hits_the_deadline_and_falls_back(monkeypatch):
    import app.server as server_mod

    async def _hang(messages, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(server_mod.llm_client, "chat", _hang)
    monkeypatch.setattr(server_mod, "TRADE_LLM_DEADLINE", 0.05)

    decision = await trade_decision(_snapshot())

    assert decision.action == "HOLD"
    assert "LLM call failed (timeout" in decision.reason
    assert "/trade deadline" in decision.reason


@pytest.mark.anyio
async def test_openai_client_applies_a_per_call_retry_budget():
    client = OpenAICompatibleClient(api_key="sk-test", base_url="https://llm.example/v1", model="m")

    reply = MagicMock()
    reply.choices = [MagicMock(message=MagicMock(content="ok"))]
    scoped = MagicMock()
    scoped.chat.completions.create = AsyncMock(return_value=reply)
    client.client = MagicMock()
    client.client.with_options = MagicMock(return_value=scoped)

    out = await client.chat([{"role": "user", "content": "hi"}], max_retries=1, timeout=40.0)

    assert out == "ok"
    client.client.with_options.assert_called_once_with(max_retries=1)
    kwargs = scoped.chat.completions.create.call_args.kwargs
    assert "max_retries" not in kwargs
    assert kwargs["timeout"] == 40.0
