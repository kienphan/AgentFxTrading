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
