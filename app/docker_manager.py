import docker
import datetime
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
import os
import shlex
import subprocess
logger = logging.getLogger(__name__)

class CbotConfig(BaseModel):
    id: Optional[int] = None
    name: str
    description: Optional[str] = ""
    run_command: str

class DockerManager:
    # A reconnect loop is >=5 lost-connection lines *since the last successful reconnect* inside
    # RECONNECT_LOOP_WINDOW_SECONDS. A rejected login cycles every ~300 ms, so it crosses both bars
    # within seconds; isolated blips that each end with "restored" reset the count and can never
    # add up to a loop, even when the health check lands in the 2-4 s gap before a normal reconnect.
    RECONNECT_LOOP_MIN_LOSSES = 5
    RECONNECT_LOOP_WINDOW_SECONDS = 300

    def __init__(self):
        try:
            self.client = docker.from_env()
            self.is_available = True
        except Exception as e:
            logger.error(f"Docker is not available: {e}")
            self.is_available = False
            self.client = None

    def running_container_names(self) -> set:
        """Names of the containers Docker reports running right now.

        One `containers.list()` (running only) instead of an inspect per bot, so callers that
        only need "is it on?" (the leaderboard's running-bots filter) stay cheap on a poll.
        """
        if not self.is_available:
            return set()
        return {container.name for container in self.client.containers.list()}

    def get_container_status(self, name: str) -> Dict:
        if not self.is_available:
            return {"status": "error", "message": "Docker not available"}
        try:
            container = self.client.containers.get(name)
            return {
                "status": container.status, # running, exited, etc.
                "id": container.short_id,
                "created": container.attrs.get("Created"),
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
        except docker.errors.NotFound:
            return {"status": "not_found"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def start_container(self, name: str, command: str) -> Dict:
        if not self.is_available:
            return {"success": False, "message": "Docker not available"}
        
        # Check if already running
        status = self.get_container_status(name)
        if status.get("status") == "running":
            return {"success": False, "message": f"Container {name} is already running"}
            
        if status.get("status") in ["exited", "created"]:
            # Just start it
            try:
                container = self.client.containers.get(name)
                container.start()
                return {"success": True, "message": f"Container {name} started"}
            except Exception as e:
                 return {"success": False, "message": str(e)}

        # If not found, we need to execute the run command.
        # It's highly recommended the run command includes --name <name>
        try:
            project_root = str(Path(__file__).resolve().parent.parent)
            cleaned_cmd = command.replace("$(pwd)", project_root).replace("$PWD", project_root)
            
            # Normalize multiline backslashes and newlines
            cleaned_cmd = cleaned_cmd.replace("\\\r\n", " ").replace("\\\n", " ").replace("\\\r", " ").replace("\\", " ")
            cleaned_cmd = cleaned_cmd.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            cleaned_cmd = " ".join(cleaned_cmd.split())

            parts = [p for p in shlex.split(cleaned_cmd) if p.strip()]
            if "-d" not in parts and "--detach" not in parts:
                parts.insert(2, "-d") # docker run -d ...
            
            # check if --name is in parts
            has_name = False
            for p in parts:
                if p.startswith("--name"):
                    has_name = True
                    break
            
            if not has_name:
                parts.insert(2, f"--name={name}")
            
            # Execute
            result = subprocess.run(parts, capture_output=True, text=True)
            if result.returncode == 0:
                return {"success": True, "message": f"Container {name} created and started."}
            else:
                return {"success": False, "message": f"Error starting: {result.stderr}"}
                
        except Exception as e:
            return {"success": False, "message": str(e)}

    def stop_container(self, name: str) -> Dict:
        if not self.is_available:
            return {"success": False, "message": "Docker not available"}
        try:
            container = self.client.containers.get(name)
            container.stop()
            return {"success": True, "message": f"Container {name} stopped"}
        except docker.errors.NotFound:
            return {"success": False, "message": "Container not found"}
        except Exception as e:
            return {"success": False, "message": str(e)}
            
    def remove_container(self, name: str) -> Dict:
        if not self.is_available:
            return {"success": False, "message": "Docker not available"}
        try:
            container = self.client.containers.get(name)
            container.remove(force=True)
            return {"success": True, "message": f"Container {name} removed"}
        except docker.errors.NotFound:
            return {"success": True, "message": "Container not found"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def restart_container(self, name: str, timeout: int = 10) -> Dict:
        if not self.is_available:
            return {"success": False, "message": "Docker not available"}
        try:
            container = self.client.containers.get(name)
            container.restart(timeout=timeout)
            return {"success": True, "message": f"Container {name} restarted"}
        except docker.errors.NotFound:
            return {"success": False, "message": "Container not found"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_container_logs(self, name: str, tail: int = 50) -> Optional[str]:
        if not self.is_available:
            return None
        try:
            container = self.client.containers.get(name)
            logs = container.logs(tail=tail)
            return logs.decode("utf-8", errors="replace")
        except Exception:
            return None

    @staticmethod
    def _log_line_time(line: str) -> Optional[datetime.datetime]:
        """Timestamp of a cTrader log line, or None. Logs are UTC, so comparisons stay relative."""
        try:
            return datetime.datetime.strptime(line[:19], "%d/%m/%Y %H:%M:%S")
        except (TypeError, ValueError):
            return None

    def check_cbot_health(self, name: str) -> Dict:
        status_info = self.get_container_status(name)
        status = status_info.get("status", "unknown")
        if status != "running":
            return {
                "name": name,
                "status": status,
                "healthy": False,
                "stuck": False,
                "reason": f"Container is {status}"
            }

        # The tail still contains the previous process's "stopped" line for a few seconds after
        # a restart. Treating that as a fresh failure restarted USTEC twice on 2026-10-18.
        started_at = status_info.get("started_at")
        if started_at:
            try:
                started = datetime.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                age = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
                if age < 60:
                    return {
                        "name": name,
                        "status": status,
                        "healthy": True,
                        "stuck": False,
                        "reason": "Container recently started (warming up)",
                    }
            except (TypeError, ValueError):
                pass

        logs = self.get_container_logs(name, tail=40)
        if not logs:
            return {
                "name": name,
                "status": status,
                "healthy": True,
                "stuck": False,
                "reason": "Running (no logs available)"
            }

        lines = [line.strip() for line in logs.strip().splitlines() if line.strip()]
        all_retry_idx = -1
        logged_in_idx = -1
        stopped_idx = -1
        started_idx = -1
        for idx, l in enumerate(lines):
            l_low = l.lower()
            if "all login retry attempts failed" in l_low:
                all_retry_idx = idx
            if (
                "logged in" in l_low
                or ("cbot instance [" in l_low and "started" in l_low)
                or "aiagentbot started" in l_low
                or "asianrangejudassweepbot started" in l_low
                or "[orb] starting" in l_low
                or "starting cbot" in l_low
                or "flowrsi" in l_low
                or "[gotobi]" in l_low
                or "holybot" in l_low
            ):
                logged_in_idx = idx
                started_idx = idx
            if (
                "stopped by user" in l_low
                or ("cbot instance [" in l_low and "stopped" in l_low)
                or "cbot stopped itself" in l_low
                or "[orb] stopped on" in l_low
                or "[flowrsi] cbot stopped on" in l_low
            ):
                stopped_idx = idx

        # Check if cBot instance was stopped and not subsequently restarted
        if stopped_idx != -1 and started_idx <= stopped_idx:
            return {
                "name": name,
                "status": status,
                "healthy": False,
                "stuck": True,
                "reason": f"cBot instance stopped inside container ('{lines[stopped_idx]}')"
            }

        if all_retry_idx != -1 and logged_in_idx < all_retry_idx:
            return {
                "name": name,
                "status": status,
                "healthy": False,
                "stuck": True,
                "reason": "Login retries exhausted without re-authenticating ('All login retry attempts failed')"
            }
        # Check for persistent repeated login failure loop in recent lines without any healthy indicator
        recent_lines = lines[-15:]
        has_errors = any("connection error:" in l.lower() or "login failed" in l.lower() for l in recent_lines)
        has_healthy = any("logged in" in l.lower() or ("cbot instance [" in l.lower() and "started" in l.lower()) or "executing market order" in l.lower() or "reported position" in l.lower() or "flowrsi" in l.lower() or "[gotobi]" in l.lower() or "holybot" in l.lower() for l in recent_lines)
        if has_errors and not has_healthy and all_retry_idx != -1:
            return {
                "name": name,
                "status": status,
                "healthy": False,
                "stuck": True,
                "reason": "Continuous login error loop without successful recovery"
            }

        # cTrader CLI 5.10+ prints no "Login failed" when the cTID is rejected (e.g. "Password was
        # not set for this cTID"): it alternates lost/establishing ~3x per second forever. A short
        # outage looks the same but ends with "restored"/"established", so only losses accumulated
        # since the last successful reconnect count. Older losses spread over hours must not add up
        # to a loop either: the health check can run in the couple of seconds between a normal
        # "lost" and its "restored", and the old line-based count restarted bots for that (e.g.
        # cbot-live-gbpjpy-fix-gotobi at 2026-11-11 18:27 UTC).
        lost_times: List[Optional[datetime.datetime]] = []
        for l in lines:
            l_low = l.lower()
            if "the connection has been lost" in l_low:
                lost_times.append(self._log_line_time(l))
            elif (
                "connection has been established" in l_low
                or "connection has been restored" in l_low
                or "logged in" in l_low
            ):
                lost_times = []
        if lost_times:
            known = [t for t in lost_times if t is not None]
            if known:
                cutoff = max(known) - datetime.timedelta(seconds=self.RECONNECT_LOOP_WINDOW_SECONDS)
                recent = [t for t in lost_times if t is None or t >= cutoff]
            else:
                recent = lost_times
            if len(recent) >= self.RECONNECT_LOOP_MIN_LOSSES:
                return {
                    "name": name,
                    "status": status,
                    "healthy": False,
                    "stuck": True,
                    "reason": (
                        f"Reconnect loop: {len(recent)} 'connection has been lost' since the last "
                        f"successful reconnect without re-establishing"
                    )
                }

        return {
            "name": name,
            "status": status,
            "healthy": True,
            "stuck": False,
            "reason": "Healthy and running"
        }

docker_manager = DockerManager()
