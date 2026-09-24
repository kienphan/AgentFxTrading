"""Externally sourced strings are HTML-escaped by the dashboard's JS renderers.

bot_id, symbol, side, the account label, prices, times and the AI decision fields all come from a
cBot's own POST payload and are stored as sent (`sanitize_bot_id` only strips quotes, and SQLite
keeps a string in a REAL column). News titles and figures come from the ForexFactory feed, the
assessment fields from the LLM, and the Docker panel's names and commands from bot configs. The
JS renderers draw all of these with innerHTML, so each value has to pass through `escapeHtml`:
a bot_id like `evil<img src=x onerror=alert(1)>` otherwise runs script in every dashboard
session. The Jinja-rendered first paint is autoescaped and is not covered here.
"""
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import news_service  # noqa: E402
DASHBOARD = ROOT / "templates" / "dashboard.html"
NODE = shutil.which("node")

HOSTILE = "evil<img src=x onerror=alert(1)>\"'"


def _script() -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    return html[html.rindex("<script>") + len("<script>"):html.rindex("</script>")]


def _function(src: str, name: str) -> str:
    """Source of `[async] function name(...) {...}`, matched on braces."""
    start = src.index(f"function {name}(")
    if src[:start].endswith("async "):
        start -= len("async ")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_bot_badge_escapes_the_bot_id():
    body = _function(_script(), "botBadge")
    assert "${b}" not in body
    assert "escapeHtml(b)" in body


RENDERERS = [
    "escapeHtml", "formatMoney", "pnlClass", "accountBadge", "sideChip", "pnlCellHtml",
    "sltpLineHtml", "sltpCellHtml", "tradeTimesHtml", "closeReasonHtml", "botBadge", "closeButtonHtml",
    "updateDashboard", "updateTradeHistory", "renderAiDecisionsFeed",
    "applyLbControls", "lbRankCell", "lbBotCells", "lbWinRateCell", "lbProfitFactorCell", "lbTradesCell",
    "lbScoreCell", "lbReturnDdCell", "lbEmptyRow", "renderLeaderboard",
    "renderBots", "updateNewsShieldStatus", "renderNewsClusters",
]

# A minimal DOM: every id resolves to a plain object whose innerHTML the test reads back.
HARNESS = """
const els = {};
const classList = {toggle() {}, add() {}, remove() {}, contains: () => false};
const document = {
    getElementById: id => (els[id] = els[id] || {innerHTML: '', textContent: '', className: '', style: {}, classList}),
    querySelectorAll: () => [],
};
const lucide = {createIcons() {}};
const window = {lucide};
let currentMode = 'demo';
let selectedAccountId = 'demo', historyPage = 1;
function fetchTradeHistory() {}
const closingPositions = new Set();
const LB_WEIGHT_HINTS = {usd: '', lot: ''};
let lbTab = 'usd', lbPeriod = 'all', lbData = null;
let rawAiDecisions = [];
const aiFeedList = document.getElementById('ai-feed-list');
const aiFeedCount = document.getElementById('ai-feed-count');
const aiSymbolFilter = {value: 'ALL'};
let currentNewsView = 'table', currentNewsCurrency = 'ALL', currentNewsRange = 'thisweek', _cachedClusters = [];
let shieldStatus = null;
const fetch = async () => ({json: async () => shieldStatus});
%(functions)s
const H = %(hostile)s;
const out = {};
(async () => {

updateDashboard({open_positions: 1, daily_pnl: 0, total_pnl: 0, win_rate: 0, trades_today: 0, loss_streak: 0},
    [{id: 7, ctrader_id: 11, bot_id: H, symbol: H, side: H, account_type: 'demo', account_label: H, volume: H,
      entry_price: H, current_price: H, sl_price: H, tp_price: H, sl_pnl: 1, tp_pnl: 2, entry_time: H}]);
out.positions = els['positions-table'].innerHTML;

updateTradeHistory({page: 1, total_pages: 1, items: [
    {bot_id: H, symbol: H, side: H, account_type: 'demo', account_label: H, volume: H, entry_price: H,
     exit_price: H, sl_price: H, tp_price: H, pnl: 1, close_reason: H, entry_time: H, exit_time: H, duration: H}]});
out.history = els['history-table'].innerHTML;

rawAiDecisions = [{action: H, symbol: H, timeframe: H, bot_id: H, reason: H, confidence: 70,
                   volume_lots: H, sl_pips: H, tp_pips: H, new_sl_price: H}];
renderAiDecisionsFeed();
out.ai_feed = aiFeedList.innerHTML;

rawAiDecisions = [];
aiSymbolFilter.value = H;
renderAiDecisionsFeed();
out.ai_feed_empty = aiFeedList.innerHTML;

const lbBot = {rank: 4, bot_id: H, bot_name: H, symbol_display: H, tier_badge: 'TIER_C',
    tier_label: 'Tier C', tier_color: '#fff', win_rate: 50, total_wins: 1, total_losses: 1, total_trades: 2,
    profit_factor: 1, total_pnl_usd: 1, closed_pnl_usd: 1, floating_pnl_usd: 0, composite_score: 40,
    payoff_ratio: 1.5, return_dd: 2, net_units: 3, max_dd_units: 1.5, basis: 'risk'};
renderLeaderboard({rankings: [lbBot], lot_neutral: {top_performer: null, rankings: [lbBot]}});
out.leaderboard = els['leaderboard-table-body'].innerHTML;
out.leaderboard_lot = els['leaderboard-lot-table-body'].innerHTML;

// A running, not-polling bot and a stuck, paused one, so both status branches and every button are drawn.
renderBots([
    {name: H, bot_id: H, account_type: 'demo', status: 'running', polling: false, last_poll_age_s: 30,
     open_positions: 1, description: H, run_command: H, container_id: 'abcdef1234567890'},
    {name: H, account_type: 'demo', status: H, stuck: true, paused: true, health_reason: H, description: H, run_command: H},
], true);
out.bots = els['bots-tbody'].innerHTML;

shieldStatus = {success: true, is_blackout: true, event_title: H, remaining_minutes: 12};
await updateNewsShieldStatus();
out.news_shield = els['news-shield-status-chip'].innerHTML;

const cluster = {id: 'd41d8cd98f00b204e9800998ecf8427e', is_past: false, diff_minutes: 90, currencies: [H],
    date_formatted_vn: H, time_formatted_vn: H, is_assessed: true,
    latest_assessment: {volatility_level: H, expected_pips_range: H},
    events: [{title: H, forecast: H, previous: H}]};
for (const view of ['table', 'grid']) {
    currentNewsView = view;
    renderNewsClusters([cluster]);
    out['news_' + view] = els['news-clusters-container'].innerHTML;
}
currentNewsCurrency = H;
renderNewsClusters([]);
out.news_empty = els['news-clusters-container'].innerHTML;

console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    src = _script()
    js = HARNESS % {
        "functions": "\n".join(_function(src, name) for name in RENDERERS),
        "hostile": json.dumps(HOSTILE),
    }
    path = tmp_path_factory.mktemp("xss") / "harness.js"
    path.write_text(js, encoding="utf-8")
    run = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("table", [
    "positions", "history", "ai_feed", "ai_feed_empty", "leaderboard", "leaderboard_lot",
    "bots", "news_shield", "news_table", "news_grid", "news_empty",
])
def test_js_renderers_escape_external_strings(rendered, table):
    out = rendered[table].lower()
    assert "&lt;img" in out, "the hostile value should reach the table, escaped"
    assert "<img" not in out


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("table, kinds", [
    ("bots", {"name", "desc", "cmd"}),
    ("positions", {"bot", "symbol", "side", "volume"}),
])
def test_buttons_hand_their_handlers_the_original_values(rendered, table, kinds):
    # The bot action buttons, the Edit form and the position Close confirm read these back
    # through dataset, which decodes the entities: escaping must not change what they see.
    attrs = re.findall(r'data-(%s)="([^"]*)"' % "|".join(kinds), rendered[table])
    assert {kind for kind, _ in attrs} == kinds
    assert all(html.unescape(value) == HOSTILE for _, value in attrs)


def test_news_cluster_ids_are_hex_whatever_the_feed_says():
    # renderNewsClusters leaves c.id raw inside inline onclick handlers and element ids, which
    # is only safe because the id is an MD5 hexdigest rather than anything taken from the feed.
    clusters = news_service.cluster_red_news([
        {"title": HOSTILE, "country": HOSTILE, "date": "2026-09-08T12:30:00Z", "impact": "High",
         "forecast": HOSTILE, "previous": HOSTILE},
    ])
    assert len(clusters) == 1
    assert re.fullmatch(r"[0-9a-f]{32}", clusters[0]["id"])
