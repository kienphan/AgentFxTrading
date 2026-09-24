"""
cBot parameters for the Backtest page.

`ctrader-cli metadata <algo>` lists every parameter (group, type, default, min/max, enum values) but
takes ~15 s, so the schema is cached per .algo SHA-256 in memory and on disk; a rebuilt .algo gets a
fresh one. The form shows the bot's values over the defaults, and overrides are validated here and
kept in the text form the CLI takes: enums and time frames by name (--SlMode=ATR_Multiplier,
--MacroTimeFrame=Hour4), doubles as Python reprs.
"""
import hashlib
import json
import logging
import math
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from app.backtest_command import (ALGO_DIR, CPU_SHARES, DROPPED_PARAMS, HIDDEN_GROUPS, MEM_LIMIT, NANO_CPUS,
                                  BotSource, locked_params)
from app.cbot_presets import DEFAULT_IMAGE

logger = logging.getLogger(__name__)

TIMEFRAMES = {"Minute": 1, "Minute5": 5, "Minute15": 15, "Minute30": 30, "Hour": 60, "Hour4": 240, "Daily": 1440}
_TF_BY_MINUTES = {minutes: name for name, minutes in TIMEFRAMES.items()}
MAX_STRING = 200


class DockerUnavailable(RuntimeError):
    """No Docker daemon on this host (e.g. a developer Mac)."""


class MetadataError(RuntimeError):
    """`ctrader-cli metadata` failed or printed something that is not the schema."""


class OverrideError(ValueError):
    """A user override the backtest must not take. The message starts with the parameter name."""


def algo_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_metadata_output(text: str) -> Dict:
    # The CLI may echo its own command line before the JSON.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise MetadataError(f"metadata printed no JSON: {text.strip()[:300]}")
    try:
        meta = json.loads(text[start:end + 1])
    except ValueError as e:
        raise MetadataError(f"metadata JSON does not parse: {e}")
    if not isinstance(meta.get("Parameters"), list):
        raise MetadataError("metadata has no Parameters list")
    return meta


def docker_metadata_runner(algo_path: Path) -> str:
    from app.docker_manager import docker_manager
    if not docker_manager.is_available or docker_manager.client is None:
        raise DockerUnavailable("Docker is not available")
    mount = f"{ALGO_DIR}/{algo_path.name}"
    try:
        out = docker_manager.client.containers.run(
            DEFAULT_IMAGE, ["metadata", mount],
            volumes={str(algo_path): {"bind": mount, "mode": "ro"}},
            network_mode="bridge", remove=True, stdout=True, stderr=True,
            mem_limit=MEM_LIMIT, nano_cpus=NANO_CPUS, cpu_shares=CPU_SHARES)
    except Exception as e:
        raise MetadataError(f"metadata container failed: {e}")
    return out.decode("utf-8", errors="replace") if isinstance(out, bytes) else str(out)


class MetadataCache:
    def __init__(self, cache_dir: Path, runner: Callable[[Path], str] = docker_metadata_runner):
        self.cache_dir = Path(cache_dir)
        self.runner = runner
        self._mem: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def get(self, algo_path: Path) -> Dict:
        """The metadata of this .algo build, with its "sha256" added."""
        sha = algo_sha256(algo_path)
        with self._lock:
            if sha in self._mem:
                return self._mem[sha]
        file = self.cache_dir / f"{sha}.json"
        meta = None
        if file.is_file():
            try:
                meta = json.loads(file.read_text(encoding="utf-8"))
            except ValueError:
                logger.warning(f"Backtest metadata cache {file} is corrupt; fetching it again")
        if meta is None:
            meta = parse_metadata_output(self.runner(Path(algo_path)))
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(meta), encoding="utf-8")
        meta["sha256"] = sha
        with self._lock:
            self._mem[sha] = meta
        return meta


def _hidden(p: Dict, strategy: str) -> bool:
    key = p["PropertyName"]
    return p.get("GroupName") in HIDDEN_GROUPS or key in locked_params(strategy, None) or key in DROPPED_PARAMS


def canonical(p: Dict, raw) -> str:
    """A bot value, a JSON override or a metadata default -> the text the CLI takes."""
    kind, key = p["Type"], p["PropertyName"]
    if kind == "Boolean":
        if raw is True or raw in ("true", "True"):
            return "true"
        if raw is False or raw in ("false", "False"):
            return "false"
        raise OverrideError(f"{key}: expected true or false")
    if kind in ("Integer", "Double"):
        if isinstance(raw, bool):
            raise OverrideError(f"{key}: expected a number")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise OverrideError(f"{key}: expected a number")
        if not math.isfinite(number):
            raise OverrideError(f"{key}: expected a finite number")
        if kind == "Integer":
            if not number.is_integer():
                raise OverrideError(f"{key}: expected a whole number")
            number = int(number)
        low, high = p.get("MinValue"), p.get("MaxValue")
        if low is not None and number < low:
            raise OverrideError(f"{key}: below the minimum {low}")
        if high is not None and number > high:
            raise OverrideError(f"{key}: above the maximum {high}")
        return str(number) if kind == "Integer" else repr(number)
    if kind == "Enum":
        values = p.get("EnumValues") or {}
        if isinstance(raw, str) and raw in values:
            return raw
        if isinstance(raw, int) and not isinstance(raw, bool):
            for name, value in values.items():
                if value == raw:
                    return name
        raise OverrideError(f"{key}: expected one of {', '.join(values)}")
    if kind == "TimeFrame":
        if isinstance(raw, dict) and raw.get("Size") in _TF_BY_MINUTES:
            return _TF_BY_MINUTES[raw["Size"]]
        if isinstance(raw, str) and raw in TIMEFRAMES:
            return raw
        raise OverrideError(f"{key}: expected one of {', '.join(TIMEFRAMES)}")
    text = "" if raw is None else str(raw)
    if len(text) > MAX_STRING or any(c in text for c in "\r\n\x00"):
        raise OverrideError(f"{key}: at most {MAX_STRING} characters on one line")
    return text


def _shown(p: Dict, raw) -> Optional[str]:
    """canonical() for display: a value the schema does not accept is shown as written."""
    if raw is None:
        return None
    try:
        return canonical(p, raw)
    except OverrideError:
        return str(raw)


def _current(p: Dict, source: BotSource) -> Optional[str]:
    key = p["PropertyName"]
    return _shown(p, source.params[key]) if key in source.params else _shown(p, p.get("DefaultValue"))


def param_view(meta: Dict, source: BotSource) -> Dict:
    groups, by_name = [], {}
    for p in meta["Parameters"]:
        if _hidden(p, source.strategy):
            continue
        name = p.get("GroupName") or "Other"
        if name not in by_name:
            by_name[name] = {"name": name, "params": []}
            groups.append(by_name[name])
        by_name[name]["params"].append({
            "key": p["PropertyName"], "label": p.get("FriendlyName") or p["PropertyName"], "type": p["Type"],
            "default": _shown(p, p.get("DefaultValue")), "bot_value": _current(p, source),
            "min": p.get("MinValue"), "max": p.get("MaxValue"),
            "enum_values": list((p.get("EnumValues") or {}).keys()) if p["Type"] == "Enum" else None,
        })
    return {"groups": groups, "locked": locked_params(source.strategy, None),
            "timeframes": list(TIMEFRAMES), "algo_build_time": meta.get("BuildTime")}


def validate_overrides(meta: Dict, source: BotSource, overrides: Dict) -> Dict[str, Dict]:
    """{key: raw} from the client -> {key: {"from": bot value, "to": canonical value}}, no-ops dropped."""
    specs = {p["PropertyName"]: p for p in meta["Parameters"]}
    changes = {}
    for key, raw in (overrides or {}).items():
        p = specs.get(key)
        if p is None:
            raise OverrideError(f"{key}: not a parameter of {source.algo}")
        if _hidden(p, source.strategy):
            raise OverrideError(f"{key}: locked for backtests")
        value = canonical(p, raw)
        current = _current(p, source)
        if value != current:
            changes[key] = {"from": current, "to": value}
    return changes


def job_params(source: BotSource, overrides: Dict[str, Dict]) -> Dict[str, str]:
    """What the job stores in `params`: the bot's own values with the overrides applied. Locked keys
    are left out; build_container_spec appends them for the job's id."""
    locked = locked_params(source.strategy, None)
    params = {k: v for k, v in source.params.items() if k not in locked and k not in DROPPED_PARAMS}
    for key, change in overrides.items():
        params[key] = change["to"]
    return params
