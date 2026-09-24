"""
Container specs for the dashboard's Backtest page
(docs/superpowers/specs/2026-09-24-backtest-page-design.md).

Pure functions: parse an installed bot's `docker run … run <algo>` command into a BotSource, and build
the `docker run … backtest` spec for a job. Nothing here talks to Docker or the DB.

The cBots still make HTTP/WebSocket calls while backtesting (Judas' TickStream, AiAgentBot's position
reports). On `--network host` those reach 127.0.0.1:8000 and write fake trades and ticks into
production, so a backtest container only ever gets bridge networking, three mounts, and the locked
parameters below written after everything else. tests/test_backtest_command.py pins each rule.
"""
import re
import shlex
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from app.cbot_presets import DEFAULT_IMAGE

CACHE_VOLUME = "agentfx-bt-cache"
CACHE_DIR = "/bt-cache"
PWD_MOUNT = "/secrets/pwd"
ALGO_DIR = "/algo"
REPORT_PATH = "/tmp/report.json"
LABEL = "agentfx.backtest"
MEM_LIMIT = "1g"
NANO_CPUS = 2_000_000_000
CPU_SHARES = 256
DISABLED_URL = "http://127.0.0.1:9/disabled"

STRATEGY_BY_ALGO = {
    "FlowRsiBot.algo": "flowrsi",
    "AsianRangeJudasSweepBot.algo": "judas",
    "AiAgentBot.algo": "tms_orb",
}
SUPPORTED_STRATEGIES = {"flowrsi"}
UNSUPPORTED_REASON = "Judas/TMS+ORB enter only through the AI; backtest mode not implemented yet"
DATA_MODES = ("ticks", "m1")

# cTrader CLI options that sit next to the cBot parameters in a `run` command.
CLI_OPTIONS = {"ctid", "pwd-file", "password", "account", "broker", "symbol", "period", "reconnect-timeout"}

# Written after the bot's values and the user's overrides, so they always win.
LOCKED_PARAMS: Dict[str, Dict[str, str]] = {
    "flowrsi": {
        "UseAiGateMode": "false",
        "ApiUrl": DISABLED_URL,
        "AiTelemetryUrl": DISABLED_URL,
        "AiReportUrl": DISABLED_URL,
        "CommandPollMs": "0",
        "EnableNewsFilter": "false",          # there is no historical news calendar
        "EnableTelegramAlerts": "false",
        "SendChartScreenshot": "false",
        "SendAiAdjustAlerts": "false",
    },
}
# Never passed to a backtest container, whatever the bot's command says.
DROPPED_PARAMS = {"TelegramBotToken", "TelegramChatId"}
# Metadata groups the form does not show; their unlocked parameters keep the bot's value.
HIDDEN_GROUPS = {"AI Agent Integration", "News Filter", "Telegram Integration"}


class SourceError(ValueError):
    """A bot's run_command (or a stored job) cannot be used for a backtest."""


@dataclass
class BotSource:
    name: str
    algo: str                    # "FlowRsiBot.algo"
    strategy: Optional[str]      # "flowrsi" | "judas" | "tms_orb" | None
    ctid: str
    account: str
    symbol: str
    period: str
    pwd_file: str                # container path, as written in run_command
    params: Dict[str, str] = field(default_factory=dict)


def _tokens(run_command: str) -> List[str]:
    # Saved commands are often multi-line with trailing backslashes (see docker_manager.start_container).
    cleaned = run_command.replace("\\\r\n", " ").replace("\\\n", " ").replace("\\\r", " ")
    cleaned = cleaned.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    try:
        return shlex.split(cleaned)
    except ValueError as e:
        raise SourceError(f"run_command does not parse: {e}")


def parse_run_command(name: str, run_command: str) -> BotSource:
    tokens = _tokens(run_command or "")
    start = next((i for i in range(len(tokens) - 1)
                  if tokens[i] == "run" and tokens[i + 1].endswith(".algo")), None)
    if start is None:
        raise SourceError("run_command has no `run <file>.algo`")
    algo_path = PurePosixPath(tokens[start + 1])
    if algo_path.parent != PurePosixPath("/workspace/cBot"):
        raise SourceError("the algo must be /workspace/cBot/<file>.algo")
    options: Dict[str, str] = {}
    params: Dict[str, str] = {}
    for token in tokens[start + 2:]:
        if not token.startswith("--") or "=" not in token:
            continue                                   # --full-access, --exit-on-stop, -e
        key, value = token[2:].split("=", 1)
        if key in CLI_OPTIONS:
            options[key] = value
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            params[key] = value
    missing = [k for k in ("ctid", "pwd-file", "account", "symbol", "period") if not options.get(k)]
    if missing:
        raise SourceError(f"run_command lacks --{', --'.join(missing)}")
    return BotSource(name=name, algo=algo_path.name, strategy=STRATEGY_BY_ALGO.get(algo_path.name),
                     ctid=options["ctid"], account=options["account"], symbol=options["symbol"],
                     period=options["period"], pwd_file=options["pwd-file"], params=params)


def algo_host_path(algo: str, project_root: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+\.algo", algo or ""):
        raise SourceError(f"unexpected algo file name: {algo}")
    return Path(project_root) / "cBot" / algo


def pwd_host_path(pwd_file: str, ctrader_home: Path) -> Path:
    """`/root/ctrader_data/<file>` in the bot container -> `<CTRADER_HOME>/ctrader_data/<file>` on the host."""
    container = PurePosixPath(pwd_file or "")
    if container.parent != PurePosixPath("/root/ctrader_data") or container.name in ("", ".", ".."):
        raise SourceError("password file must be /root/ctrader_data/<file>")
    base = (Path(ctrader_home) / "ctrader_data").resolve()
    host = (base / container.name).resolve()
    if host.parent != base or not host.is_file():      # resolve() follows symlinks out of ctrader_data
        raise SourceError("password file not found")
    return host


def describe_source(name: str, run_command: str, project_root: Path, ctrader_home: Path) -> Dict:
    """One row of GET /api/backtests/sources."""
    row = {"name": name, "strategy": None, "symbol": "", "period": "", "account_label": "",
           "supported": False, "reason": ""}
    try:
        src = parse_run_command(name, run_command)
    except SourceError as e:
        row["reason"] = str(e)
        return row
    row.update(strategy=src.strategy, symbol=src.symbol, period=src.period,
               account_label=src.params.get("AccountLabel", ""))
    if src.strategy not in SUPPORTED_STRATEGIES:
        row["reason"] = UNSUPPORTED_REASON
        return row
    try:
        pwd_host_path(src.pwd_file, ctrader_home)
        if not algo_host_path(src.algo, project_root).is_file():
            raise SourceError(f"{src.algo} is not built")
    except SourceError as e:
        row["reason"] = str(e)
        return row
    row["supported"] = True
    return row


def locked_params(strategy: str, job_id: Optional[int]) -> Dict[str, str]:
    bot_id = f"bt-{job_id}" if job_id is not None else "bt-<id>"
    return {"BotId": bot_id, "AccountLabel": "backtest", **LOCKED_PARAMS[strategy]}


def container_name(job_id: int) -> str:
    return f"bt-{int(job_id)}"


def _num(value) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _cli_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")      # --end is inclusive


def build_container_spec(job: Dict, project_root: Path, ctrader_home: Path) -> Dict:
    """kwargs for `client.containers.run(**spec)`. `job` is a backtest_jobs row with `params` decoded."""
    strategy = job["strategy"]
    if strategy not in SUPPORTED_STRATEGIES:
        raise SourceError(UNSUPPORTED_REASON)
    if job["data_mode"] not in DATA_MODES:
        raise SourceError(f"unknown data mode {job['data_mode']}")
    host_algo = algo_host_path(job["algo"], project_root)
    host_pwd = pwd_host_path(job["pwd_file"], ctrader_home)
    algo_mount = f"{ALGO_DIR}/{job['algo']}"
    locked = locked_params(strategy, job["id"])

    command = [
        "backtest", algo_mount,
        f"--ctid={job['ctid']}", f"--pwd-file={PWD_MOUNT}", f"--account={job['account']}",
        f"--symbol={job['symbol']}", f"--period={job['period']}",
        f"--start={_cli_date(job['start_date'])}", f"--end={_cli_date(job['end_date'])}",
        f"--data-mode={job['data_mode']}",
    ]
    if job["data_mode"] == "m1":
        command.append(f"--spread={_num(job['spread_pips'])}")
    command += [f"--data-dir={CACHE_DIR}", f"--balance={_num(job['balance'])}", "--commission-auto",
                f"--report-json={REPORT_PATH}", "--full-access", "--exit-on-stop"]
    command += [f"--{k}={v}" for k, v in (job.get("params") or {}).items()
                if k not in locked and k not in DROPPED_PARAMS]
    command += [f"--{k}={v}" for k, v in locked.items()]

    return {
        "image": DEFAULT_IMAGE,
        "name": container_name(job["id"]),
        "command": command,
        "labels": {LABEL: str(job["id"])},
        "network_mode": "bridge",
        "mem_limit": MEM_LIMIT,
        "nano_cpus": NANO_CPUS,
        "cpu_shares": CPU_SHARES,
        "volumes": {
            str(host_algo): {"bind": algo_mount, "mode": "ro"},
            str(host_pwd): {"bind": PWD_MOUNT, "mode": "ro"},
            CACHE_VOLUME: {"bind": CACHE_DIR, "mode": "rw"},
        },
        "detach": True,
        "auto_remove": False,          # the report is read out of the exited container
    }
