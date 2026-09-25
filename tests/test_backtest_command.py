"""Backtest container specs: an installed bot's run_command as a backtest source, and the safety rules
of the `docker run … backtest` spec (docs/superpowers/specs/2026-09-24-backtest-page-design.md).

The cBots still call HTTP/WebSocket endpoints while backtesting (Judas' TickStream, AiAgentBot's
position reports), so these tests pin that a backtest container never shares the host network, never
sees more than its three mounts, and always runs with the AI, news and Telegram switched off."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_command as bc  # noqa: E402
from app.cbot_presets import build_run_command  # noqa: E402

ACCOUNT = {"slug": "demo-demo", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd"}


def preset(strategy="flowrsi", symbol="EURUSD"):
    return build_run_command(ACCOUNT, strategy, symbol, "/home/forge/AgentFxTrading", "/home/forge/ctrader")


@pytest.fixture
def homes(tmp_path):
    project = tmp_path / "project"
    (project / "cBot").mkdir(parents=True)
    (project / "cBot" / "FlowRsiBot.algo").write_bytes(b"algo")
    ctrader = tmp_path / "ctrader"
    (ctrader / "ctrader_data").mkdir(parents=True)
    (ctrader / "ctrader_data" / "ctid_demo-demo_pwd").write_text("s3cret-password")
    return project, ctrader


def make_job(**over):
    job = {"id": 7, "strategy": "flowrsi", "algo": "FlowRsiBot.algo", "ctid_email": "trader@example.com",
           "account": "10115236", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd",
           "symbol": "EURUSD", "period": "m15", "start_date": "2026-07-24", "end_date": "2026-09-23",
           "data_mode": "ticks", "spread_pips": None, "balance": 10000.0,
           "params": {"FastRsiPeriod": "7", "TargetRiskReward": "2.0", "UseAiGateMode": "true",
                      "ApiUrl": "http://127.0.0.1:8000/trade", "BotId": "cbot-demo-demo-eurusd-all-flowrsi",
                      "TelegramBotToken": "123456:telegram-token"}}
    job.update(over)
    return job


def spec_for(homes, **over):
    project, ctrader = homes
    return bc.build_container_spec(make_job(**over), project, ctrader)


# --- parsing ------------------------------------------------------------------------------------

def test_parses_a_preset_flowrsi_command():
    src = bc.parse_run_command("cbot-demo-demo-eurusd-all-flowrsi", preset())
    assert src.algo == "FlowRsiBot.algo" and src.strategy == "flowrsi"
    assert (src.ctid, src.account, src.symbol, src.period) == ("trader@example.com", "10115236", "EURUSD", "m15")
    assert src.pwd_file == "/root/ctrader_data/ctid_demo-demo_pwd"
    assert src.params["BotId"] == "cbot-demo-demo-eurusd-all-flowrsi"      # quotes stripped
    assert src.params["TargetRiskReward"] == "1.5"
    assert src.params["UseAiGateMode"] == "true"
    assert "full-access" not in src.params and "ctid" not in src.params


def test_parses_a_multiline_hand_written_command():
    cmd = ("docker run -d --name cbot-x --network host \\\n  -v /home/forge/AgentFxTrading:/workspace \\\n"
           "  ghcr.io/spotware/ctrader-console:latest run /workspace/cBot/FlowRsiBot.algo \\\n"
           "  --ctid=trader@example.com --pwd-file=/root/ctrader_data/pw --account=1 \\\n"
           "  --symbol=XAUUSD --period=m15 --full-access --TargetRiskReward=2.5 -e")
    src = bc.parse_run_command("cbot-x", cmd)
    assert src.symbol == "XAUUSD" and src.params == {"TargetRiskReward": "2.5"}


@pytest.mark.parametrize("cmd, message", [
    ("docker run -d ghcr.io/spotware/ctrader-console:latest accounts", "run <file>.algo"),
    ("docker run img run /workspace/cBot/FlowRsiBot.algo --pwd-file=/root/ctrader_data/pw --account=1 "
     "--symbol=EURUSD --period=m15", "--ctid"),
    ("docker run img run /root/elsewhere/FlowRsiBot.algo --ctid=a@b.c --pwd-file=/root/ctrader_data/pw "
     "--account=1 --symbol=EURUSD --period=m15", "/workspace/cBot"),
    ('docker run img run /workspace/cBot/FlowRsiBot.algo --ctid="unterminated', "does not parse"),
])
def test_rejects_commands_it_cannot_use(cmd, message):
    with pytest.raises(bc.SourceError, match=re.escape(message)):
        bc.parse_run_command("x", cmd)


# --- host paths ---------------------------------------------------------------------------------

def test_password_file_maps_into_ctrader_home(homes):
    _, ctrader = homes
    host = bc.pwd_host_path("/root/ctrader_data/ctid_demo-demo_pwd", ctrader)
    assert host == (ctrader / "ctrader_data" / "ctid_demo-demo_pwd").resolve()


@pytest.mark.parametrize("pwd_file", [
    "/root/ctrader_data/../.ssh/id_rsa", "/root/other/ctid_pwd", "/root/ctrader_data/", "ctid_pwd",
    "/root/ctrader_data/missing_pwd",
])
def test_password_file_cannot_leave_ctrader_data(homes, pwd_file):
    with pytest.raises(bc.SourceError):
        bc.pwd_host_path(pwd_file, homes[1])


def test_password_file_symlink_out_of_ctrader_data_is_refused(homes, tmp_path):
    _, ctrader = homes
    outside = tmp_path / "outside"
    outside.write_text("x")
    (ctrader / "ctrader_data" / "link_pwd").symlink_to(outside)
    with pytest.raises(bc.SourceError, match="not found"):
        bc.pwd_host_path("/root/ctrader_data/link_pwd", ctrader)


# --- describe_source ----------------------------------------------------------------------------

def test_describe_flowrsi_source_is_supported(homes):
    project, ctrader = homes
    row = bc.describe_source("cbot-demo-demo-eurusd-all-flowrsi", preset(), project, ctrader)
    assert row == {"name": "cbot-demo-demo-eurusd-all-flowrsi", "strategy": "flowrsi", "symbol": "EURUSD",
                   "period": "m15", "account_label": "demo", "supported": True, "reason": ""}


@pytest.mark.parametrize("strategy", ["judas", "tms_orb"])
def test_describe_ai_only_bots_as_unsupported(homes, strategy):
    row = bc.describe_source("x", preset(strategy), *homes)
    assert row["supported"] is False and row["reason"] == bc.UNSUPPORTED_REASON
    assert row["strategy"] == strategy


def test_describe_reports_missing_password_file_and_unbuilt_algo(homes):
    project, ctrader = homes
    (ctrader / "ctrader_data" / "ctid_demo-demo_pwd").unlink()
    assert bc.describe_source("x", preset(), project, ctrader)["reason"] == "password file not found"
    (ctrader / "ctrader_data" / "ctid_demo-demo_pwd").write_text("pw")
    (project / "cBot" / "FlowRsiBot.algo").unlink()
    assert bc.describe_source("x", preset(), project, ctrader)["reason"] == "FlowRsiBot.algo is not built"


def test_describe_unparsable_command():
    row = bc.describe_source("x", "echo hello", Path("/nonexistent"), Path("/nonexistent"))
    assert row["supported"] is False and "run <file>.algo" in row["reason"]


# --- safety invariants of build_container_spec (spec: "Safety invariants") ----------------------

def test_invariant_1_bridge_network_only(homes):
    spec = spec_for(homes)
    assert spec["network_mode"] == "bridge"
    assert set(spec) == {"image", "name", "command", "labels", "network_mode", "mem_limit", "nano_cpus",
                         "cpu_shares", "volumes", "detach", "auto_remove"}


def test_invariant_2_only_three_mounts(homes):
    project, ctrader = homes
    assert spec_for(homes)["volumes"] == {
        str(project / "cBot" / "FlowRsiBot.algo"): {"bind": "/algo/FlowRsiBot.algo", "mode": "ro"},
        str((ctrader / "ctrader_data" / "ctid_demo-demo_pwd").resolve()): {"bind": "/secrets/pwd", "mode": "ro"},
        "agentfx-bt-cache": {"bind": "/bt-cache", "mode": "rw"},
    }


def test_invariant_3_locked_params_come_last_and_win(homes):
    command = spec_for(homes)["command"]
    locked = bc.locked_params("flowrsi", 7)
    assert command[-len(locked):] == [f"--{k}={v}" for k, v in locked.items()]
    assert "--UseAiGateMode=true" not in command
    assert "--ApiUrl=http://127.0.0.1:8000/trade" not in command
    assert "--BotId=cbot-demo-demo-eurusd-all-flowrsi" not in command
    assert "--BotId=bt-7" in command and "--AccountLabel=backtest" in command
    assert "--TargetRiskReward=2.0" in command and "--FastRsiPeriod=7" in command


def test_invariant_4_name_and_label(homes):
    spec = spec_for(homes)
    assert spec["name"] == "bt-7" and spec["labels"] == {"agentfx.backtest": "7"}


def test_invariant_5_resource_limits(homes):
    spec = spec_for(homes)
    assert (spec["mem_limit"], spec["nano_cpus"], spec["cpu_shares"]) == ("1g", 2_000_000_000, 256)
    assert spec["detach"] is True and spec["auto_remove"] is False


def test_invariant_6_list_command_without_secrets(homes):
    command = spec_for(homes)["command"]
    assert isinstance(command, list) and all(isinstance(a, str) for a in command)
    assert command[:2] == ["backtest", "/algo/FlowRsiBot.algo"]
    assert "--pwd-file=/secrets/pwd" in command
    joined = " ".join(command)
    assert "s3cret-password" not in joined and "telegram-token" not in joined


def test_dates_balance_and_report_flags(homes):
    command = spec_for(homes)["command"]
    for flag in ("--start=24/07/2026", "--end=23/09/2026", "--data-mode=ticks", "--data-dir=/bt-cache",
                 "--balance=10000", "--commission-auto", "--report-json=/tmp/report.json", "--full-access",
                 "--exit-on-stop", "--symbol=EURUSD", "--period=m15", "--account=10115236"):
        assert flag in command, flag
    assert not any(a.startswith("--spread=") for a in command)


def test_m1_adds_the_spread(homes):
    command = spec_for(homes, data_mode="m1", spread_pips=1.2)["command"]
    assert "--data-mode=m1" in command and "--spread=1.2" in command


@pytest.mark.parametrize("over", [{"strategy": "judas"}, {"data_mode": "tick-csv"}, {"algo": "../evil.algo"}])
def test_refuses_jobs_it_must_not_run(homes, over):
    with pytest.raises(bc.SourceError):
        spec_for(homes, **over)
