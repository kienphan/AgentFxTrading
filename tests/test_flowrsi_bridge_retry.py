"""
Regression guard: FlowRSI dropped a /trade call on a connection error (2026-09-23).

Four times that day (US30 12:00, AUDJPY 15:00, AUDUSD 15:30, USDJPY 15:45 UTC) the POST to
/trade failed ~0.3 s after sending with "An error occurred while sending the request". The
server never logged the request, so it failed before any HTTP exchange -- most likely on a
pooled keep-alive socket the server had just closed. The bot logged only that outer message,
counted it as "AI query failed (1/3)" (a failure counter, not a retry) and dropped the bar:
the AUDJPY BUY candidate was skipped. It now retries once on a fresh request and logs the
inner exception, so the next occurrence names its cause.
"""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs").read_text(encoding="utf-8")


def _method(signature: str) -> str:
    start = SRC.index(signature)
    open_idx = SRC.index("{", start)
    depth = 0
    for i in range(open_idx, len(SRC)):
        if SRC[i] == "{":
            depth += 1
        elif SRC[i] == "}":
            depth -= 1
            if depth == 0:
                return SRC[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces in {signature}")


ASK = _method("private async Task AskAgentAsync(")


def test_trade_post_is_retried_once_on_a_connection_error():
    posts = [m.start() for m in re.finditer(r"PostAsync\(localTargetUrl", ASK)]
    assert len(posts) == 2, f"expected the /trade POST plus one retry, found {len(posts)}"
    catch = ASK.find("catch (HttpRequestException")
    assert posts[0] < catch < posts[1], "the retry must sit in a catch for HttpRequestException"


def test_retry_sends_a_fresh_request_body():
    """HttpClient may dispose the content of a failed send; reusing it throws."""
    assert len(re.findall(r"new StringContent\(jsonPayload", ASK)) == 2


def test_timeouts_are_not_retried():
    """A 60 s HttpClient timeout is a TaskCanceledException: retrying would double the wait."""
    catch_clause = re.search(r"catch \(([^)]*)\)", ASK[ASK.find("catch (HttpRequestException"):]).group(1)
    assert "TaskCanceled" not in catch_clause and catch_clause.startswith("HttpRequestException")


def test_bridge_errors_name_the_inner_exception():
    outer = ASK[ASK.rindex("catch (Exception ex)"):]
    assert "InnerException" in outer, "the bridge error log still drops the underlying cause"
    retry = ASK[ASK.find("catch (HttpRequestException"):]
    assert "InnerException" in retry[: retry.index("PostAsync(localTargetUrl")]
