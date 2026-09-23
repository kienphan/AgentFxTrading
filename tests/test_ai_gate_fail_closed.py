"""
Regression guard for C-01 (audit 2026-09-22): the AI gate must fail CLOSED.

When ``UseAiGateMode`` is on, the bot advertises that every entry is vetted by the
AI hub. Three paths used to drop straight into ``ExecuteTechnicalOrder`` when the
hub did not answer -- cooldown active, non-2xx HTTP, or an exception (60s
``HttpClient`` timeout / connection loss). So the moment the AI was least
trustworthy (e.g. 15 bots hammering ``/trade`` at M15 close, or
``systemctl restart agentfx.service`` landing on a bar close) was exactly when the
fleet traded the most, with zero AI supervision.

``AsianRangeJudasSweepBot`` -- named "reference pattern" in FlowRsiBot's own
comments -- fails closed: ``HandleAiFailure`` then ``return``.

The Python suite has no .NET toolchain, so these tests pin the source-level
contract: no AI-failure path may enter a trade unless the operator explicitly
opted in via ``AllowTechnicalFallbackOnAiFailure`` (default ``false``).
"""

import re
from pathlib import Path

import pytest

CS_PATH = Path(__file__).resolve().parent.parent / "cBot" / "FlowRsiBot.cs"
FALLBACK_PARAM = "AllowTechnicalFallbackOnAiFailure"


def _src() -> str:
    return CS_PATH.read_text(encoding="utf-8")


def _braced_block(src: str, marker: str) -> str:
    """Return the `{...}` block that opens right after `marker`, brace-matched."""
    start = src.index(marker)
    open_idx = src.index("{", start)
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise AssertionError(f"Unbalanced braces after marker {marker!r}")


# Each AI-failure path: (human name, marker that opens the branch)
FAILURE_BRANCHES = [
    ("cooldown", "if (DateTime.UtcNow < _aiCooldownUntil)"),
    ("http-error", "if (!response.IsSuccessStatusCode)"),
    ("exception", "catch (Exception ex)\n            {\n                string exErr = ex.InnerException"),
]


@pytest.mark.parametrize("name,marker", FAILURE_BRANCHES, ids=[b[0] for b in FAILURE_BRANCHES])
def test_ai_failure_branch_never_trades_without_explicit_opt_in(name, marker):
    """No AI-failure path may reach ExecuteTechnicalOrder unless gated by the opt-in flag."""
    block = _braced_block(_src(), marker)
    for call in re.finditer(r"ExecuteTechnicalOrder\(", block):
        preceding = block[: call.start()]
        assert FALLBACK_PARAM in preceding, (
            f"AI gate fails OPEN on the {name} path: ExecuteTechnicalOrder is reached "
            f"without a {FALLBACK_PARAM} guard, so the bot enters a trade with no AI "
            f"approval while 'AI Gate Mode = true' is displayed."
        )


@pytest.mark.parametrize("name,marker", FAILURE_BRANCHES, ids=[b[0] for b in FAILURE_BRANCHES])
def test_ai_failure_branch_logs_the_skipped_candidate(name, marker):
    """Fail-closed must be visible in the log, not a silent drop."""
    block = _braced_block(_src(), marker)
    assert "Print(" in block, f"{name} path swallows the AI failure without logging"


def test_http_and_exception_paths_still_register_the_failure():
    """HTTP/exception paths must keep feeding HandleAiFailure so the 3-strike cooldown arms."""
    src = _src()
    for name, marker in FAILURE_BRANCHES:
        if name == "cooldown":
            continue  # cooldown IS the result of HandleAiFailure; re-arming it would never expire
        block = _braced_block(src, marker)
        assert "HandleAiFailure(" in block, (
            f"{name} path no longer calls HandleAiFailure - the 3-strike safety cooldown "
            f"would never arm."
        )


def test_technical_fallback_is_an_explicit_parameter_defaulting_to_off():
    """Trading without AI must be a deliberate, visible setting - never a hidden default."""
    src = _src()
    match = re.search(
        r"\[Parameter\((?P<attr>[^\]]*)\)\]\s*\n\s*public bool " + FALLBACK_PARAM + r"\s*\{",
        src,
    )
    assert match, (
        f"Missing `[Parameter(...)] public bool {FALLBACK_PARAM}` - operators cannot see "
        f"or control whether the bot trades when the AI hub is down."
    )
    attr = match.group("attr")
    assert re.search(r"DefaultValue\s*=\s*false", attr), (
        f"{FALLBACK_PARAM} must default to false (fail closed), got: {attr}"
    )


def test_non_ai_mode_still_executes_technical_orders():
    """Guard against over-fixing: backtest / standalone mode legitimately trades without AI."""
    src = _src()
    block = _braced_block(src, "else if (!UseAiGateMode && !hasOpenPos &&")
    assert "ExecuteTechnicalOrder(" in block, (
        "The !UseAiGateMode direct-execution path was removed - backtesting and "
        "standalone (non-AI) mode can no longer place trades."
    )
