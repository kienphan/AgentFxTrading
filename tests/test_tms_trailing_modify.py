"""
Regression guard: AiAgentBot's ATR trailing stop spammed the broker (2026-09-23).

``ManageExits`` computed the trailing stop unrounded (UK100 SELL: 10712.73643) and
compared it with ``pos.StopLoss``, which the broker holds rounded to ``Symbol.Digits``
(10712.74). 10712.73643 < 10712.74, so it "tightened" to the stop already in place, and
cTrader answered ``InvalidRequest`` -- 15 times on PID675016455 alone. It also chased
every tick: ~100 ModifyStopLossPrice calls in 20 minutes for 0.2-point steps. And the
"[Trailing Tier 2] tightened SL" line was printed whether the broker accepted or not.

No .NET toolchain here, so these tests pin the source-level contract and evaluate the
extracted guard conditions against the numbers from that log.
"""

import re
from pathlib import Path

import pytest

CS_PATH = Path(__file__).resolve().parent.parent / "cBot" / "AiAgentBot.cs"


def _src() -> str:
    return CS_PATH.read_text(encoding="utf-8")


def _trailing_block() -> str:
    """The body of the `if (TrailTriggerAtr > 0 && ...)` branch in ManageExits."""
    src = _src()
    start = src.index("if (TrailTriggerAtr > 0")
    open_idx = src.index("{", start)
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : i + 1]
    raise AssertionError("Unbalanced braces in the trailing branch")


def _condition_after(block: str, if_idx: int) -> str:
    open_idx = block.index("(", if_idx)
    depth = 0
    for i in range(open_idx, len(block)):
        if block[i] == "(":
            depth += 1
        elif block[i] == ")":
            depth -= 1
            if depth == 0:
                return block[open_idx + 1 : i]
    raise AssertionError("Unbalanced parentheses in if condition")


def _guards() -> list:
    """The two conditions that decide whether the stop moves (BUY first, then SELL)."""
    block = _trailing_block()
    conds = [
        _condition_after(block, m.start())
        for m in re.finditer(r"\bif\s*\(", block)
    ]
    guards = [c for c in conds if "trailSl" in c and "pos.StopLoss.Value" in c]
    assert len(guards) == 2, f"expected a BUY and a SELL trailing guard, found {guards}"
    return guards


def _moves(cond: str, *, trail: float, current: float, bid: float, ask: float, step: float) -> bool:
    py = cond.replace("pos.StopLoss.Value", "CURRENT").replace("trailSl", "TRAIL")
    py = py.replace("Symbol.Bid", "BID").replace("Symbol.Ask", "ASK").replace("minTrailStep", "STEP")
    py = py.replace("&&", " and ").replace("||", " or ")
    py = " ".join(py.split())
    return bool(
        eval(  # noqa: S307 - evaluating our own translated source under an empty builtins
            f"({py})",
            {"__builtins__": {}},
            {"TRAIL": trail, "CURRENT": current, "BID": bid, "ASK": ask, "STEP": step},
        )
    )


def test_candidate_stop_is_rounded_to_the_brokers_digits():
    """Unrounded, the candidate differs from the stored stop by a rounding residue."""
    block = _trailing_block()
    assignments = re.findall(r"trailSl\s*=\s*([^;]+);", block)
    assert len(assignments) == 2, assignments
    for rhs in assignments:
        assert rhs.strip().startswith("Math.Round(") and "Symbol.Digits" in rhs, (
            f"trailSl = {rhs.strip()} is compared with pos.StopLoss, which the broker "
            f"rounds to Symbol.Digits: the same stop gets re-sent and rejected."
        )


# SELL UK100 PID675016455 at 11:22 UTC: stop 10712.74, Ask ~10702, trail distance
# 105.4p (0.1 pip) so a tenth of it is 1.054 points.
SELL_CASES = [
    # (id, trail, current, step, should_move)
    ("same-stop-resent", 10712.74, 10712.74, 0.01, False),
    ("sub-step-tick-chase", 10712.55, 10712.74, 1.054, False),
    ("full-step", 10711.60, 10712.74, 1.054, True),
    ("widening", 10713.90, 10712.74, 1.054, False),
]


@pytest.mark.parametrize(
    "case_id,trail,current,step,should_move", SELL_CASES, ids=[c[0] for c in SELL_CASES]
)
def test_sell_trail_moves_only_on_a_real_step(case_id, trail, current, step, should_move):
    sell_guard = _guards()[1]
    moved = _moves(sell_guard, trail=trail, current=current, bid=10701.0, ask=10702.0, step=step)
    assert moved is should_move, f"[{case_id}] SELL guard `{sell_guard}` moved={moved}"


BUY_CASES = [
    ("same-stop-resent", 1.32740, 1.32740, 0.00001, False),
    ("sub-step-tick-chase", 1.32745, 1.32740, 0.00020, False),
    ("full-step", 1.32765, 1.32740, 0.00020, True),
    ("widening", 1.32700, 1.32740, 0.00020, False),
]


@pytest.mark.parametrize(
    "case_id,trail,current,step,should_move", BUY_CASES, ids=[c[0] for c in BUY_CASES]
)
def test_buy_trail_moves_only_on_a_real_step(case_id, trail, current, step, should_move):
    buy_guard = _guards()[0]
    moved = _moves(buy_guard, trail=trail, current=current, bid=1.33000, ask=1.33002, step=step)
    assert moved is should_move, f"[{case_id}] BUY guard `{buy_guard}` moved={moved}"


def test_min_step_never_drops_below_one_tick():
    """A zero step would let a rounded-equal stop through again."""
    assert re.search(r"minTrailStep\s*=\s*Math\.Max\(\s*Symbol\.TickSize\s*,", _trailing_block())


def test_tightened_is_logged_only_after_the_broker_accepts():
    src = _src()
    modify_idx = src.index("ModifyStopLossPrice(trailSl)")
    log_idx = src.index("[Trailing Tier 2]", modify_idx)
    assert "IsSuccessful" in src[modify_idx:log_idx], (
        "the trailing stop is reported as tightened without checking the TradeResult"
    )
