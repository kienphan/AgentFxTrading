"""cBot parameters for the Backtest page: the cached `ctrader-cli metadata` schema, the form view
(bot values over defaults, integration groups hidden), and override validation.

The CLI takes enums and time frames by name (verified 2026-09-24: --SlMode=ATR_Multiplier and
--MacroTimeFrame=Hour4 show up as `cmd arg`), so every value is stored in that canonical text form."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_params as bp  # noqa: E402
from app.backtest_command import parse_run_command  # noqa: E402
from app.cbot_presets import build_run_command  # noqa: E402

META_TEXT = (ROOT / "tests" / "fixtures" / "backtest" / "flowrsi_metadata.json").read_text()
META = json.loads(META_TEXT)
ACCOUNT = {"slug": "demo-demo", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd"}


def source(**params):
    src = parse_run_command("cbot-demo-demo-eurusd-all-flowrsi",
                            build_run_command(ACCOUNT, "flowrsi", "EURUSD", "/w", "/r"))
    src.params.update(params)
    return src


# --- metadata output and cache ------------------------------------------------------------------

def test_parse_metadata_output_skips_the_command_echo():
    meta = bp.parse_metadata_output("dotnet metadata /algo/FlowRsiBot.algo\n" + META_TEXT + "\n")
    assert meta["Name"] == "FlowRsiBot" and len(meta["Parameters"]) == 23


@pytest.mark.parametrize("text", ["Error: Algo file is not specified", "{not json}", '{"Name": "x"}'])
def test_parse_metadata_output_rejects_garbage(text):
    with pytest.raises(bp.MetadataError):
        bp.parse_metadata_output(text)


def test_cache_runs_metadata_once_per_algo_build(tmp_path):
    algo = tmp_path / "FlowRsiBot.algo"
    algo.write_bytes(b"build-1")
    calls = []

    def runner(path):
        calls.append(path)
        return META_TEXT

    cache = bp.MetadataCache(tmp_path / "meta", runner)
    first = cache.get(algo)
    assert cache.get(algo) is first and len(calls) == 1
    assert first["sha256"] == bp.algo_sha256(algo)
    assert bp.MetadataCache(tmp_path / "meta", runner).get(algo)["Name"] == "FlowRsiBot"   # disk cache
    assert len(calls) == 1
    algo.write_bytes(b"build-2")                                                          # rebuilt .algo
    cache.get(algo)
    assert len(calls) == 2


# --- form view ----------------------------------------------------------------------------------

def test_param_view_hides_locked_and_integration_groups():
    view = bp.param_view(META, source())
    assert [g["name"] for g in view["groups"]] == [
        "General", "Nested RSI Engine", "SMC & ICT Market Structure", "Macro TMS Trend Filter", "Risk Management"]
    keys = {p["key"] for g in view["groups"] for p in g["params"]}
    assert keys == {"ShowLogs", "FastRsiPeriod", "RsiBullishCrossMin", "EnableSmcFilter", "MacroTimeFrame",
                    "RiskPercentage", "SlMode", "TargetRiskReward"}
    assert view["locked"]["UseAiGateMode"] == "false" and view["locked"]["BotId"] == "bt-<id>"
    assert view["timeframes"] == list(bp.TIMEFRAMES)
    assert view["algo_build_time"] == "2026-09-24T16:34:43.3439105Z"


def test_param_view_shows_bot_values_over_canonical_defaults():
    view = bp.param_view(META, source(TargetRiskReward="2.0"))
    params = {p["key"]: p for g in view["groups"] for p in g["params"]}
    assert (params["TargetRiskReward"]["default"], params["TargetRiskReward"]["bot_value"]) == ("1.5", "2.0")
    assert params["FastRsiPeriod"]["bot_value"] == "7"
    assert params["RsiBullishCrossMin"]["bot_value"] == "25.0"
    assert params["ShowLogs"]["bot_value"] == "true"
    assert params["SlMode"]["bot_value"] == "Technical_Swing"
    assert params["SlMode"]["enum_values"] == ["Technical_Swing", "ATR_Multiplier", "Fixed_Pips"]
    assert params["MacroTimeFrame"]["bot_value"] == "Hour"
    assert (params["FastRsiPeriod"]["min"], params["FastRsiPeriod"]["max"]) == (2, 50)
    assert params["TargetRiskReward"]["label"] == "Target Risk-to-Reward Ratio"


# --- overrides ----------------------------------------------------------------------------------

def test_overrides_are_canonical_and_no_ops_are_dropped():
    changes = bp.validate_overrides(META, source(), {
        "TargetRiskReward": 2, "FastRsiPeriod": "7", "SlMode": "ATR_Multiplier", "EnableSmcFilter": False,
        "MacroTimeFrame": "Hour4", "RsiBullishCrossMin": 25,
    })
    assert changes == {
        "TargetRiskReward": {"from": "1.5", "to": "2.0"},
        "SlMode": {"from": "Technical_Swing", "to": "ATR_Multiplier"},
        "EnableSmcFilter": {"from": "true", "to": "false"},
        "MacroTimeFrame": {"from": "Hour", "to": "Hour4"},
    }


@pytest.mark.parametrize("key, value, message", [
    ("UseAiGateMode", True, "locked"),
    ("ApiUrl", "http://evil", "locked"),
    ("AiConfidenceThreshold", 70, "locked"),
    ("TelegramBotToken", "x", "locked"),
    ("BotId", "x", "locked"),
    ("NoSuchParam", 1, "not a parameter"),
    ("FastRsiPeriod", 1, "minimum"),
    ("FastRsiPeriod", 7.5, "whole number"),
    ("FastRsiPeriod", True, "number"),
    ("RsiBullishCrossMin", 50, "maximum"),
    ("RiskPercentage", "abc", "number"),
    ("RiskPercentage", "inf", "number"),
    ("SlMode", "Nope", "one of"),
    ("MacroTimeFrame", "h1", "one of"),
    ("EnableSmcFilter", "yes", "true or false"),
    ("EnableSmcFilter", 1, "true or false"),
])
def test_invalid_overrides_are_rejected(key, value, message):
    with pytest.raises(bp.OverrideError, match=message) as err:
        bp.validate_overrides(META, source(), {key: value})
    assert str(err.value).startswith(key)


def test_job_params_drop_locked_and_secret_keys_and_apply_overrides():
    src = source(TelegramBotToken="123:abc")
    params = bp.job_params(src, {"TargetRiskReward": {"from": "1.5", "to": "2.0"}})
    assert params["TargetRiskReward"] == "2.0" and params["FastRsiPeriod"] == "7"
    for key in ("UseAiGateMode", "ApiUrl", "BotId", "AccountLabel", "TelegramBotToken"):
        assert key not in params, key
