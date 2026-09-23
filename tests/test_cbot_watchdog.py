import sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from pathlib import Path
from app import cbot_watchdog as wd
from app.cbot_watchdog import CbotWatchdog
from app.docker_manager import DockerManager

def test_check_cbot_health_stuck_log():
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "running", "id": "12345"})

    stuck_log = """
Login to 6094347...
Connection Error: Unknown
Login failed. Retrying in 4 seconds...
Login to 6094347...
Connection Error: Unknown
All login retry attempts failed, moving to reconnection state.
08/09/2026 01:00:02.969 | The connection has been lost. Reconnecting...
08/09/2026 01:00:02.969 | Establishing connection using senior1206@gmail.com...
08/09/2026 01:00:03.055 | The connection has been restored.
"""
    dm.get_container_logs = MagicMock(return_value=stuck_log)
    health = dm.check_cbot_health("cbot-usdjpy")
    assert health["healthy"] is False
    assert health["stuck"] is True
    assert "All login retry attempts failed" in health["reason"]

def test_check_cbot_health_healthy_log():
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "running", "id": "12345"})

    healthy_log = """
08/09/2026 01:44:21.919 | Establishing connection using senior1206@gmail.com...
08/09/2026 01:44:35.140 | The connection has been established.
Login to 6094347...
Logged in.
Starting cBot...
Info | CBot instance [AiAgentBot, USDJPY, m15] started.
"""
    dm.get_container_logs = MagicMock(return_value=healthy_log)
    health = dm.check_cbot_health("cbot-usdjpy")
    assert health["healthy"] is True
    assert health["stuck"] is False

def test_check_cbot_health_reconnect_loop_is_stuck():
    # CLI 5.10 prints no "Login failed" when the cTID is rejected (e.g. "Password was not
    # set for this cTID"): it just alternates lost/establishing ~3x per second forever.
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "running", "id": "12345"})

    loop_log = "| ShowLogs             | True                      | default value        |\n"
    loop_log += "---------------------------------------------------------------------------\n"
    for i in range(10):
        loop_log += f"21/09/2026 08:38:1{i}.522 | Establishing connection using someone@example.com...\n"
        loop_log += f"21/09/2026 08:38:1{i}.288 | The connection has been lost. Reconnecting...\n"
    dm.get_container_logs = MagicMock(return_value=loop_log)
    health = dm.check_cbot_health("cbot-demo-xauusd")
    assert health["healthy"] is False
    assert health["stuck"] is True
    assert "reconnect" in health["reason"].lower()

def test_check_cbot_health_transient_reconnect_then_restored_is_healthy():
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "running", "id": "12345"})

    blip_log = """
21/09/2026 10:11:44.306 | Info | AiAgentBot started | TF=Minute15 | Session=newyork
21/09/2026 12:00:01.000 | The connection has been lost. Reconnecting...
21/09/2026 12:00:01.001 | Establishing connection using someone@example.com...
21/09/2026 12:00:01.300 | The connection has been lost. Reconnecting...
21/09/2026 12:00:01.301 | Establishing connection using someone@example.com...
21/09/2026 12:00:01.600 | The connection has been lost. Reconnecting...
21/09/2026 12:00:01.601 | Establishing connection using someone@example.com...
21/09/2026 12:00:01.900 | The connection has been lost. Reconnecting...
21/09/2026 12:00:01.901 | Establishing connection using someone@example.com...
21/09/2026 12:00:02.200 | The connection has been lost. Reconnecting...
21/09/2026 12:00:02.201 | Establishing connection using someone@example.com...
21/09/2026 12:00:02.500 | The connection has been restored.
21/09/2026 12:00:03.000 | Info | [TickStream] Connected.
"""
    dm.get_container_logs = MagicMock(return_value=blip_log)
    health = dm.check_cbot_health("cbot-demo-xauusd")
    assert health["healthy"] is True
    assert health["stuck"] is False

def test_check_cbot_health_not_running():
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "exited", "id": "12345"})
    health = dm.check_cbot_health("cbot-usdjpy")
    assert health["healthy"] is False
    assert health["stuck"] is False

@patch("app.cbot_watchdog.docker_manager")
@patch("app.cbot_watchdog.get_portfolio_manager")
def test_check_and_heal_caches_health_for_the_dashboard(mock_get_pm, mock_dm):
    mock_dm.is_available = True
    mock_pm = MagicMock()
    mock_pm.get_cbot_configs.return_value = [{"name": "cbot-a"}, {"name": "cbot-b"}]
    mock_get_pm.return_value = mock_pm
    healthy = {"status": "running", "healthy": True, "stuck": False, "reason": "Healthy and running"}
    exited = {"status": "exited", "healthy": False, "stuck": False, "reason": "Container is exited"}
    mock_dm.check_cbot_health.side_effect = lambda name: healthy if name == "cbot-a" else exited

    watchdog = CbotWatchdog()
    assert watchdog.last_health("cbot-a") is None          # nothing before the first cycle
    watchdog.check_and_heal()
    assert watchdog.last_health("cbot-a") == healthy
    assert watchdog.last_health("cbot-b") == exited

    # A bot whose config was deleted drops out on the next cycle.
    mock_pm.get_cbot_configs.return_value = [{"name": "cbot-a"}]
    watchdog.check_and_heal()
    assert watchdog.last_health("cbot-b") is None

def test_watchdog_cooldown_and_backoff():
    watchdog = CbotWatchdog(
        cooldown_seconds=10,
        max_restarts_in_window=3,
        window_seconds=60,
        backoff_cooldown_seconds=30
    )

    can_restart, _ = watchdog._can_restart("test-bot")
    assert can_restart is True

    # 1st restart
    watchdog._record_restart("test-bot", "reason 1", True, "ok")
    can_restart, msg = watchdog._can_restart("test-bot")
    assert can_restart is False
    assert "Cooldown active" in msg

    # Simulate cooldown passing
    watchdog._restart_history["test-bot"][-1] -= 15
    can_restart, _ = watchdog._can_restart("test-bot")
    assert can_restart is True

    # 2nd and 3rd restarts
    watchdog._record_restart("test-bot", "reason 2", True, "ok")
    watchdog._restart_history["test-bot"][-1] -= 15
    watchdog._record_restart("test-bot", "reason 3", True, "ok")

    # Now max_restarts reached (3 in window) -> backoff triggers
    can_restart, msg = watchdog._can_restart("test-bot")
    assert can_restart is False
    assert "Backoff active" in msg

@patch("app.cbot_watchdog.docker_manager")
@patch("app.cbot_watchdog.get_portfolio_manager")
def test_watchdog_check_and_heal(mock_get_pm, mock_dm):
    mock_dm.is_available = True
    mock_pm = MagicMock()
    mock_pm.get_cbot_configs.return_value = [{"name": "cbot-usdjpy"}]
    mock_get_pm.return_value = mock_pm

    # Case 1: Bot is stuck -> triggers restart
    mock_dm.check_cbot_health.return_value = {
        "status": "running",
        "stuck": True,
        "reason": "All login retry attempts failed"
    }
    mock_dm.restart_container.return_value = {"success": True, "message": "restarted"}

    watchdog = CbotWatchdog(cooldown_seconds=100)
    actions = watchdog.check_and_heal()

    assert len(actions) == 1
    assert actions[0]["name"] == "cbot-usdjpy"
    assert actions[0]["success"] is True
    mock_dm.restart_container.assert_called_once_with("cbot-usdjpy", timeout=15)

    # Case 2: Immediate next check -> blocked by cooldown
    actions2 = watchdog.check_and_heal()
    assert len(actions2) == 0


# ---------------------------------------------------------------------------
# Stale bar feed: a running cBot that stopped pushing snapshots mid-session
# ---------------------------------------------------------------------------

USDJPY_RUN_COMMAND = (
    "docker run -d \\ --name cbot-usdjpy \\ --restart unless-stopped \\ --network host \\ "
    "-v /root/AgentFxTrading:/workspace \\ -v /root:/root \\ ghcr.io/spotware/ctrader-console:latest \\ "
    "run /workspace/cBot/AiAgentBot.algo \\ --ctid=senior1206@gmail.com \\ "
    "--pwd-file=/root/ctrader_data/ctid_pwd \\ --account=6094347 \\ --symbol=USDJPY \\ --period=m15 \\ "
    "--full-access \\ --BotId=\"usdjpy_m15\" \\ --ApiUrl=\"http://127.0.0.1:8000/trade\" \\ "
    "--AccountLabel=\"live\" \\ --SessionName=\"tokyo\" \\ --OrbStartHour=0 \\ --SessionEndHour=9 \\ "
    "--SessionDstRule=\"None\" \\ --MinDecisiveBreakoutPips=4.0"
)

LONDON_RUN_COMMAND = (
    "docker run -d \\ --name cbot-gbpusd \\ --network host \\ run /workspace/cBot/AiAgentBot.algo \\ "
    "--BotId=\"gbpusd_m15\" \\ --SessionName=\"london\" \\ --OrbStartHour=8 \\ --SessionEndHour=17 \\ "
    "--SessionDstRule=\"Europe\""
)

JUDAS_RUN_COMMAND = (
    "docker run -d \\ --name cbot-gbpusd-judas \\ --network host \\ "
    "run /workspace/cBot/AsianRangeJudasSweepBot.algo \\ --BotId=\"cbot-gbpusd-judas\" \\ "
    "--DashboardServerUrl=http://127.0.0.1:8000"
)


@pytest.fixture(autouse=True)
def _clean_snapshot_telemetry():
    wd.reset_snapshot_telemetry()
    yield
    wd.reset_snapshot_telemetry()


def test_parse_session_params_reads_tms_window():
    params = wd.parse_session_params(USDJPY_RUN_COMMAND)
    assert params["bot_id"] == "usdjpy_m15"
    assert params["session_name"] == "tokyo"
    assert (params["start_hour"], params["start_minute"], params["end_hour"]) == (0, 0, 9)
    assert params["end_minute"] == 0
    assert params["dst_rule"] == "None"

def test_parse_session_params_skips_bots_without_bar_cycle():
    assert wd.parse_session_params(JUDAS_RUN_COMMAND) is None
    assert wd.parse_session_params(None) is None
    assert wd.parse_session_params("") is None


def test_active_session_start_tokyo_no_dst():
    params = wd.parse_session_params(USDJPY_RUN_COMMAND)
    inside = datetime(2026, 9, 14, 1, 30, tzinfo=timezone.utc)
    assert wd.active_session_start(params, inside) == datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    assert wd.active_session_start(params, datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)) is None


def test_active_session_start_london_shifts_with_europe_dst():
    params = wd.parse_session_params(LONDON_RUN_COMMAND)
    # Summer (BST): the bot opens at 08:00 local == 07:00 UTC
    assert wd.active_session_start(params, datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc)) is None
    assert wd.active_session_start(params, datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
    # Winter (GMT): back to 08:00 UTC
    assert wd.active_session_start(params, datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)) is None
    assert wd.active_session_start(params, datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)


def test_active_session_start_honours_session_end_minute_and_fallback():
    params = wd.parse_session_params(USDJPY_RUN_COMMAND)
    # SessionEndHour == 0 falls back to a 9h window from the start (GetSessionInfo rule)
    fallback = dict(params, start_hour=0, end_hour=0, end_minute=0)
    assert wd.active_session_start(fallback, datetime(2026, 9, 14, 8, 59, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    assert wd.active_session_start(fallback, datetime(2026, 9, 14, 9, 1, tzinfo=timezone.utc)) is None
    # SessionEndMinute narrows the window
    narrowed = dict(params, end_minute=30)
    assert wd.active_session_start(narrowed, datetime(2026, 9, 14, 9, 29, tzinfo=timezone.utc)) is not None
    assert wd.active_session_start(narrowed, datetime(2026, 9, 14, 9, 30, tzinfo=timezone.utc)) is None


def test_is_forex_weekend_bounds():
    assert wd.is_forex_weekend(datetime(2026, 9, 11, 21, 30, tzinfo=timezone.utc)) is True   # Fri close
    assert wd.is_forex_weekend(datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)) is True    # Sat
    assert wd.is_forex_weekend(datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)) is True     # Sun pre-open
    assert wd.is_forex_weekend(datetime(2026, 9, 13, 21, 30, tzinfo=timezone.utc)) is False   # Sun open
    assert wd.is_forex_weekend(datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)) is False     # Mon


def _seed_snapshot(bot_id: str, when: datetime) -> None:
    wd._snapshot_seen_at[f"live-6094347/{bot_id}"] = when.timestamp()


def test_stale_feed_reason_flags_silent_bot_inside_its_session():
    now = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    _seed_snapshot("usdjpy_m15", now - timedelta(hours=1))
    watchdog = CbotWatchdog(stale_feed_seconds=2400)

    reason = watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, now)
    assert reason is not None
    assert "No bar snapshot for 60 min" in reason
    assert "'tokyo'" in reason


def test_stale_feed_reason_ignores_normal_and_out_of_session_silence():
    now = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    watchdog = CbotWatchdog(stale_feed_seconds=2400)

    # Reported one bar ago -> healthy
    _seed_snapshot("usdjpy_m15", now - timedelta(minutes=16))
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, now) is None

    # Silent for an hour, but outside the bot's own session -> expected
    _seed_snapshot("usdjpy_m15", now - timedelta(hours=1))
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND,
                                       datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)) is None

    # Weekend -> broker closed, silence is expected
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND,
                                       datetime(2026, 9, 12, 2, 0, tzinfo=timezone.utc)) is None


def test_stale_feed_reason_ignores_bots_that_never_reported():
    now = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    watchdog = CbotWatchdog(stale_feed_seconds=2400)
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, now) is None
    assert watchdog._stale_feed_reason("cbot-gbpusd-judas", JUDAS_RUN_COMMAND, now) is None


def test_stale_feed_heals_once_per_session_instance():
    now = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    _seed_snapshot("usdjpy_m15", now - timedelta(hours=1))
    watchdog = CbotWatchdog(stale_feed_seconds=2400)

    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, now) is not None
    # Same session -> latched, no restart loop while the restarted bot warms up
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, now) is None
    # Next day's Tokyo session -> eligible again
    tomorrow = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
    _seed_snapshot("usdjpy_m15", tomorrow - timedelta(hours=1))
    assert watchdog._stale_feed_reason("cbot-usdjpy", USDJPY_RUN_COMMAND, tomorrow) is not None


NY_INDEX_RUN_COMMAND = (
    "docker run -d \\ --name cbot-us30 \\ --network host \\ run /workspace/cBot/AiAgentBot.algo \\ "
    "--BotId=\"us30_m15\" \\ --SessionName=\"newyork_index\" \\ --OrbStartHour=14 \\ --OrbStartMinute=30 \\ "
    "--SessionEndHour=21 \\ --SessionDstRule=\"US\""
)

BAR_CYCLE_COMMANDS = {
    "cbot-usdjpy": USDJPY_RUN_COMMAND,        # FX, Tokyo window, no DST
    "cbot-gbpusd": LONDON_RUN_COMMAND,        # FX, London window, Europe DST
    "cbot-us30": NY_INDEX_RUN_COMMAND,        # index, NY window, US DST
}


def test_active_session_start_us_index_shifts_with_us_dst():
    params = wd.parse_session_params(NY_INDEX_RUN_COMMAND)
    assert params["start_minute"] == 30
    # Summer (EDT): 09:30 AM local == 13:30 UTC (base 14 - 1 = 13:30)
    assert wd.active_session_start(params, datetime(2026, 9, 14, 13, 29, tzinfo=timezone.utc)) is None
    assert wd.active_session_start(params, datetime(2026, 9, 14, 13, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 9, 14, 13, 30, tzinfo=timezone.utc)
    # Winter (EST): back to 14:30 UTC (09:30 AM local)
    assert wd.active_session_start(params, datetime(2026, 11, 16, 14, 29, tzinfo=timezone.utc)) is None
    assert wd.active_session_start(params, datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)

def test_stale_feed_never_fires_while_the_market_is_closed():
    """Saturday and Sunday carry no bars for FX or index bots, so a silent feed must
    never look like a stall — otherwise every weekend would trigger restarts."""
    start = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)   # Friday 00:00 UTC
    step = timedelta(minutes=30)
    probes = 0

    now = start
    while now < start + timedelta(days=4):
        for name, run_command in BAR_CYCLE_COMMANDS.items():
            params = wd.parse_session_params(run_command)
            _seed_snapshot(params["bot_id"], now - timedelta(hours=3))
            reason = CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(name, run_command, now)
            probes += 1
            if now.weekday() >= 5 or wd.is_forex_weekend(now):
                assert reason is None, f"{name} false-healed at {now.isoformat()}"
        now += step

    assert probes > 500

    # ...and the same sweep still catches a genuine mid-session stall on a weekday
    monday = datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    _seed_snapshot("usdjpy_m15", monday - timedelta(hours=3))
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-usdjpy", USDJPY_RUN_COMMAND, monday) is not None


@patch("app.cbot_watchdog.docker_manager")
@patch("app.cbot_watchdog.get_portfolio_manager")
def test_watchdog_heals_bot_with_stale_bar_feed(mock_get_pm, mock_dm):
    mock_dm.is_available = True
    mock_dm.check_cbot_health.return_value = {
        "status": "running", "stuck": False, "healthy": True, "reason": "Healthy and running"
    }
    mock_dm.restart_container.return_value = {"success": True, "message": "restarted"}

    mock_pm = MagicMock()
    mock_pm.get_cbot_configs.return_value = [
        {"name": "cbot-usdjpy", "run_command": USDJPY_RUN_COMMAND},
        {"name": "cbot-gbpusd-judas", "run_command": JUDAS_RUN_COMMAND},
    ]
    mock_get_pm.return_value = mock_pm

    wd._snapshot_seen_at["live-6094347/usdjpy_m15"] = time.time() - 3600

    watchdog = CbotWatchdog(stale_feed_seconds=2400)
    session_start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    with patch.object(wd, "is_forex_weekend", return_value=False), \
         patch.object(wd, "active_session_start", return_value=session_start):
        actions = watchdog.check_and_heal()

    assert len(actions) == 1
    assert actions[0]["name"] == "cbot-usdjpy"
    assert "No bar snapshot" in actions[0]["reason"]
    assert actions[0]["success"] is True
    mock_dm.restart_container.assert_called_once_with("cbot-usdjpy", timeout=15)


# ---------------------------------------------------------------------------
# All-day bots (FlowRSI, Judas): bar heartbeat carried on the tick stream
#
# They have no session window and call /trade only when a setup or a filter allows it
# (Judas sat silent 5-7 h a day, FlowRSI skips a bar on news, spread or a tripped circuit
# breaker), so /trade silence means nothing for them. Each tick frame carries the last
# bar the bot handled instead; a bot whose ticks keep flowing while that stops advancing
# has a stalled bar pipeline.
# ---------------------------------------------------------------------------

FLOWRSI_RUN_COMMAND = (
    "docker run -d \\ --name cbot-demo-demo-gbpusd-all-flowrsi \\ --network host \\ "
    "run /workspace/cBot/FlowRsiBot.algo \\ --BotId=\"cbot-demo-demo-gbpusd-all-flowrsi\" \\ "
    "--FastRsiPeriod=7 --SlowRsiPeriod=14"
)
FLOWRSI_BOT = "cbot-demo-demo-gbpusd-all-flowrsi"
NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def _stream(bot_id, last_bar, start, end, step=timedelta(minutes=2)):
    t = start
    while t <= end:
        bar = last_bar(t) if callable(last_bar) else last_bar
        wd.record_bot_tick(bot_id, bar, now=t.timestamp())
        t += step


def test_bar_stall_flags_all_day_bot_whose_ticks_flow_but_bars_stop():
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", NOW - timedelta(minutes=60), NOW)
    reason = CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-demo-demo-gbpusd-all-flowrsi", FLOWRSI_RUN_COMMAND, NOW)
    assert reason is not None
    assert "60 min" in reason and "2026-09-24T08:45:00" in reason


def test_bar_stall_ignores_healthy_bot_whose_bars_advance():
    quarter = lambda t: t.replace(minute=t.minute // 15 * 15, second=0).strftime("%Y-%m-%dT%H:%M:%S")
    _stream(FLOWRSI_BOT, quarter, NOW - timedelta(minutes=90), NOW)
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-demo-demo-gbpusd-all-flowrsi", FLOWRSI_RUN_COMMAND, NOW) is None


def test_bar_stall_ignores_a_closed_market():
    """No ticks means no market (daily index break, weekend): silence is expected."""
    _stream(FLOWRSI_BOT, "2026-09-24T08:00:00", NOW - timedelta(minutes=90), NOW - timedelta(minutes=10))
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-demo-demo-gbpusd-all-flowrsi", FLOWRSI_RUN_COMMAND, NOW) is None


def test_bar_stall_gives_the_first_bar_after_a_reopen_its_full_allowance():
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", NOW - timedelta(minutes=70), NOW - timedelta(minutes=60))
    # market shut for 40 min, ticks resume 20 min ago with no bar closed yet
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", NOW - timedelta(minutes=20), NOW)
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-demo-demo-gbpusd-all-flowrsi", FLOWRSI_RUN_COMMAND, NOW) is None


def test_bar_stall_ignores_bots_without_bar_telemetry():
    """An older build sends ticks without last_bar: nothing to judge by."""
    _stream(FLOWRSI_BOT, None, NOW - timedelta(minutes=60), NOW)
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-demo-demo-gbpusd-all-flowrsi", FLOWRSI_RUN_COMMAND, NOW) is None


def test_bar_stall_heals_once_per_stalled_bar():
    watchdog = CbotWatchdog(stale_feed_seconds=2400)
    name = "cbot-demo-demo-gbpusd-all-flowrsi"
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", NOW - timedelta(minutes=60), NOW)
    assert watchdog._stale_feed_reason(name, FLOWRSI_RUN_COMMAND, NOW) is not None
    # restarted bot still warming up on the same bar -> latched, no restart loop
    later = NOW + timedelta(minutes=10)
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", NOW, later)
    assert watchdog._stale_feed_reason(name, FLOWRSI_RUN_COMMAND, later) is None
    # it recovers, then stalls again on a new bar -> eligible again
    _stream(FLOWRSI_BOT, "2026-09-24T10:00:00", later, later + timedelta(minutes=50))
    assert watchdog._stale_feed_reason(name, FLOWRSI_RUN_COMMAND, later + timedelta(minutes=50)) is not None


def test_judas_bot_is_judged_by_its_bar_heartbeat():
    _stream("cbot-gbpusd-judas", "2026-09-24T08:45:00", NOW - timedelta(minutes=60), NOW)
    assert CbotWatchdog(stale_feed_seconds=2400)._stale_feed_reason(
        "cbot-gbpusd-judas", JUDAS_RUN_COMMAND, NOW) is not None


@patch("app.cbot_watchdog.docker_manager")
@patch("app.cbot_watchdog.get_portfolio_manager")
def test_watchdog_heals_all_day_bot_with_stalled_bars(mock_get_pm, mock_dm):
    mock_dm.is_available = True
    mock_dm.check_cbot_health.return_value = {
        "status": "running", "stuck": False, "healthy": True, "reason": "Healthy and running"
    }
    mock_dm.restart_container.return_value = {"success": True, "message": "restarted"}
    mock_pm = MagicMock()
    mock_pm.get_cbot_configs.return_value = [{"name": FLOWRSI_BOT, "run_command": FLOWRSI_RUN_COMMAND}]
    mock_get_pm.return_value = mock_pm

    now = datetime.now(timezone.utc)
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", now - timedelta(minutes=60), now)
    actions = CbotWatchdog(stale_feed_seconds=2400).check_and_heal()

    assert [a["name"] for a in actions] == [FLOWRSI_BOT]
    assert "no bar handled" in actions[0]["reason"]
    mock_dm.restart_container.assert_called_once_with(FLOWRSI_BOT, timeout=15)


def test_status_exposes_the_bar_heartbeat():
    now = datetime.now(timezone.utc)
    _stream(FLOWRSI_BOT, "2026-09-24T08:45:00", now - timedelta(minutes=4), now)
    beat = CbotWatchdog().get_status()["bar_telemetry"][FLOWRSI_BOT]
    assert beat["last_bar"] == "2026-09-24T08:45:00"
    assert 200 <= beat["bar_age"] <= 300
    assert beat["tick_age"] < 60
