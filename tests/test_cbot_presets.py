import re
import shlex
import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from app.cbot_presets import (
    DEFAULT_IMAGE, PRESETS, SESSION_SLUGS, STRATEGIES, SYMBOLS,
    build_run_command, container_name, describe_cell, installed_cells, presets_payload,
)

ROOT = "/home/forge/AgentFxTrading"
HOME = "/home/forge/ctrader"
DEMO = {"id": 1, "slug": "demo-main", "ctid_email": "me@example.com", "account_number": "10101649",
        "account_type": "demo", "label": "Demo Main", "pwd_file": "/root/ctrader_data/ctid_demo-main_pwd"}
LIVE = {"id": 2, "slug": "live-ic", "ctid_email": "me@example.com", "account_number": "6094347",
        "account_type": "live", "label": "IC", "pwd_file": "/root/ctrader_data/ctid_live-ic_pwd"}

# The full matrix: every strategy on every symbol, 15 × 3 = 45 cells.
EXPECTED_MATRIX = {sym: {"tms_orb", "judas", "flowrsi"} for sym in (
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "USDCAD", "AUDUSD",
    "AUDJPY", "US30", "USTEC", "DE40", "UK100", "BTCUSD", "ETHUSD",
)}


def test_matrix_covers_every_strategy_on_every_symbol():
    assert len(PRESETS) == 45
    actual = {}
    for strategy, symbol in PRESETS:
        actual.setdefault(symbol, set()).add(strategy)
    assert actual == EXPECTED_MATRIX
    assert len(SYMBOLS) == 15
    assert set(SYMBOLS) == set(EXPECTED_MATRIX)


def test_every_cell_is_well_formed():
    assert set(STRATEGIES) == {"tms_orb", "judas", "flowrsi"}
    for (strategy, symbol), cell in PRESETS.items():
        assert strategy in STRATEGIES
        assert symbol == symbol.upper()
        assert cell["period"] in ("m5", "m15"), (strategy, symbol)
        assert cell["session"], (strategy, symbol)
        assert cell["params"], (strategy, symbol)
    # --period in the README block is the source of truth
    assert PRESETS[("tms_orb", "USTEC")]["period"] == "m5"
    assert PRESETS[("tms_orb", "DE40")]["period"] == "m15"


def test_tms_orb_golden_command():
    cmd = build_run_command(DEMO, "tms_orb", "EURUSD", ROOT, HOME)
    assert cmd == (
        "docker run -d --name cbot-demo-main-eurusd-london --restart unless-stopped --network host "
        f"-v {ROOT}:/workspace -v {HOME}:/root {DEFAULT_IMAGE} run /workspace/cBot/AiAgentBot.algo "
        "--ctid=me@example.com --pwd-file=/root/ctrader_data/ctid_demo-main_pwd --account=10101649 "
        "--symbol=EURUSD --period=m15 --full-access "
        '--BotId="cbot-demo-main-eurusd-london" --ApiUrl="http://127.0.0.1:8000/trade" --AccountLabel="Demo Main" '
        '--TmsTimeFrame="Hour" --EmaPeriod=5 --SessionName="london" --OrbStartHour=8 --SessionEndHour=17 '
        '--SessionDstRule="Europe" --MinDecisiveBreakoutPips=3.0 --MinOrWidthPips=6.0 --OrbBufferPips=1.0 '
        "--BreakevenTriggerAtr=1.2 --BreakevenOffsetAtr=0.1 --TrailTriggerAtr=2.0 --TrailDistanceAtr=1.0 "
        "--PartialCloseRatio=0.5 --MinSlAtr=0.8 --MaxSlAtr=3.0 --MinTpAtr=1.0 --MaxTpAtr=6.0 --MaxGivebackAtr=1.0 "
        "--EnablePostTpGate=true --PostTpPullbackAtr=0.5 --BounceTradeEnabled=true --BounceDistanceThreshold=5 "
        "--RiskPerTradePercent=0.2 --TrendTpDisabled=true"
    )


def test_judas_golden_command():
    cmd = build_run_command(LIVE, "judas", "GBPUSD", ROOT, HOME)
    assert cmd == (
        "docker run -d --name cbot-live-ic-gbpusd-KZ-london-ny-judas --restart unless-stopped --network host "
        f"-v {ROOT}:/workspace -v {HOME}:/root {DEFAULT_IMAGE} run /workspace/cBot/AsianRangeJudasSweepBot.algo "
        "--ctid=me@example.com --pwd-file=/root/ctrader_data/ctid_live-ic_pwd --account=6094347 "
        "--symbol=GBPUSD --period=m15 --full-access "
        '--BotId="cbot-live-ic-gbpusd-KZ-london-ny-judas" --ApiUrl="http://127.0.0.1:8000/trade" --AccountLabel="IC" '
        '--label="cbot-live-ic-gbpusd-KZ-london-ny-judas" --DashboardServerUrl="http://127.0.0.1:8000" '
        "--UseDirectAiApi=false --UseAiGateMode=true --minAsianRangePips=15.0 --maxAsianRangePips=45.0 "
        "--sweepBufferPips=3.5 --AiSlMinFloorPips=15.0 --stoplossPip=15.0 "
        "--takeprofitPip=35.0 --enableBreakEvenPrice=true"
    )


def test_flowrsi_golden_command():
    cmd = build_run_command(DEMO, "flowrsi", "EURUSD", ROOT, HOME)
    assert cmd == (
        "docker run -d --name cbot-demo-main-eurusd-all-flowrsi --restart unless-stopped --network host "
        f"-v {ROOT}:/workspace -v {HOME}:/root {DEFAULT_IMAGE} run /workspace/cBot/FlowRsiBot.algo "
        "--ctid=me@example.com --pwd-file=/root/ctrader_data/ctid_demo-main_pwd --account=10101649 "
        "--symbol=EURUSD --period=m15 --full-access "
        '--BotId="cbot-demo-main-eurusd-all-flowrsi" --ApiUrl="http://127.0.0.1:8000/trade" --AccountLabel="Demo Main" '
        "--FastRsiPeriod=7 --SlowRsiPeriod=14 --EnableSmcFilter=true --EnableFvgDetection=true "
        "--EnablePremiumDiscountFilter=true --RiskPercentage=0.5 --MaxRiskPerTradeMoney=50.0 "
        "--TargetRiskReward=1.5 --UseAiGateMode=true"
    )


def test_readme_quirks_are_kept_verbatim():
    xau = build_run_command(DEMO, "tms_orb", "XAUUSD", ROOT, HOME)
    assert "--SessionName" not in xau and "--TmsTimeFrame" not in xau      # README XAUUSD block has neither
    assert "--OrbStartHour=13 --SessionEndHour=21 --SessionDstRule=\"US\"" in xau
    assert "--MinDecisiveBreakoutPips=200.0 --MinOrWidthPips=400.0 --OrbBufferPips=50.0" in xau
    us30 = build_run_command(DEMO, "tms_orb", "US30", ROOT, HOME)
    assert '--SessionName="newyork_index" --OrbStartHour=14 --OrbStartMinute=30 --SessionEndHour=21' in us30
    uk = build_run_command(DEMO, "tms_orb", "UK100", ROOT, HOME)
    assert "--MinSlAtr=1.5 --MaxSlAtr=4.5 --MinTpAtr=2.0 --MaxTpAtr=8.0 --MaxGivebackAtr=1.0" in uk
    assert "--BounceDistanceThreshold=1.5 " in uk
    btc = build_run_command(DEMO, "judas", "BTCUSD", ROOT, HOME)
    assert btc.endswith("--takeprofitPip=60000.0 --enableBreakEvenPrice=true --riskFactor=0.2")
    assert "--maxAsianRangePips=400000.0" in btc
    xau_judas = build_run_command(DEMO, "judas", "XAUUSD", ROOT, HOME)
    assert "--riskFactor" not in xau_judas


def test_indices_share_one_breakeven_and_trailing_profile():
    """The four index symbols breakeven and trail together; only UK100's stop/target band stays wider."""
    index_trail = "--BreakevenTriggerAtr=1.6 --BreakevenOffsetAtr=0.2 --TrailTriggerAtr=2.2 --TrailDistanceAtr=1.3"
    for symbol in ("US30", "USTEC", "DE40", "UK100"):
        assert index_trail in build_run_command(DEMO, "tms_orb", symbol, ROOT, HOME), symbol
    # Forex and crypto keep the looser TMS profile — the index values must not leak into _TMS_ATR.
    tms_trail = "--BreakevenTriggerAtr=1.2 --BreakevenOffsetAtr=0.1 --TrailTriggerAtr=2.0 --TrailDistanceAtr=1.0"
    for symbol in ("EURUSD", "USDJPY", "XAUUSD", "BTCUSD"):
        assert tms_trail in build_run_command(DEMO, "tms_orb", symbol, ROOT, HOME), symbol


def test_tms_orb_exit_floors_are_flags_per_symbol():
    """The pip floors AiAgentBot used to pick from the symbol name now travel as flags."""
    index_profile = "--Tier2TriggerAtr=2.5 --Tier2TrailDistanceAtr=0.9 --Tier2GivebackMfeRatio=0.35 --MinAdjustBeProfitPips=50.0"
    for sym, tier2_pips, arm_pips in (("US30", 1200.0, 1000.0), ("USTEC", 600.0, 600.0),
                                      ("DE40", 400.0, 500.0), ("UK100", 400.0, 400.0)):
        cmd = build_run_command(DEMO, "tms_orb", sym, ROOT, HOME)
        assert index_profile in cmd, sym
        assert f"--Tier2TriggerPips={tier2_pips} --GivebackArmMinPips={arm_pips} " in cmd, sym
    xau = build_run_command(DEMO, "tms_orb", "XAUUSD", ROOT, HOME)
    assert "--GivebackArmMinPips=150.0 --MinAdjustBeProfitPips=300.0 " in xau
    assert "--MinAdjustBeProfitPips=6000.0 " in build_run_command(DEMO, "tms_orb", "BTCUSD", ROOT, HOME)
    assert "--MinAdjustBeProfitPips=600.0 " in build_run_command(DEMO, "tms_orb", "ETHUSD", ROOT, HOME)
    # Forex keeps the cBot defaults (Tier 2 at 2.0 ATR, 0.6 ATR trail, 30% giveback, arm 14p, AI BE 20p).
    for sym in ("EURUSD", "USDJPY", "GBPJPY", "AUDUSD"):
        cmd = build_run_command(DEMO, "tms_orb", sym, ROOT, HOME)
        assert "--Tier2" not in cmd and "--GivebackArmMinPips" not in cmd and "--MinAdjustBeProfitPips" not in cmd, sym


def test_crypto_tms_orb_scales_from_gold_on_the_new_york_session():
    btc = build_run_command(DEMO, "tms_orb", "BTCUSD", ROOT, HOME)
    assert " --symbol=BTCUSD --period=m15 " in btc
    assert '--SessionName="newyork" --OrbStartHour=13 --SessionEndHour=21 --SessionDstRule="US"' in btc
    assert "--MinDecisiveBreakoutPips=10000.0 --MinOrWidthPips=20000.0 --OrbBufferPips=2500.0" in btc   # $100/$200/$25
    assert "--BounceDistanceThreshold=10 " in btc
    eth = build_run_command(DEMO, "tms_orb", "ETHUSD", ROOT, HOME)
    assert "--MinDecisiveBreakoutPips=800.0 --MinOrWidthPips=1600.0 --OrbBufferPips=200.0" in eth       # $8/$16/$2
    assert PRESETS[("tms_orb", "BTCUSD")]["session"] == "New York"


def test_judas_forex_cells_reuse_the_major_and_cross_presets():
    major = "--minAsianRangePips=15.0 --maxAsianRangePips=45.0 --sweepBufferPips=3.5 --AiSlMinFloorPips=15.0 --stoplossPip=15.0 --takeprofitPip=35.0"
    cross = "--minAsianRangePips=25.0 --maxAsianRangePips=70.0 --sweepBufferPips=5.0 --AiSlMinFloorPips=25.0 --stoplossPip=25.0 --takeprofitPip=50.0"
    for sym in ("USDJPY", "USDCAD", "AUDUSD"):
        cmd = build_run_command(DEMO, "judas", sym, ROOT, HOME)
        assert major in cmd and "--riskFactor" not in cmd, sym
    cmd = build_run_command(DEMO, "judas", "AUDJPY", ROOT, HOME)
    assert cross in cmd and "--riskFactor" not in cmd


def test_judas_index_cells_scale_from_uk100_with_index_risk():
    expected = {   # UK100 ×5 / ×4 / ×3 (pip 0.1): min/max range, sweep buffer, SL floor, BE, SL, TP
        "US30":  "--minAsianRangePips=600.0 --maxAsianRangePips=4000.0 --sweepBufferPips=150.0 --AiSlMinFloorPips=750.0 --stoplossPip=750.0 --takeprofitPip=1750.0",
        "USTEC": "--minAsianRangePips=500.0 --maxAsianRangePips=3000.0 --sweepBufferPips=120.0 --AiSlMinFloorPips=600.0 --stoplossPip=600.0 --takeprofitPip=1400.0",
        "DE40":  "--minAsianRangePips=350.0 --maxAsianRangePips=2500.0 --sweepBufferPips=90.0 --AiSlMinFloorPips=450.0 --stoplossPip=450.0 --takeprofitPip=1000.0",
    }
    for sym, params in expected.items():
        cmd = build_run_command(LIVE, "judas", sym, ROOT, HOME)
        assert params in cmd, sym
        assert cmd.endswith("--enableBreakEvenPrice=true --riskFactor=0.2"), sym


def test_flowrsi_forex_cells_match_the_eurusd_block_exactly():
    base = build_run_command(DEMO, "flowrsi", "EURUSD", ROOT, HOME)
    for sym in ("GBPUSD", "USDCAD", "AUDUSD"):
        cmd = build_run_command(DEMO, "flowrsi", sym, ROOT, HOME)
        assert cmd == base.replace("EURUSD", sym).replace("eurusd", sym.lower()), sym
        assert "--FvgMinPips" not in cmd and "--MaxSpreadPips" not in cmd, sym


def test_flowrsi_jpy_cells_add_only_the_jpy_floors():
    base = build_run_command(DEMO, "flowrsi", "EURUSD", ROOT, HOME)
    for sym in ("USDJPY", "GBPJPY", "EURJPY", "AUDJPY"):
        cmd = build_run_command(DEMO, "flowrsi", sym, ROOT, HOME)
        expected = base.replace("EURUSD", sym).replace("eurusd", sym.lower())
        assert cmd == expected + " --MinSlFloorPips=18.0 --MinBreakEvenPips=15.0", sym


def test_flowrsi_gold_index_crypto_override_the_pip_sized_params():
    expected = {   # FvgMinPips, MaxSpreadPips, TrailingStopDistancePips, BreakEvenExtraPips, SL floor / BE floor
        "XAUUSD": (50.0, 50.0, 800.0, 20.0, " --MinSlFloorPips=1500.0 --MinBreakEvenPips=1000.0"),
        "US30":   (100.0, 60.0, 350.0, 10.0, ""),
        "USTEC":  (80.0, 50.0, 350.0, 10.0, ""),
        "DE40":   (50.0, 40.0, 350.0, 10.0, ""),
        "UK100":  (30.0, 30.0, 350.0, 5.0, ""),
        "BTCUSD": (5000.0, 5000.0, 30000.0, 1000.0, ""),
        "ETHUSD": (300.0, 500.0, 2000.0, 100.0, ""),
    }
    for sym, (fvg, spread, trail, be, floors) in expected.items():
        cmd = build_run_command(DEMO, "flowrsi", sym, ROOT, HOME)
        assert cmd.endswith(
            "--TargetRiskReward=1.5 --UseAiGateMode=true "
            f"--FvgMinPips={fvg} --MaxSpreadPips={spread} --TrailingStopDistancePips={trail} --BreakEvenExtraPips={be}"
            + floors
        ), sym
        assert "--FastRsiPeriod=7 --SlowRsiPeriod=14" in cmd, sym


def test_container_name_carries_the_session():
    assert container_name("demo-main", "tms_orb", "XAUUSD") == "cbot-demo-main-xauusd-newyork"
    assert container_name("demo-main", "tms_orb", "EURUSD") == "cbot-demo-main-eurusd-london"
    assert container_name("demo-main", "tms_orb", "USDJPY") == "cbot-demo-main-usdjpy-tokyo"
    assert container_name("live-ic", "judas", "XAUUSD") == "cbot-live-ic-xauusd-KZ-london-ny-judas"
    assert container_name("live-ic", "flowrsi", "EURUSD") == "cbot-live-ic-eurusd-all-flowrsi"


def test_every_preset_session_has_a_slug_in_its_name():
    for (strategy, symbol), cell in PRESETS.items():
        name = container_name("demo-main", strategy, symbol)
        assert f"-{SESSION_SLUGS[cell['session']]}" in name, (strategy, symbol)
        assert name.endswith(SESSION_SLUGS[cell["session"]] + STRATEGIES[strategy]["suffix"]), name


def test_live_vs_demo_naming():
    # /api/bots detects live bots by "live-" in the name; demo names must not contain it
    assert "live-" in container_name("live-ic", "tms_orb", "US30")
    assert "live" not in container_name("demo-main", "tms_orb", "US30")


def test_every_command_survives_shlex_and_has_docker_essentials():
    for (strategy, symbol) in PRESETS:
        cmd = build_run_command(LIVE, strategy, symbol, ROOT, HOME)
        assert "\n" not in cmd and "\\" not in cmd
        parts = shlex.split(cmd)
        assert parts[:3] == ["docker", "run", "-d"]
        assert "--name" in parts and parts[parts.index("--name") + 1] == container_name("live-ic", strategy, symbol)
        assert parts.count("-v") == 2
        assert f"{ROOT}:/workspace" in parts and f"{HOME}:/root" in parts
        assert f"--symbol={symbol}" in parts
        assert f"--period={PRESETS[(strategy, symbol)]['period']}" in parts
        assert f"--BotId={container_name('live-ic', strategy, symbol)}" in parts   # quotes stripped by shlex
        assert "--AccountLabel=IC" in parts
        assert ("--DashboardServerUrl=http://127.0.0.1:8000" in parts) == (strategy == "judas")
        assert f"/workspace/cBot/{STRATEGIES[strategy]['algo']}" in parts


def test_custom_image_and_describe_cell():
    cmd = build_run_command(DEMO, "tms_orb", "USTEC", ROOT, HOME, image="ghcr.io/spotware/ctrader-console:1.2")
    assert " ghcr.io/spotware/ctrader-console:1.2 run " in cmd
    assert describe_cell("tms_orb", "USTEC", "Demo Main") == "TMS+ORB USTEC m5 — Demo Main"
    assert describe_cell("judas", "XAUUSD", "IC") == "Judas Sweep XAUUSD m15 — IC"
    assert describe_cell("flowrsi", "EURUSD", "IC") == "FlowRSI EURUSD m15 — IC"


def test_presets_payload_shape():
    payload = presets_payload()
    assert payload["strategies"] == STRATEGIES
    assert payload["symbols"] == SYMBOLS
    assert len(payload["cells"]) == 45
    cell = next(c for c in payload["cells"] if c["symbol"] == "USTEC" and c["strategy"] == "tms_orb")
    assert cell == {"symbol": "USTEC", "strategy": "tms_orb", "period": "m5", "session": "New York"}
    for c in payload["cells"]:
        assert set(c) == {"symbol", "strategy", "period", "session"}
        assert "params" not in c


def test_installed_cells_maps_config_names_back_to_cell_and_account():
    names = {
        "cbot-demo-main-xauusd-newyork",          # DEMO tms_orb XAUUSD
        "cbot-live-ic-xauusd-newyork",            # LIVE tms_orb XAUUSD (same cell, second account)
        "cbot-live-ic-eurusd-all-flowrsi",        # LIVE flowrsi EURUSD
        "cbot-live-ic-xauusd",                    # pre-session name: no longer a preset name, ignored
        "cbot-manual-thing",                      # hand-made config: not a preset name, ignored
    }
    rows = installed_cells([DEMO, LIVE], names)
    assert rows == [
        {"symbol": "XAUUSD", "strategy": "tms_orb", "name": "cbot-demo-main-xauusd-newyork",
         "account_id": 1, "account_label": "Demo Main", "account_type": "demo"},
        {"symbol": "XAUUSD", "strategy": "tms_orb", "name": "cbot-live-ic-xauusd-newyork",
         "account_id": 2, "account_label": "IC", "account_type": "live"},
        {"symbol": "EURUSD", "strategy": "flowrsi", "name": "cbot-live-ic-eurusd-all-flowrsi",
         "account_id": 2, "account_label": "IC", "account_type": "live"},
    ]


def test_installed_cells_is_empty_without_accounts_or_configs():
    assert installed_cells([], {"cbot-demo-main-xauusd-newyork"}) == []
    assert installed_cells([DEMO, LIVE], set()) == []


# --- docs ↔ preset drift guard ---------------------------------------------------
# docs/docker-instances.md spells out all 45 cells as `docker run` commands. Those blocks are what a
# user copies by hand, so a preset edit that forgets them ships docs that silently disagree with what
# the dashboard generates. Compare the strategy flags only: the doc uses placeholder credentials and a
# `demo` account slug. The six READMEs must not re-document cells — they link to the catalog instead,
# so there is exactly one place to keep in sync (the README quick-start example is the one exception).
INSTANCES_DOC = root / "docs" / "docker-instances.md"
README_FILES = sorted(root.glob("README*.md"))
README_EXAMPLE = ("tms_orb", "XAUUSD")
_INFRA_FLAGS = {"ctid", "pwd-file", "account", "symbol", "period", "full-access",
                "BotId", "ApiUrl", "AccountLabel", "label", "DashboardServerUrl"}
_ALGO_TO_STRATEGY = {spec["algo"]: name for name, spec in STRATEGIES.items()}


def _strategy_flags(command: str) -> dict:
    """The tuned `--Flag=value` pairs of a docker run, with line continuations folded and infra dropped."""
    flat = re.sub(r"\s+", " ", command.replace("\\\n", " "))
    return {key: value for key, value in re.findall(r'--([A-Za-z0-9_-]+)=("[^"]*"|\S+)', flat)
            if key not in _INFRA_FLAGS}


def _documented_cells(path: Path) -> dict:
    """(strategy, symbol) -> strategy flags, for every cBot `docker run` block in one markdown file."""
    cells = {}
    for block in re.findall(r"```bash\n(.*?)```", path.read_text(), re.S):
        algo = re.search(r"/workspace/cBot/(\S+\.algo)", block)
        symbol = re.search(r"--symbol=(\S+)", block)
        if not (algo and symbol) or algo.group(1) not in _ALGO_TO_STRATEGY:
            continue
        key = (_ALGO_TO_STRATEGY[algo.group(1)], symbol.group(1))
        assert key not in cells, f"{path.name} documents {key} twice"
        cells[key] = _strategy_flags(block)
    return cells


def test_instances_doc_exists():
    assert INSTANCES_DOC.is_file(), "docs/docker-instances.md is the instance catalog; it must exist"


def test_instances_doc_covers_every_cell():
    assert set(_documented_cells(INSTANCES_DOC)) == set(PRESETS), (
        "docs/docker-instances.md must document all 45 preset cells, no more and no fewer")


@pytest.mark.parametrize(("strategy", "symbol"), sorted(PRESETS), ids=lambda v: v)
def test_instances_doc_matches_the_presets(strategy, symbol):
    documented = _documented_cells(INSTANCES_DOC)[(strategy, symbol)]
    generated = _strategy_flags(build_run_command(DEMO, strategy, symbol, ROOT, HOME))
    assert documented == generated, (
        f"docs/docker-instances.md disagrees with app/cbot_presets.py for {strategy} {symbol}: "
        f"{ {k: (generated.get(k), documented.get(k)) for k in set(generated) | set(documented) if generated.get(k) != documented.get(k)} }"
    )


def test_readme_files_are_discovered():
    assert [p.name for p in README_FILES] == [
        "README.ja.md", "README.md", "README.pt.md", "README.ru.md", "README.vi.md", "README.zh.md"]


@pytest.mark.parametrize("readme", README_FILES, ids=lambda p: p.name)
def test_readmes_keep_only_the_quick_start_example(readme):
    """A README carries one worked example and links to the catalog; anything more is a second copy to drift."""
    cells = _documented_cells(readme)
    assert set(cells) == {README_EXAMPLE}, (
        f"{readme.name} should document only {README_EXAMPLE} and link to docs/docker-instances.md "
        f"for the rest, but documents {sorted(cells)}")
    generated = _strategy_flags(build_run_command(DEMO, *README_EXAMPLE, ROOT, HOME))
    assert cells[README_EXAMPLE] == generated, f"{readme.name}'s example disagrees with app/cbot_presets.py"


@pytest.mark.parametrize("readme", README_FILES, ids=lambda p: p.name)
def test_readmes_link_to_the_instance_catalog(readme):
    assert "docs/docker-instances.md" in readme.read_text(), (
        f"{readme.name} must link to the instance catalog now that it no longer lists the commands")


# --- no symbol-name branching in the cBots ------------------------------------------
# Per-symbol tuning lives in the presets above. A cBot that branches on SymbolName ("XAU", "JPY",
# "US30") silently overrides the preset flags and cannot be swept in a backtest. The one exception
# is IsCurrencyAffected, which maps a symbol to the currencies whose news pause it.
_SYMBOL_BRANCH = re.compile(
    r'Contains\("(XAU|GOLD|XAG|SILVER|JPY|US30|DJ30|USTEC|NAS100|DE40|GER40|UK100|GB100|BTC|ETH)"\)')


def _without_currency_mapping(src: str) -> str:
    start = src.find("private bool IsCurrencyAffected(")
    if start < 0:
        return src
    end = src.index("\n        }\n", start)
    return src[:start] + src[end:]


@pytest.mark.parametrize("cs_name", ["AiAgentBot.cs", "AsianRangeJudasSweepBot.cs", "FlowRsiBot.cs"])
def test_cbots_do_not_branch_on_the_symbol_name(cs_name):
    src = _without_currency_mapping((root / "cBot" / cs_name).read_text(encoding="utf-8"))
    hits = [m.group(0) for m in _SYMBOL_BRANCH.finditer(src)]
    assert not hits, f"{cs_name} branches on the symbol name {hits}; make it a [Parameter] and set it in the presets"
