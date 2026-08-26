import docker
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
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
            # We use subprocess to run the arbitrary command because parsing all docker run flags 
            # (volumes, envs, network) into docker-py is too complex.
            # We just append -d if not present.
            parts = shlex.split(command)
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

docker_manager = DockerManager()
