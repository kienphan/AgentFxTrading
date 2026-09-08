import docker
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
    def __init__(self):
        try:
            self.client = docker.from_env()
            self.is_available = True
        except Exception as e:
            logger.error(f"Docker is not available: {e}")
            self.is_available = False
            self.client = None

    def get_container_status(self, name: str) -> Dict:
        if not self.is_available:
            return {"status": "error", "message": "Docker not available"}
        try:
            container = self.client.containers.get(name)
            return {
                "status": container.status, # running, exited, etc.
                "id": container.short_id,
                "created": container.attrs.get("Created")
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
        for idx, l in enumerate(lines):
            l_low = l.lower()
            if "all login retry attempts failed" in l_low or "connection failed, moving to reconnection state" in l_low:
                all_retry_idx = idx
            if "logged in" in l_low or "cbot instance [" in l_low or "aiagentbot started" in l_low or "asianrangejudassweepbot started" in l_low:
                logged_in_idx = idx

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
        has_healthy = any("logged in" in l.lower() or "cbot instance [" in l.lower() or "executing market order" in l.lower() or "reported position" in l.lower() for l in recent_lines)
        if has_errors and not has_healthy and all_retry_idx != -1:
            return {
                "name": name,
                "status": status,
                "healthy": False,
                "stuck": True,
                "reason": "Continuous login error loop without successful recovery"
            }

        return {
            "name": name,
            "status": status,
            "healthy": True,
            "stuck": False,
            "reason": "Healthy and running"
        }

docker_manager = DockerManager()
