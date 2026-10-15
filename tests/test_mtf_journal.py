"""Forward test evidence: every M15 decision is journaled before the cBot can send anything."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import mtf_agent


class _FakeLlm:
    def __init__(self, answer):
        self.answer = answer

    async def chat(self, messages):
        return json.dumps(self.answer)


SELL_LIMIT = {
    "symbol": "XAUUSD",
    "market_analysis": {"D1": {"bias": "BEARISH"}, "H4": {"bias": "BEARISH"}},
    "decision": {"action": "SELL_LIMIT", "entry": 4284.0, "stop_loss": 4296.0, "take_profit": 4250.0, "confidence": 76},
    "reasoning": {"direction_reason": "H4 lower highs", "entry_reason": "retest of H1 supply", "main_risk": "close above 4296"},
}
WAIT = {"decision": {"action": "WAIT"}, "reasoning": {"main_risk": "timeframes conflict"}}

SNAPSHOT = {
    "symbol": "XAUUSD", "bid": 4266.0, "ask": 4266.4, "spread_pips": 4.0,
    "max_spread_pips": 40.0, "min_rr": 2.0, "bot_id": "mtf-m15",
}


def _client(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("MTF_JOURNAL_PATH", str(tmp_path / "mtf_journal.jsonl"))
    app = FastAPI()
    mtf_agent.register_mtf_routes(app, lambda: _FakeLlm(answer))
    return TestClient(app)


def _lines(tmp_path):
    path = tmp_path / "mtf_journal.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_approved_order_is_journaled_with_its_thesis_and_id(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, SELL_LIMIT).post("/trade/mtf", json=SNAPSHOT).json()
    assert body["approved"] is True
    assert body["client_order_id"].startswith("mtf-m15-XAUUSD-")
    [rec] = _lines(tmp_path)
    assert rec["event"] == "decision"
    assert rec["approved"] is True
    assert rec["client_order_id"] == body["client_order_id"]
    assert rec["intended"] == {"entry": 4284.0, "stop": 4296.0, "take_profit": 4250.0}
    assert rec["action"] == "SELL_LIMIT"
    assert "retest of H1 supply" in rec["thesis"]
    assert "close above 4296" in rec["invalidation"]
    assert rec["bias"]["D1"] == "BEARISH"


def test_wait_is_journaled_as_evidence_without_an_order_id(tmp_path, monkeypatch):
    body = _client(tmp_path, monkeypatch, WAIT).post("/trade/mtf", json=SNAPSHOT).json()
    assert body["approved"] is False
    assert body["client_order_id"] == ""
    [rec] = _lines(tmp_path)
    assert rec["approved"] is False
    assert rec["action"] == "WAIT"
    assert rec["gate_reason"] == "timeframes conflict"


def test_journal_only_appends(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, WAIT)
    client.post("/trade/mtf", json=SNAPSHOT)
    first = (tmp_path / "mtf_journal.jsonl").read_text(encoding="utf-8")
    client.post("/trade/mtf", json=SNAPSHOT)
    after = (tmp_path / "mtf_journal.jsonl").read_text(encoding="utf-8")
    assert after.startswith(first)
    assert len(_lines(tmp_path)) == 2


def test_cbot_close_event_is_appended(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, WAIT)
    close = {"event": "close", "client_order_id": "mtf-m15-XAUUSD-20260926T101500Z",
             "exit_reason": "stop_loss", "fill_entry": 4284.1, "fill_exit": 4296.2, "net": -50.3}
    assert client.post("/trade/mtf/event", json=close).json()["status"] == "ok"
    [rec] = _lines(tmp_path)
    assert rec["event"] == "close"
    assert rec["exit_reason"] == "stop_loss"
    assert "ts_utc" in rec


def test_unknown_event_is_refused_and_not_written(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, WAIT)
    resp = client.post("/trade/mtf/event", json={"event": "edit_past_trade"})
    assert resp.status_code == 400
    assert not (tmp_path / "mtf_journal.jsonl").exists()
