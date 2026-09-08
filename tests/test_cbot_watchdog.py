import sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

import pytest
import time
from unittest.mock import MagicMock, patch
from pathlib import Path
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

def test_check_cbot_health_not_running():
    dm = DockerManager()
    dm.is_available = True
    dm.get_container_status = MagicMock(return_value={"status": "exited", "id": "12345"})
    health = dm.check_cbot_health("cbot-usdjpy")
    assert health["healthy"] is False
    assert health["stuck"] is False

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
