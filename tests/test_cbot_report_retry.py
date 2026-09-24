"""
Regression guard for daily-check 2026-09-24 #1: a lost /portfolio/report put two trades off the books.

    14:15:16 VN  us30-all-flowrsi  [Portfolio Hub] Failed to report position open:
                 An error occurred while sending the request.
    14:45:37 VN  eurjpy-london     [Portfolio] Failed to report position open: ...

Each bot posted its reports once, fire-and-forget: no retry, no look at the HTTP status, and
only ex.Message in the log. The same day the /trade path of 7 FlowRSI bots logged 12
"Connection reset by peer", none near a restart: uvicorn closes an idle keep-alive connection
after 5 s while .NET keeps it pooled for a minute and can reuse it just as it closes.

So every bot now (1) retires pooled connections before uvicorn does, (2) retries a report on
a transport error or a non-2xx answer, which the server makes safe by ignoring a replayed
report, and (3) sends the position's side, size and entry on close, so the server can book a
trade whose open report was lost anyway.
"""

import re
from pathlib import Path

import pytest

CBOT = Path(__file__).resolve().parent.parent / "cBot"

REPORTS = {
    "FlowRsiBot.cs": ["ReportPositionOpen", "ReportPartialClose", "ReportPositionClosed"],
    "AiAgentBot.cs": ["ReportPositionOpen", "ReportPartialClose", "ReportPositionClosed"],
    "AsianRangeJudasSweepBot.cs": ["ReportPositionOpen", "ReportPositionClosed"],
}


def _src(bot: str) -> str:
    return (CBOT / bot).read_text(encoding="utf-8")


def _method_body(bot: str, name: str) -> str:
    src = _src(bot)
    m = re.search(r"private\s+(?:async\s+)?[\w<>]+\s+" + re.escape(name) + r"\s*\(", src)
    assert m, f"{bot}: method {name} not found"
    open_idx = src.index("{", m.end())
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise AssertionError(f"{bot}: unbalanced braces in {name}")


@pytest.mark.parametrize("bot", sorted(REPORTS))
def test_bot_has_a_retrying_report_poster(bot):
    body = _method_body(bot, "PostReportAsync")
    assert re.search(r"\bfor\s*\(", body), "PostReportAsync must retry in a loop"
    assert "Task.Delay(" in body, "retries need a backoff delay"
    assert "IsSuccessStatusCode" in body, "a non-2xx answer must count as a failure"
    assert "InnerException" in body, "log the inner exception: ex.Message alone hid the socket error"


@pytest.mark.parametrize("bot,method", [(b, m) for b, ms in sorted(REPORTS.items()) for m in ms])
def test_reports_go_through_the_retrying_poster(bot, method):
    body = _method_body(bot, method)
    assert "PostReportAsync(" in body, f"{bot} {method} does not use PostReportAsync"
    assert "_httpClient.PostAsync(" not in body, f"{bot} {method} still posts once, fire-and-forget"
    assert "Failed to report" in body, "keep the failure text the daily audit greps for"


@pytest.mark.parametrize("bot", sorted(REPORTS))
def test_pooled_connections_retire_before_uvicorns_keep_alive(bot):
    src = _src(bot)
    assert not re.search(r"_httpClient\s*=\s*new HttpClient\s*(\(\s*\)|\{)", src), (
        f"{bot}: the report HttpClient still uses the default one-minute pooled idle timeout"
    )
    m = re.search(r"PooledConnectionIdleTimeout\s*=\s*TimeSpan\.FromSeconds\((\d+(?:\.\d+)?)\)", src)
    assert m, f"{bot}: no PooledConnectionIdleTimeout on the report HttpClient"
    assert float(m.group(1)) < 5, "must be below uvicorn's 5 s --timeout-keep-alive"


@pytest.mark.parametrize("bot", ["AiAgentBot.cs", "AsianRangeJudasSweepBot.cs", "FlowRsiBot.cs"])
def test_close_report_carries_the_position(bot):
    body = _method_body(bot, "ReportPositionClosed")
    for field in ("side =", "volume =", "entry_price =", "entry_time ="):
        assert field in body, f"{bot} close report lacks {field.split()[0]}: the server cannot book a lost open"


@pytest.mark.parametrize("bot", ["AiAgentBot.cs", "FlowRsiBot.cs"])
def test_close_report_carries_the_initial_stop(bot):
    """The leaderboard scores by initial risk; a rebuilt row without sl_pips drops a bot to lot basis."""
    body = _method_body(bot, "ReportPositionClosed")
    assert "sl_pips =" in body
