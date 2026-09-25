"""Backtest page: the dashboard wiring and the renderers in static/js/backtest.js.

Strings the renderers draw come from a bot's run_command (names, symbols), the cBot metadata (group
and parameter labels), the user (notes, parameter values) and a container's log (errors). They are all
drawn with innerHTML, so each one must pass through the dashboard's escapeHtml."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import app  # noqa: E402

client = TestClient(app)
JS = ROOT / "static" / "js" / "backtest.js"
DASHBOARD = ROOT / "templates" / "dashboard.html"
NODE = shutil.which("node")
HOSTILE = "evil<img src=x onerror=alert(1)>\"'"


def _function(src: str, name: str) -> str:
    """Source of `function name(...) {...}`, matched on braces (same as tests/test_dashboard_xss.py)."""
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_dashboard_has_the_backtest_nav_view_and_script():
    html = client.get("/demo/dashboard").text
    assert 'data-target="view-backtest"' in html and 'id="view-backtest"' in html
    assert '<script src="/static/js/backtest.js' in html and " defer></script>" in html
    for element_id in ("bt-form-panel", "bt-bot", "bt-start", "bt-end", "bt-data-mode", "bt-spread-wrap",
                       "bt-spread", "bt-balance", "bt-note", "bt-params", "bt-locked", "bt-run-btn", "bt-form-msg",
                       "bt-compare-btn", "bt-jobs-tbody", "bt-detail-panel", "bt-detail", "bt-compare-panel",
                       "bt-compare", "bt-compare-close"):
        assert f'id="{element_id}"' in html, element_id
    assert "Technical logic only" in html


def test_switch_view_opens_the_backtest_page():
    html = client.get("/demo/dashboard").text
    assert "targetId === 'view-backtest'" in html and "btOnShow();" in html


def test_backtest_js_is_served_and_uses_the_api():
    resp = client.get("/static/js/backtest.js")
    assert resp.status_code == 200 and "function btOnShow" in resp.text
    for route in ("/api/backtests/sources", "/params", "/api/backtests", "/cancel", "/report.json"):
        assert route in resp.text, route


def test_best_value_rules():
    src = JS.read_text(encoding="utf-8")
    assert "['max_equity_dd_pct', 'Max equity DD %', 'min', 'pct']" in src
    assert "['avg_loss', 'Avg loss (deal)', 'zero', 'money']" in src
    assert "['position_win_rate', 'Win rate % (positions)', 'max', 'num']" in src
    assert "['avg_position_loss', 'Avg loss (position)', 'zero', 'money']" in src
    assert "['full_loss_pct', 'Full-size losses %', 'min', 'num']" in src


def test_the_avg_win_loss_pair_may_wrap_in_its_card():
    # Two amounts do not fit one ninth of the KPI row; .kpi-value would clip the loss.
    css = (ROOT / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert ".kpi-value.bt-kpi-wrap{white-space:normal}" in css


def test_job_list_heads_position_columns():
    html = client.get("/demo/dashboard").text
    assert "<th>Win % (pos)</th>" in html and "<th>Positions</th>" in html


HARNESS = r"""
%(escape)s
%(js)s
const H = %(hostile)s;
const out = {};
out.sources = btSourceOptionsHtml([{name: H, supported: false, reason: H}, {name: H, supported: true, reason: ''}]);
const view = {timeframes: ['Hour', H], groups: [{name: H, params: [
    {key: H, label: H, type: 'String', bot_value: H, default: H, help: H},
    {key: 'SlMode', label: H, type: 'Enum', bot_value: H, enum_values: [H, 'ATR_Multiplier']},
    {key: 'FastRsiPeriod', label: 'Fast', type: 'Integer', bot_value: H, min: H, max: 50},
    {key: 'MacroTimeFrame', label: 'TF', type: 'TimeFrame', bot_value: 'Hour'},
    {key: 'ShowLogs', label: 'Logs', type: 'Boolean', bot_value: 'true'}]}]};
const overrides = {FastRsiPeriod: H};
overrides[H] = H;
out.params = btParamGroupsHtml(view, overrides, new Set([H]));
out.locked = btLockedHtml({[H]: H});
out.help = btParamHelpHtml({key: H, label: H, help: H, default: H, min: H, max: H});
out.helpRange = [btParamHelpHtml({key: 'A', label: 'A', help: 'x', default: '1', min: 0.5, max: null}),
                 btParamHelpHtml({key: 'B', label: 'B', help: 'x', default: 'true', min: null, max: null})];
const job = {id: 3, bot_name: H, symbol: H, period: H, start_date: H, end_date: H, data_mode: H, note: H,
    status: 'done', overrides: {[H]: {from: H, to: H}}, algo_build_time: H,
    summary: {net_profit: H, profit_factor: 1.2, win_rate: 50, max_equity_dd_pct: 4, total_trades: 10,
              starting_capital: 10000},
    report: {trades: [{entry_time: H, close_time: H, direction: H, lots: H, entry_price: H, close_price: H,
                       pips: H, net: H, commissions: 0, swaps: 0}],
             parameters: {[H]: H, BotId: 'bt-3'}, equity: [], pnl_by_hour: [], pnl_by_weekday: []}};
out.rows = btJobRowsHtml([job, {...job, id: 4, status: 'failed', error: H},
                          {...job, id: 5, status: 'running', phase: H, progress: 42}], new Set([3]));
out.detail = btDetailHtml(job);
out.failed = btDetailHtml({...job, status: 'failed', error: H});
const other = {...job, id: 6, start_date: '2026-01-01',
               report: {...job.report, parameters: {[H]: 'other', BotId: 'bt-6'}}};
out.compare = btCompareHtml([job, other]);
out.diff = btParamDiff([job, other]);
out.warnings = btCompareWarnings([job, other]);
const stats = {net_profit: -3151.04, roi_pct: -31.5, profit_factor: 0.55, max_equity_dd_pct: 32.4,
               commissions: -342.62, swaps: -94.04, win_rate: 56.2, total_trades: 379, avg_win: 18.2,
               avg_loss: -42.33, positions: 270, position_win_rate: 42.22, avg_position_win: 33.84,
               avg_position_loss: -44.93, full_loss_pct: 57.78};
out.kpi = btKpiCardsHtml(stats);
out.kpiOld = btKpiCardsHtml({...stats, positions: undefined, position_win_rate: undefined,
                             avg_position_win: undefined, avg_position_loss: undefined, full_loss_pct: undefined});
out.rowNew = btJobRowsHtml([{...job, summary: stats}], new Set());
out.rowOld = btJobRowsHtml([{...job, summary: {win_rate: 56.2, total_trades: 379}}], new Set());
out.best = [btBestIndex([1, 3, 2], 'max'), btBestIndex([1, 3, 2], 'min'), btBestIndex([-5, -1, 2], 'zero'),
            btBestIndex([2, 2], 'max'), btBestIndex([1, 2], null)];
console.log(JSON.stringify(out));
"""


def _render() -> dict:
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    inline = dashboard[dashboard.rindex("<script>") + len("<script>"):dashboard.rindex("</script>")]
    src = HARNESS % {"escape": _function(inline, "escapeHtml"), "js": JS.read_text(encoding="utf-8"),
                     "hostile": json.dumps(HOSTILE)}
    result = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_backtest_renderers_escape_external_strings():
    out = _render()
    for name in ("sources", "params", "locked", "help", "rows", "detail", "failed", "compare"):
        assert "<img" not in out[name], name
        assert "evil&lt;img" in out[name], name
    assert out["params"].count('class="bt-help"') == 1         # only the parameter that has help
    assert "≥ 0.5" in out["helpRange"][0] and "Phạm vi" not in out["helpRange"][1]
    assert out["diff"] == [HOSTILE]                               # BotId is ignored
    assert out["warnings"] == ["date ranges differ"]
    assert out["best"] == [1, 0, 1, -1, -1]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_position_stats_lead_and_deal_stats_stay_labelled():
    out = _render()
    kpi = out["kpi"]
    assert "Win rate · positions" in kpi and "42.2%" in kpi and "56.2% of deals" in kpi
    assert ">270<" in kpi and "379 deals" in kpi
    assert "bt-kpi-wrap\">+$33.84 / −$44.93" in kpi and "per deal +$18.20 / −$42.33" in kpi
    assert "Full-size losses" in kpi and "57.8%" in kpi
    assert "—%" not in out["kpiOld"] and "56.2% of deals" in out["kpiOld"]
    assert "42.22<div" in out["rowNew"] and "56.20 deals" in out["rowNew"]
    assert "270<div" in out["rowNew"] and "379 deals" in out["rowNew"]
    assert "—<div" in out["rowOld"] and "56.20 deals" in out["rowOld"]   # saved before position stats
