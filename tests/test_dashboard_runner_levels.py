"""Active Positions for a runner whose take-profit the bot has removed.

FlowRSI closes part of a winning position at break-even and, once it starts trailing the rest,
takes the TP off at the broker (RemoveTpOnTrailing). AUDJPY #675464477 on 2026-09-24: entry
111.021, entry TP 111.432, TP removed at 06:14 UTC, stop trailed to 111.322 with price at 111.58.
The page showed the entry-time TP from the DB row on first paint, a target price had already
passed without closing anything, and a bare "—" after the next tick. Neither said that the
position now exits only at its stop.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jinja2
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DASHBOARD = ROOT / "templates" / "dashboard.html"
NODE = shutil.which("node")


def _runner_report(**overrides):
    # A FlowRSI bar snapshot sends `tp = p.TakeProfit ?? 0.0`, stored as tp_price 0.0
    report = {"sl_price": 111.322, "tp_price": 0.0, "sl_pnl": 3.71, "tp_pnl": None,
              "unrealized_pnl": 6.89, "unrealized_pnl_pips": 55.3, "_reported_at": time.time()}
    report.update(overrides)
    return report


def _db_row():
    return {"side": "BUY", "entry_price": 111.021, "sl_price": 110.747, "tp_price": 111.432,
            "sl_pips": 27.4, "tp_pips": 40.7}


# --- server --------------------------------------------------------------------------------

@pytest.mark.parametrize("tp_value", [0.0, None])
def test_a_live_stop_without_a_target_drops_the_entry_time_tp(tp_value):
    from app.dashboard import _attach_live_metrics

    pos = _db_row()
    _attach_live_metrics(pos, _runner_report(tp_price=tp_value), None)
    assert pos["no_tp"] is True
    assert pos["tp_price"] is None
    assert pos["tp_pnl"] is None
    assert pos["sl_price"] == 111.322 and pos["sl_pnl"] == 3.71


def test_a_live_target_or_no_live_levels_keeps_the_tp():
    from app.dashboard import _attach_live_metrics

    pos = _db_row()
    _attach_live_metrics(pos, _runner_report(tp_price=111.432, tp_pnl=8.2), None)
    assert pos["no_tp"] is False
    assert pos["tp_price"] == 111.432

    # No report yet, or a bot build that sends no levels: nothing is known about the TP now
    for report in (None, {"unrealized_pnl": 1.0, "unrealized_pnl_pips": 2.0, "_reported_at": time.time()}):
        pos = _db_row()
        _attach_live_metrics(pos, report, None)
        assert pos["no_tp"] is False
        assert pos["tp_price"] == 111.432


def test_tick_levels_flag_a_position_without_a_target():
    from app.dashboard import tick_levels

    assert tick_levels({"sl_price": 111.322, "tp_price": None, "sl_pnl": 3.71, "tp_pnl": None})["no_tp"] is True
    assert tick_levels({"sl": 2490.0, "tp": 2520.0})["no_tp"] is False
    # AiAgentBot / Judas send sl and tp as null together when they have no P&L sample
    assert tick_levels({"sl": None, "tp": None})["no_tp"] is False


# --- first paint (Jinja macro) -------------------------------------------------------------

def _macro_source() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index("{% macro sltp_line")
    return html[start:html.index("{% endmacro %}", start) + len("{% endmacro %}")]


def _render_macro(call: str) -> str:
    env = jinja2.Environment(autoescape=True)
    return " ".join(env.from_string(_macro_source() + call).render().split())


def test_first_paint_says_the_runner_exits_at_its_stop():
    tp = _render_macro("{{ sltp_line('TP', none, none, 40.7, true) }}")
    assert "none · exits at SL" in tp
    assert "40.7p" not in tp

    sl = _render_macro("{{ sltp_line('SL', 111.322, 3.71, 27.4, true) }}")
    assert "111.322" in sl and "+$3.71" in sl
    assert ">trailing<" in sl


def test_first_paint_marks_a_stop_past_break_even_and_leaves_the_rest_alone():
    assert ">locked<" in _render_macro("{{ sltp_line('SL', 2500.5, 0.4, 100, false) }}")
    losing = _render_macro("{{ sltp_line('SL', 2490.0, -100.0, 100, false) }}")
    assert "locked" not in losing and "trailing" not in losing
    # Recent Trades passes no flag: a closed row without a TP price still shows its pips
    assert "40p" in _render_macro("{{ sltp_line('TP', none, none, 40) }}")


# --- live updates (JS renderer) ------------------------------------------------------------

def _function(src: str, name: str) -> str:
    """Source of `function name(...) {...}`, matched on braces."""
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


HARNESS = """
%(functions)s
console.log(JSON.stringify({
    // updateDashboard row: the server's position dict, entry-time pips still on it
    runner: sltpCellHtml({sl_price: 111.322, sl_pnl: 3.71, tp_price: null, tp_pnl: null,
                          sl_pips: 27.4, tp_pips: 40.7, no_tp: true}, true),
    // the tick handler's levels (tick_levels): no pips
    runner_tick: sltpCellHtml({sl_price: 111.322, sl_pnl: 3.71, tp_price: null, tp_pnl: null, no_tp: true}, true),
    break_even: sltpCellHtml({sl_price: 2500.5, sl_pnl: 0.4, tp_price: 2520, tp_pnl: 199, no_tp: false}, true),
    losing: sltpCellHtml({sl_price: 2490, sl_pnl: -100, tp_price: 2520, tp_pnl: 199, no_tp: false}, true),
    history: sltpCellHtml({sl_price: null, sl_pips: 20, tp_price: null, tp_pips: 40}, false),
}));
"""


@pytest.fixture(scope="module")
def cells(tmp_path_factory):
    html = DASHBOARD.read_text(encoding="utf-8")
    src = html[html.rindex("<script>") + len("<script>"):html.rindex("</script>")]
    js = HARNESS % {"functions": "\n".join(_function(src, n) for n in ("escapeHtml", "sltpLineHtml", "sltpCellHtml"))}
    path = tmp_path_factory.mktemp("sltp") / "harness.js"
    path.write_text(js, encoding="utf-8")
    run = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("case", ["runner", "runner_tick"])
def test_live_rows_say_the_runner_exits_at_its_stop(cells, case):
    cell = cells[case]
    assert "none · exits at SL" in cell
    assert "40.7p" not in cell
    assert "+$3.71" in cell and ">trailing<" in cell


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_live_rows_mark_a_stop_past_break_even_only(cells):
    assert ">locked<" in cells["break_even"]
    assert "none" not in cells["break_even"]
    assert "locked" not in cells["losing"] and "trailing" not in cells["losing"]
    assert "40p" in cells["history"] and "none" not in cells["history"]


def test_the_tick_handler_hands_the_levels_to_the_same_renderer():
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "sltpCellHtml(tick.levels, true)" in html
    assert "sltp_line('TP', pos.tp_price, pos.tp_pnl, pos.tp_pips, pos.no_tp)" in html
    assert "sltp_line('SL', pos.sl_price, pos.sl_pnl, pos.sl_pips, pos.no_tp)" in html
