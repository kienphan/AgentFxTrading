import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.server import app

client = TestClient(app)

def test_ui_endpoints():
    # Test Sessions API
    resp_sess = client.get("/api/dashboard/sessions")
    assert resp_sess.status_code == 200
    data_sess = resp_sess.json()
    assert "sessions" in data_sess
    assert "sydney" in data_sess["sessions"]
    assert "killzone" in data_sess
    assert "forex_status" in data_sess

    # Test Exposure API
    resp_exp = client.get("/api/dashboard/exposure?account_id=all")
    assert resp_exp.status_code == 200
    data_exp = resp_exp.json()
    assert "by_asset_class" in data_exp
    assert "total_volume" in data_exp

    # Test Cumulative PnL API
    resp_cum = client.get("/api/dashboard/cumulative-pnl?days=30&account_id=all")
    assert resp_cum.status_code == 200
    data_cum = resp_cum.json()
    assert isinstance(data_cum, list)

    # Test Latest Decisions API with filtering
    resp_dec = client.get("/api/dashboard/latest-decisions?limit=3")
    assert resp_dec.status_code == 200
    data_dec = resp_dec.json()
    assert isinstance(data_dec, list)

    resp_dec_filtered = client.get("/api/dashboard/latest-decisions?limit=3&symbol=EURUSD")
    assert resp_dec_filtered.status_code == 200
    assert isinstance(resp_dec_filtered.json(), list)
    # Test Summary API
    resp_summary = client.get("/api/dashboard/summary")
    assert resp_summary.status_code == 200
    data_sum = resp_summary.json()
    assert "profit_factor" in data_sum
    assert "win_rate" in data_sum
    assert "total_trades" in data_sum

    # Test Demo and Real Dashboard HTML renders cleanly with new widgets
    resp_demo = client.get("/demo/dashboard")
    assert resp_demo.status_code == 200
    html_demo = resp_demo.text
    assert "market-session-bar" in html_demo
    assert "pnl-chart" in html_demo
    assert "equity-chart" in html_demo
    assert "exposure-chart" in html_demo
    assert 'id="profit-factor"' in html_demo
    resp_real = client.get("/real/dashboard")
    assert resp_real.status_code == 200
    html_real = resp_real.text
    assert "market-session-bar" in html_real
    assert "view-decisions" in html_real
    assert "view-decisions" in html_demo
    assert "ai-feed-list" in html_real
    assert 'id="profit-factor"' in html_real
    assert "view-news" in html_demo
    # Ensure view-news is inside <main class="main-content"> before </main>
    main_close_idx = html_demo.find("</main>")
    news_view_idx = html_demo.find('id="view-news"')
    assert news_view_idx != -1 and news_view_idx < main_close_idx


def test_flowrsi_badge_renders_on_dashboard():
    """FlowRSI bots must render with their dedicated emerald 'FlowRSI' badge rather than falling back to TMS+ORB."""
    resp_demo = client.get("/demo/dashboard")
    assert resp_demo.status_code == 200
    # When a FlowRSI position exists or is rendered in HTML, it must carry bot-badge--flowrsi
    assert "bot-badge--flowrsi" in resp_demo.text
    assert "FlowRSI" in resp_demo.text

def _position():
    return {
        "bot_id": "uk100_m15",
        "symbol": "UK100",
        "side": "BUY",
        "volume": 0.1,
        "entry_price": 10727.6,
        "account_id": "live-6094347",
    }


def test_pnl_is_broker_reported_and_never_reconstructed():
    """Unrealized P&L comes from the broker only.

    The server cannot convert a price move into account currency (UK100 is GBP-denominated,
    cTrader reports pipSize 0.1 while a former per-symbol table used 1.0), so when the bot has
    not reported, the field must read as missing instead of being invented.
    """
    from app.dashboard import _attach_live_metrics

    # Broker report present -> passed through untouched, with its age
    pos = _position()
    _attach_live_metrics(pos, {"unrealized_pnl": -0.12, "unrealized_pnl_pips": -9.0, "_reported_at": time.time() - 30},
                         {"bid": 10726.7, "ask": 10727.7, "ts": time.time() - 30})
    assert pos["unrealized_pnl"] == -0.12
    assert pos["unrealized_pnl_pips"] == -9.0
    assert 25 <= pos["pnl_age_seconds"] <= 40
    assert 25 <= pos["price_age_seconds"] <= 40
    assert pos["current_price"] == 10726.7  # BUY marks to the bid

    # No broker report -> nothing invented, even though price and entry are both known
    pos = _position()
    _attach_live_metrics(pos, None, {"bid": 10726.7, "ask": 10727.7, "ts": time.time()})
    assert pos["unrealized_pnl"] is None
    assert pos["unrealized_pnl_pips"] is None
    assert pos["pnl_age_seconds"] is None

    # No quote yet -> falls back to the entry price and reports no age
    pos = _position()
    _attach_live_metrics(pos, None, None)
    assert pos["current_price"] == 10727.6
    assert pos["price_age_seconds"] is None


def test_tick_metrics_merge_into_the_cached_position_report():
    """A tick refreshes P&L without discarding the rest of the bot's position report."""
    from app.portfolio import PortfolioManager

    pm = PortfolioManager.__new__(PortfolioManager)          # no DB needed for the cache
    pm._bot_positions_cache = {"live-1:bot-a": {"side": "BUY", "entry_price": 10727.6,
                                                "unrealized_pnl": -0.12, "unrealized_pnl_pips": -9.0,
                                                "_reported_at": 1.0}}

    pm.update_position_metrics("bot-a", 1.24, 68.0, account_id="live-1")

    entry = pm._bot_positions_cache["live-1:bot-a"]
    assert entry["unrealized_pnl"] == 1.24
    assert entry["unrealized_pnl_pips"] == 68.0
    assert entry["_reported_at"] > 1.0
    # The snapshot's other fields survive: the dashboard still needs entry/SL/TP
    assert entry["entry_price"] == 10727.6
    assert entry["side"] == "BUY"
    # The bare bot_id key is written too, which is how a position row without an account resolves
    assert pm._bot_positions_cache["bot-a"]["unrealized_pnl"] == 1.24

    # A tick can arrive before the first snapshot (e.g. right after a restart)
    pm.update_position_metrics("bot-b", -0.5, -25.0, account_id="live-1")
    assert pm._bot_positions_cache["live-1:bot-b"]["unrealized_pnl_pips"] == -25.0

    # Keys registered by earlier snapshots are refreshed even without an account_id on the tick,
    # so the key the dashboard resolves first cannot stay behind a stale snapshot value
    pm2 = PortfolioManager.__new__(PortfolioManager)
    pm2._bot_positions_cache = {"live-9:bot-c": {"entry_price": 1.1, "unrealized_pnl": 0.0}}
    pm2.update_position_metrics("bot-c", 2.5, 10.0)
    assert pm2._bot_positions_cache["live-9:bot-c"]["unrealized_pnl"] == 2.5
    assert pm2._bot_positions_cache["live-9:bot-c"]["entry_price"] == 1.1


def test_setup_instances_panel_is_in_docker_view():
    html = client.get("/demo/dashboard").text
    docker_idx = html.find('id="view-docker"')
    logs_idx = html.find('id="view-logs"')
    panel_idx = html.find('id="setup-panel"')
    button_idx = html.find('id="setup-instances-btn"')
    assert docker_idx != -1 and panel_idx != -1 and button_idx != -1
    assert docker_idx < button_idx < panel_idx < logs_idx        # inside the Docker view, next to Add Bot
    assert "Setup Instances" in html
    for element_id in ("setup-account", "setup-new-account", "setup-acc-password", "setup-grid",
                       "setup-save-only", "setup-create-btn", "setup-results"):
        assert f'id="{element_id}"' in html, element_id
    assert 'id="setup-acc-password" type="password"' in html
    for endpoint in ("/api/setup/presets", "/api/setup/instances", "/api/setup/installed", "/api/ctrader-accounts"):
        assert endpoint in html, endpoint


def test_setup_grid_marks_cells_already_installed_for_an_account():
    html = client.get("/demo/dashboard").text
    # Each preset cell carries a note slot; the installed list fills it with the account label(s)
    # without re-rendering the grid (which would drop the user's ticks).
    assert 'class="setup-cell-note"' in html
    assert "function refreshSetupInstalled" in html
    # refreshed when the panel opens and again after "Create instances"
    assert html.count("await refreshSetupInstalled()") >= 2


def test_bot_action_buttons_show_pending_state_and_polls_do_not_overlap():
    html = client.get("/demo/dashboard").text
    # A docker stop/restart takes 7-10 s: the clicked row must say so instead of looking dead.
    for label in ("Starting…", "Stopping…", "Restarting…", "Deleting…"):
        assert label in html, label
    # A 10 s poll must never re-render over a pending row, nor stack up while a slow poll runs.
    assert "if (botsFetchInFlight || botActionInFlight) return;" in html


def test_position_pips_are_read_from_every_alias_a_cbot_sends():
    """The pip figure must survive whichever field name the reporting cBot uses.

    Only the bot can produce it (see the note in `_attach_live_metrics`), and the three cBots
    do not agree on a name: AiAgentBot sends `unrealized_pnl_pips`, while FlowRsiBot and
    AsianRangeJudasSweepBot use the same short names as their money field (`pnl` / `pnl_pips`).
    A missing alias used to fall through to the 0.0 default, so the dashboard printed the
    broker's dollars next to a flat "(0.0p)" for those two bots.
    """
    from app.server import PositionInfo

    ai_agent = PositionInfo(side="BUY", entry_price=1.1, unrealized_pnl=-4.68,
                            unrealized_pnl_pips=-9.0)
    assert ai_agent.resolved_pnl == -4.68
    assert ai_agent.resolved_pnl_pips == -9.0

    # FlowRsiBot / AsianRangeJudasSweepBot snapshot shape
    short = PositionInfo(type="Buy", entry_price=1.1, pnl=-4.68, pnl_pips=-9.0)
    assert short.resolved_pnl == -4.68
    assert short.resolved_pnl_pips == -9.0

    # `pips` is what the same bots already call it on the tick route
    assert PositionInfo(pnl=9.94, pips=51.0).resolved_pnl_pips == 51.0

    # A bot that reports no pips at all still reads as 0.0 rather than raising
    assert PositionInfo(pnl=9.94).resolved_pnl_pips == 0.0

    # A genuine zero is not mistaken for "absent" and replaced by a later alias
    assert PositionInfo(unrealized_pnl_pips=0.0, pnl_pips=-9.0).resolved_pnl_pips == 0.0


def test_active_positions_rows_have_a_close_button():
    html = client.get("/demo/dashboard").text
    assert "action-close-pos" in html
    assert "fetch(`/api/positions/${d.id}/close`" in html
    assert "fetch(`/api/bot-commands/${id}`)" in html
    assert 'colspan="11" class="empty-state">No active positions' in html
    assert 'colspan="10" class="empty-state">No active positions' not in html
    # delegated once on the tbody, so rows re-rendered by updateDashboard() keep working
    assert "document.getElementById('positions-table').addEventListener('click'" in html
    assert "did not pick up the command within 15 s" in html


def test_a_failed_close_re_enables_the_button_on_screen():
    """The table can be re-rendered while a close waits, drawing a new "Closing…" button:
    a failure must reset the button in the table now, not only the detached one clicked."""
    html = client.get("/demo/dashboard").text
    failure = html[html.index("async function closePosition(btn)"):]
    failure = failure[failure.index("} catch (err) {"):failure.index("} finally {")]
    assert "document.querySelectorAll(`#positions-table .action-close-pos[data-id=\"${d.id}\"]`)" in failure
    assert failure.index("closingPositions.delete(String(d.id));") < failure.index("querySelectorAll")


def test_bots_tab_has_pause_resume_and_close_and_stop():
    html = client.get("/demo/dashboard").text
    for snippet in ("action-pause", "action-resume", "action-close-stop", "Close &amp; Stop",
                    "Pausing…", "Resuming…", "Closing & stopping…", "/close-and-stop", ">PAUSED</span>"):
        assert snippet in html, snippet


def test_bots_tab_flags_running_containers_that_do_not_poll():
    html = client.get("/demo/dashboard").text
    assert "(isRunning && !b.polling)" in html
    assert ">NOT POLLING</span>" in html
    assert "${statusHtml}${pausedHtml}${notPollingHtml}" in html
