"""Backtest page: the help text behind each parameter's "?" icon.

The form shows every FlowRSI parameter outside the hidden groups, so the help table is checked against
the [Parameter] declarations in cBot/FlowRsiBot.cs itself, not against the 23-parameter metadata
fixture: a parameter added to the cBot fails here until it has a tooltip."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backtest_params as bp  # noqa: E402
from app.backtest_command import DROPPED_PARAMS, HIDDEN_GROUPS, locked_params, parse_run_command  # noqa: E402
from app.backtest_param_help import PARAM_HELP, param_help  # noqa: E402
from app.cbot_presets import build_run_command  # noqa: E402

CBOT = ROOT / "cBot" / "FlowRsiBot.cs"
META = json.loads((ROOT / "tests" / "fixtures" / "backtest" / "flowrsi_metadata.json").read_text())
ACCOUNT = {"slug": "demo-demo", "ctid_email": "trader@example.com", "account_number": "10115236",
           "label": "demo", "pwd_file": "/root/ctrader_data/ctid_demo-demo_pwd"}
PARAMETER = re.compile(r'\[Parameter\("[^"]*",\s*Group\s*=\s*"([^"]+)"[^\]]*\]\s*public\s+\S+\s+(\w+)\s*\{')


def _form_keys():
    """PropertyNames of the FlowRSI parameters the Backtest form shows."""
    declared = PARAMETER.findall(CBOT.read_text(encoding="utf-8"))
    assert len(declared) > 60, "the [Parameter] pattern no longer matches FlowRsiBot.cs"
    hidden = set(locked_params("flowrsi", None)) | DROPPED_PARAMS
    return {key for group, key in declared if group not in HIDDEN_GROUPS and key not in hidden}


def test_every_parameter_on_the_form_has_help_and_no_help_is_orphaned():
    keys = _form_keys()
    assert "FastRsiPeriod" in keys and "RemoveTpOnTrailing" in keys and "UseAiGateMode" not in keys
    help_keys = set(PARAM_HELP["flowrsi"])
    assert keys - help_keys == set(), "parameters on the Backtest form without a tooltip"
    assert help_keys - keys == set(), "tooltips for parameters the form does not show"


def test_help_texts_are_non_trivial():
    for key, text in PARAM_HELP["flowrsi"].items():
        assert text == text.strip() and len(text) >= 60, key


def test_parameters_the_cbot_never_reads_say_so():
    for key in ("EnablePremiumDiscountFilter", "AtrTpMultiplier", "FixedTpPips", "MaxPositionsAllowed"):
        assert "không" in PARAM_HELP["flowrsi"][key].lower() and "⚠" in PARAM_HELP["flowrsi"][key], key


def test_param_help_lookup():
    assert param_help("flowrsi", "FastRsiPeriod") == PARAM_HELP["flowrsi"]["FastRsiPeriod"]
    assert param_help("flowrsi", "NoSuchParam") is None
    assert param_help("judas", "FastRsiPeriod") is None


def test_param_view_carries_the_help():
    src = parse_run_command("cbot-demo-demo-eurusd-all-flowrsi",
                            build_run_command(ACCOUNT, "flowrsi", "EURUSD", "/w", "/r"))
    params = {p["key"]: p for g in bp.param_view(META, src)["groups"] for p in g["params"]}
    for key, p in params.items():
        assert p["help"] == PARAM_HELP["flowrsi"][key], key
