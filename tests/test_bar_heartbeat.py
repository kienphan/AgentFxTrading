"""
Bar heartbeat for the all-day bots (FlowRSI, Judas) - 2026-09-23.

The watchdog's stale-feed check only covered bots with a session window (--OrbStartHour),
so the 15 FlowRSI and 15 Judas bots had no protection against the 2026-09-14 failure: a
bot that stays up and logged in but stops handling bars. Their /trade calls are no signal
(Judas calls only on a fresh sweep; FlowRSI skips the call on news, spread or a tripped
circuit breaker), so each tick frame now carries `last_bar`, the open time of the last bar
the bot handled, and the watchdog restarts a bot whose ticks flow while that stands still.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import cbot_watchdog as wd
from app.server import app

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


def _method(src: str, signature: str) -> str:
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
    raise AssertionError(f"Unbalanced braces in {signature}")


# --- server: both tick channels feed the heartbeat --------------------------------------

def test_api_tick_records_the_bar_heartbeat():
    wd.reset_snapshot_telemetry()
    r = client.post("/api/tick", json={
        "bot_id": "cbot-hb-eurusd-all-flowrsi", "account_number": "12345", "symbol": "EURUSD",
        "bid": 1.1400, "ask": 1.1401, "last_bar": "2026-09-24T08:45:00",
    })
    assert r.json()["status"] == "ok"
    assert wd._bar_seen["cbot-hb-eurusd-all-flowrsi"] == "2026-09-24T08:45:00"


def test_ws_tick_records_the_bar_heartbeat():
    wd.reset_snapshot_telemetry()
    with client.websocket_connect("/ws/cbot") as ws:
        ws.send_json({"type": "tick", "bot_id": "cbot-hb-gbpusd-judas", "symbol": "GBPUSD",
                      "bid": 1.3300, "ask": 1.3301, "last_bar": "2026-09-24T08:45:00"})
        assert ws.receive_json()["type"] == "ack"
    assert wd._bar_seen["cbot-hb-gbpusd-judas"] == "2026-09-24T08:45:00"


# --- FlowRSI ----------------------------------------------------------------------------

FLOWRSI = (ROOT / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")
JUDAS = (ROOT / "cBot" / "AsianRangeJudasSweepBot.cs").read_text(encoding="utf-8")


def test_bar_stamp_changes_on_every_bar():
    """
    Stamping Bars.LastBar.OpenTime repeated a value across two bars: inside OnBarClosed that
    is sometimes the closed bar and sometimes the new one (17:45 close on 2026-09-23: half the
    bots sent 17:30, half 17:45; 19 then sent 17:45 again at 18:00). The handling time cannot
    repeat.
    """
    for src in (FLOWRSI, JUDAS):
        body = _method(src, "private void MarkBarHandled()")
        assert "Server.Time" in body and "Bars.LastBar" not in body, body


def test_flowrsi_sends_last_bar_with_its_ticks():
    body = _method(FLOWRSI, "private void SendLiveTickTelemetry(")
    assert re.search(r"\blast_bar\s*=", body), "FlowRSI tick telemetry does not carry last_bar"


def test_flowrsi_marks_every_deliberately_handled_bar():
    """
    News, a tripped circuit breaker and a wide spread skip the bar on purpose - those count
    as handled, or the watchdog would restart a bot that is doing its job (and a restart
    re-arms a tripped circuit breaker).
    """
    body = _method(FLOWRSI, "protected override void OnBarClosed()")
    try_body = body[: body.index("catch")]
    returns = [m.start() for m in re.finditer(r"\breturn;", try_body)]
    assert len(returns) == 3, returns
    for idx in returns:
        preceding = try_body[max(0, idx - 80) : idx]
        assert "MarkBarHandled();" in preceding, f"early return without MarkBarHandled: ...{preceding}"
    evaluate = try_body.index("EvaluateStrategySignals(hasOpenPos);")
    assert "MarkBarHandled();" in try_body[evaluate:], "the evaluated bar is never marked handled"


# --- Judas ------------------------------------------------------------------------------

def test_judas_tick_frames_carry_last_bar():
    message = _method(JUDAS, "private class TickStreamMessage")
    assert "public string last_bar" in message
    loop = _method(JUDAS, "private async Task TickStreamLoopAsync(")
    assert re.search(r"\blast_bar\s*=", loop), "the tick frame is built without last_bar"


def test_judas_marks_each_handled_bar():
    body = _method(JUDAS, "protected override void OnBarClosed()")
    try_body = body[: body.rindex("catch (Exception")]
    expired = try_body.index("if (_isExpired)")
    assert "MarkBarHandled();" in try_body[expired : try_body.index("return;", expired)], (
        "an expired bot is idle on purpose; a restart cannot fix it"
    )
    tail = try_body[try_body.rindex("SendStateToAgentAsync") :]
    assert "MarkBarHandled();" in tail, "a fully evaluated bar is never marked handled"
