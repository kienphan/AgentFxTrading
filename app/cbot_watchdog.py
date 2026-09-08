import asyncio
import datetime
import logging
import time
from typing import Dict, List, Optional, Any
from app.docker_manager import docker_manager
from app.portfolio import get_portfolio_manager

logger = logging.getLogger("AgentFxTrading.Watchdog")

class CbotWatchdog:
    """
    Automatic Health Monitor & Self-Healing Watchdog for cTrader cBot containers.
    Detects unrecovered broker disconnection and login failure loops,
    automatically restarting affected containers with rate-limiting and backoff.
    """

    def __init__(
        self,
        check_interval_seconds: int = 60,
        cooldown_seconds: int = 120,
        max_restarts_in_window: int = 3,
        window_seconds: int = 600,
        backoff_cooldown_seconds: int = 300
    ):
        self.check_interval_seconds = check_interval_seconds
        self.cooldown_seconds = cooldown_seconds
        self.max_restarts_in_window = max_restarts_in_window
        self.window_seconds = window_seconds
        self.backoff_cooldown_seconds = backoff_cooldown_seconds

        # Per-bot restart timestamps: bot_name -> list of float timestamps
        self._restart_history: Dict[str, List[float]] = {}
        # Recent healing events (max 50)
        self._recent_events: List[Dict[str, Any]] = []
        self._is_running = False
        self._last_check_time: Optional[float] = None

    def _now_gmt7_str(self) -> str:
        dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _can_restart(self, name: str) -> tuple[bool, str]:
        now = time.time()
        history = self._restart_history.get(name, [])
        # Prune older than window_seconds
        history = [t for t in history if now - t <= self.window_seconds]
        self._restart_history[name] = history

        if not history:
            return True, "OK"

        last_restart = history[-1]
        elapsed_since_last = now - last_restart

        if len(history) >= self.max_restarts_in_window:
            if elapsed_since_last < self.backoff_cooldown_seconds:
                remaining = int(self.backoff_cooldown_seconds - elapsed_since_last)
                return False, f"Backoff active ({len(history)} restarts in 10m). Wait {remaining}s"
        elif elapsed_since_last < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - elapsed_since_last)
            return False, f"Cooldown active. Wait {remaining}s"

        return True, "OK"

    def _record_restart(self, name: str, reason: str, success: bool, message: str):
        now = time.time()
        if name not in self._restart_history:
            self._restart_history[name] = []
        self._restart_history[name].append(now)

        event = {
            "timestamp": self._now_gmt7_str(),
            "bot_name": name,
            "reason": reason,
            "success": success,
            "message": message
        }
        self._recent_events.insert(0, event)
        if len(self._recent_events) > 50:
            self._recent_events.pop()

    def check_and_heal(self) -> List[Dict[str, Any]]:
        """
        Inspect all configured cBots and auto-heal any stuck containers.
        Returns list of actions taken.
        """
        self._last_check_time = time.time()
        actions = []

        if not docker_manager.is_available:
            return actions

        pm = get_portfolio_manager()
        configs = pm.get_cbot_configs()
        bot_names = [c["name"] for c in configs]

        for name in bot_names:
            try:
                health = docker_manager.check_cbot_health(name)
                # Only heal running containers that are stuck
                if health.get("status") == "running" and health.get("stuck"):
                    reason = health.get("reason", "Container stuck")
                    can_restart, limit_reason = self._can_restart(name)

                    if not can_restart:
                        logger.warning(
                            f"[CBOT WATCHDOG] Bot '{name}' is STUCK ({reason}) "
                            f"but restart rate-limited: {limit_reason}"
                        )
                        continue

                    logger.warning(
                        f"[CBOT WATCHDOG] Auto-healing bot '{name}' -> "
                        f"Reason: {reason}. Triggering restart..."
                    )

                    res = docker_manager.restart_container(name, timeout=15)
                    success = res.get("success", False)
                    msg = res.get("message", "")

                    if success:
                        logger.info(
                            f"[CBOT WATCHDOG] Bot '{name}' successfully restarted and recovered."
                        )
                    else:
                        logger.error(
                            f"[CBOT WATCHDOG] Failed to restart bot '{name}': {msg}"
                        )

                    self._record_restart(name, reason, success, msg)
                    actions.append({
                        "name": name,
                        "reason": reason,
                        "success": success,
                        "message": msg
                    })

            except Exception as e:
                logger.error(f"[CBOT WATCHDOG] Error checking bot '{name}': {e}")

        return actions

    async def run_loop(self):
        """Background monitoring loop."""
        self._is_running = True
        logger.info(
            f"[CBOT WATCHDOG] Started cBot Watchdog service "
            f"(interval={self.check_interval_seconds}s, cooldown={self.cooldown_seconds}s)"
        )

        while self._is_running:
            try:
                # Run inspection in thread to avoid blocking event loop on docker IO
                await asyncio.to_thread(self.check_and_heal)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CBOT WATCHDOG] Unexpected error in loop: {e}")

            try:
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break

        logger.info("[CBOT WATCHDOG] cBot Watchdog service stopped.")

    def stop(self):
        self._is_running = False

    def get_status(self) -> Dict[str, Any]:
        """Status summary for dashboard API."""
        return {
            "is_running": self._is_running,
            "check_interval_seconds": self.check_interval_seconds,
            "last_check": datetime.datetime.fromtimestamp(
                self._last_check_time, tz=datetime.timezone.utc
            ).astimezone(datetime.timezone(datetime.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S")
            if self._last_check_time else None,
            "recent_events": self._recent_events[:10],
            "total_healed_events": len(self._recent_events)
        }

cbot_watchdog = CbotWatchdog()
