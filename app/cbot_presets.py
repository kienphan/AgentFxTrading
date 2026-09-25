"""
Strategy × symbol presets for the dashboard's "Setup Instances" screen.

Every strategy has a cell on every symbol (15 × 3 = 45). 22 cells are tuned
per symbol; the other 23 are derived from the nearest tuned cell, scaled by
pip size (index 0.1, XAU/BTC/ETH/JPY 0.01, forex 0.0001) — see README
"Derived presets". All 45 are written out as `docker run` commands in
docs/docker-instances.md, which tests/test_cbot_presets.py keeps in sync
with this module. `params` holds only the strategy-tuned flags.
Infrastructure flags (credentials, symbol, period, BotId, ApiUrl,
AccountLabel, Judas' label/DashboardServerUrl) are emitted by
`build_run_command`, so a preset never knows which account runs it.
"""
from typing import Collection, Dict, Iterable, List, Tuple

DEFAULT_IMAGE = "ghcr.io/spotware/ctrader-console:latest"
API_URL = "http://127.0.0.1:8000/trade"
DASHBOARD_URL = "http://127.0.0.1:8000"

STRATEGIES: Dict[str, Dict[str, str]] = {
    "tms_orb": {"label": "TMS+ORB",     "algo": "AiAgentBot.algo",              "suffix": ""},
    "judas":   {"label": "Judas Sweep", "algo": "AsianRangeJudasSweepBot.algo", "suffix": "-judas"},
    "flowrsi": {"label": "FlowRSI",     "algo": "FlowRsiBot.algo",              "suffix": "-flowrsi"},
}

# Row order of the setup grid (the spec's matrix order).
SYMBOLS: List[str] = [
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "USDCAD", "AUDUSD",
    "AUDJPY", "US30", "USTEC", "DE40", "UK100", "BTCUSD", "ETHUSD",
]

# --- TMS+ORB building blocks (the README blocks share these verbatim) ---
_TMS_HEAD = {"TmsTimeFrame": "Hour", "EmaPeriod": 5}
_LONDON = {**_TMS_HEAD, "SessionName": "london", "OrbStartHour": 8, "SessionEndHour": 17, "SessionDstRule": "Europe"}
_LONDON_INDEX = {**_TMS_HEAD, "SessionName": "london", "OrbStartHour": 8, "SessionEndHour": 16, "SessionDstRule": "Europe"}
_TOKYO = {**_TMS_HEAD, "SessionName": "tokyo", "OrbStartHour": 0, "SessionEndHour": 9, "SessionDstRule": "None"}
_NEWYORK = {**_TMS_HEAD, "SessionName": "newyork", "OrbStartHour": 13, "SessionEndHour": 21, "SessionDstRule": "US"}
_NEWYORK_INDEX = {**_TMS_HEAD, "SessionName": "newyork_index", "OrbStartHour": 14, "OrbStartMinute": 30,
                  "SessionEndHour": 21, "SessionDstRule": "US"}
# The README XAUUSD block predates the TmsTimeFrame/EmaPeriod/SessionName flags; kept verbatim.
_XAU_SESSION = {"OrbStartHour": 13, "SessionEndHour": 21, "SessionDstRule": "US"}

_TMS_ATR = {
    "BreakevenTriggerAtr": 1.2, "BreakevenOffsetAtr": 0.1, "TrailTriggerAtr": 2.0, "TrailDistanceAtr": 1.0,
    "PartialCloseRatio": 0.5, "MinSlAtr": 0.8, "MaxSlAtr": 3.0, "MinTpAtr": 1.0, "MaxTpAtr": 6.0,
    "MaxGivebackAtr": 1.0,
}
# Gold and crypto keep the forex exit profile, but their pip is a cent, so the pip floors scale up:
# giveback arms at $1.50 on gold, and an AI break-even ADJUST needs $3 / $60 / $6 of profit first.
_XAU_ATR = {**_TMS_ATR, "GivebackArmMinPips": 150.0, "MinAdjustBeProfitPips": 300.0}
_BTC_ATR = {**_TMS_ATR, "MinAdjustBeProfitPips": 6000.0}
_ETH_ATR = {**_TMS_ATR, "MinAdjustBeProfitPips": 600.0}
# Indices break out wider than forex, so all four let a trade run further before breakeven and trail looser,
# reach Tier 2 later (2.5 ATR) and then trail at 0.9 ATR and give back at most 35% of the peak.
_INDEX_ATR = {
    **_TMS_ATR,
    "BreakevenTriggerAtr": 1.6, "BreakevenOffsetAtr": 0.2, "TrailTriggerAtr": 2.2, "TrailDistanceAtr": 1.3,
    "Tier2TriggerAtr": 2.5, "Tier2TrailDistanceAtr": 0.9, "Tier2GivebackMfeRatio": 0.35, "MinAdjustBeProfitPips": 50.0,
}
# UK100 shares that breakeven/trail profile but keeps the widest stops of the four. Its giveback stays at
# 1.0 ATR like the others: the bot used to raise every index to 1.0, so a 0.6 here never took effect.
_UK100_ATR = {
    **_INDEX_ATR,
    "MinSlAtr": 1.5, "MaxSlAtr": 4.5, "MinTpAtr": 2.0, "MaxTpAtr": 8.0,
}


def _index_atr(base: dict, tier2_pips: float, arm_pips: float) -> dict:
    """Per-index pip floors: profit that also reaches Tier 2, and the peak that arms the giveback lock."""
    return {**base, "Tier2TriggerPips": tier2_pips, "GivebackArmMinPips": arm_pips}


def _tms(session: dict, breakout: float, or_width: float, buffer: float, bounce, atr: dict = _TMS_ATR) -> dict:
    return {
        **session,
        "MinDecisiveBreakoutPips": breakout, "MinOrWidthPips": or_width, "OrbBufferPips": buffer,
        **atr,
        "EnablePostTpGate": True, "PostTpPullbackAtr": 0.5,
        "BounceTradeEnabled": True, "BounceDistanceThreshold": bounce,
        "RiskPerTradePercent": 0.2, "TrendTpDisabled": True,
    }


def _judas(min_range: float, max_range: float, sweep_buffer: float, ai_sl_floor: float,
           be_trigger: float, sl: float, tp: float, risk_factor=None) -> dict:
    """
    `be_trigger` is deliberately NOT emitted.

    The bot's `breakEvenMode` defaults to Risk_Reward_Ratio, and ProcessBreakEvenLogic
    reads `breakEvenTrigger` (pips) only in the Fixed_Pips branch -- so shipping it meant
    every operator tuning break-even was adjusting a number with no effect. The positional
    is kept because the call sites below are transcribed from the README blocks, which
    list it; `breakEvenRrTrigger` is what actually gates the move.
    """
    params = {
        "UseDirectAiApi": False, "UseAiGateMode": True,
        "minAsianRangePips": min_range, "maxAsianRangePips": max_range, "sweepBufferPips": sweep_buffer,
        "AiSlMinFloorPips": ai_sl_floor,
        "stoplossPip": sl, "takeprofitPip": tp, "enableBreakEvenPrice": True,
    }
    if risk_factor is not None:
        params["riskFactor"] = risk_factor
    return params


def _cell(period: str, session: str, params: dict) -> dict:
    return {"period": period, "session": session, "params": params}


_FLOWRSI_BASE = {
    "FastRsiPeriod": 7, "SlowRsiPeriod": 14, "EnableSmcFilter": True, "EnableFvgDetection": True,
    "EnablePremiumDiscountFilter": True, "RiskPercentage": 0.5, "MaxRiskPerTradeMoney": 50.0,
    "TargetRiskReward": 1.5, "UseAiGateMode": True,
}


def _flowrsi(fvg_min=None, max_spread=None, trail=None, be_extra=None, min_sl=None, min_be=None) -> dict:
    """
    README EURUSD block; forex keeps the cBot's pip defaults (FVG 2 / spread 30 / trail 25 / BE buffer 0.5,
    SL floor 15, break-even 10), other classes override them. `min_sl` / `min_be` set the SL floor and the
    profit needed before break-even (MinSlFloorPips / MinBreakEvenPips).
    """
    params = dict(_FLOWRSI_BASE)
    if fvg_min is not None:
        params.update({"FvgMinPips": fvg_min, "MaxSpreadPips": max_spread,
                       "TrailingStopDistancePips": trail, "BreakEvenExtraPips": be_extra})
    if min_sl is not None:
        params.update({"MinSlFloorPips": min_sl, "MinBreakEvenPips": min_be})
    return params


_JUDAS_SESSION = "London + NY killzones"
_JUDAS_MAJOR = (15.0, 45.0, 3.5, 15.0, 20.0, 15.0, 35.0)    # EURUSD/GBPUSD README block
_JUDAS_CROSS = (25.0, 70.0, 5.0, 25.0, 30.0, 25.0, 50.0)    # GBPJPY/EURJPY README block

# (strategy, SYMBOL) -> {"period", "session", "params"}; 15 symbols × 3 strategies = 45 cells.
PRESETS: Dict[Tuple[str, str], Dict] = {
    # TMS+ORB (AiAgentBot) — README blocks
    ("tms_orb", "XAUUSD"): _cell("m15", "New York", _tms(_XAU_SESSION, 200.0, 400.0, 50.0, 10, atr=_XAU_ATR)),
    ("tms_orb", "EURUSD"): _cell("m15", "London",   _tms(_LONDON, 3.0, 6.0, 1.0, 5)),
    ("tms_orb", "GBPUSD"): _cell("m15", "London",   _tms(_LONDON, 4.5, 10.0, 1.5, 10)),
    ("tms_orb", "USDJPY"): _cell("m15", "Tokyo",    _tms(_TOKYO, 4.0, 8.0, 1.5, 3)),
    ("tms_orb", "GBPJPY"): _cell("m15", "London",   _tms(_LONDON, 6.0, 15.0, 2.0, 5)),
    ("tms_orb", "EURJPY"): _cell("m15", "London",   _tms(_LONDON, 5.0, 12.0, 1.5, 5)),
    ("tms_orb", "USDCAD"): _cell("m15", "New York", _tms(_NEWYORK, 4.0, 10.0, 1.5, 4)),
    ("tms_orb", "AUDUSD"): _cell("m15", "Tokyo",    _tms(_TOKYO, 3.0, 8.0, 1.0, 3)),
    ("tms_orb", "AUDJPY"): _cell("m15", "Tokyo",    _tms(_TOKYO, 4.0, 10.0, 1.5, 4)),
    ("tms_orb", "US30"):   _cell("m15", "New York", _tms(_NEWYORK_INDEX, 30.0, 80.0, 15.0, 30, atr=_index_atr(_INDEX_ATR, 1200.0, 1000.0))),
    ("tms_orb", "USTEC"):  _cell("m5",  "New York", _tms(_NEWYORK_INDEX, 25.0, 70.0, 12.0, 25, atr=_index_atr(_INDEX_ATR, 600.0, 600.0))),
    ("tms_orb", "DE40"):   _cell("m15", "London",   _tms(_LONDON_INDEX, 20.0, 60.0, 10.0, 25, atr=_index_atr(_INDEX_ATR, 400.0, 500.0))),
    ("tms_orb", "UK100"):  _cell("m15", "London",   _tms(_LONDON_INDEX, 25.0, 120.0, 15.0, 1.5, atr=_index_atr(_UK100_ATR, 400.0, 400.0))),
    # TMS+ORB — derived from XAUUSD ($2 / $4 / $0.5) by dollar volatility: BTC ×50, ETH ×4
    ("tms_orb", "BTCUSD"): _cell("m15", "New York", _tms(_NEWYORK, 10000.0, 20000.0, 2500.0, 10, atr=_BTC_ATR)),
    ("tms_orb", "ETHUSD"): _cell("m15", "New York", _tms(_NEWYORK, 800.0, 1600.0, 200.0, 10, atr=_ETH_ATR)),
    # Asian Range Judas Sweep (AsianRangeJudasSweepBot) — README blocks
    # Gold quotes 1 pip = $0.01, so the sweep buffer is 500p = $5.00. It also sets the
    # structural-invalidation threshold, which at the previous 30p was $0.30.
    ("judas", "XAUUSD"): _cell("m15", _JUDAS_SESSION, _judas(200.0, 8000.0, 500.0, 200.0, 250.0, 200.0, 450.0)),
    ("judas", "EURUSD"): _cell("m15", _JUDAS_SESSION, _judas(15.0, 45.0, 3.5, 15.0, 20.0, 15.0, 35.0)),
    ("judas", "GBPUSD"): _cell("m15", _JUDAS_SESSION, _judas(15.0, 45.0, 3.5, 15.0, 20.0, 15.0, 35.0)),
    ("judas", "GBPJPY"): _cell("m15", _JUDAS_SESSION, _judas(25.0, 70.0, 5.0, 25.0, 30.0, 25.0, 50.0)),
    ("judas", "EURJPY"): _cell("m15", _JUDAS_SESSION, _judas(25.0, 70.0, 5.0, 25.0, 30.0, 25.0, 50.0)),
    ("judas", "UK100"):  _cell("m15", _JUDAS_SESSION, _judas(120.0, 800.0, 30.0, 150.0, 200.0, 150.0, 350.0, risk_factor=0.2)),
    ("judas", "BTCUSD"): _cell("m15", _JUDAS_SESSION, _judas(10000.0, 400000.0, 1500.0, 20000.0, 25000.0, 25000.0, 60000.0, risk_factor=0.2)),
    ("judas", "ETHUSD"): _cell("m15", _JUDAS_SESSION, _judas(800.0, 35000.0, 150.0, 1500.0, 2000.0, 2000.0, 5000.0, risk_factor=0.2)),
    # Judas — derived: USD majors reuse the major block, AUDJPY the cross block,
    # indices scale UK100 (12 / 80 / 3 / 15 / 20 / 15 / 35 pt) by daily range: US30 ×5, USTEC ×4, DE40 ×3
    ("judas", "USDJPY"): _cell("m15", _JUDAS_SESSION, _judas(*_JUDAS_MAJOR)),
    ("judas", "USDCAD"): _cell("m15", _JUDAS_SESSION, _judas(*_JUDAS_MAJOR)),
    ("judas", "AUDUSD"): _cell("m15", _JUDAS_SESSION, _judas(*_JUDAS_MAJOR)),
    ("judas", "AUDJPY"): _cell("m15", _JUDAS_SESSION, _judas(*_JUDAS_CROSS)),
    ("judas", "US30"):   _cell("m15", _JUDAS_SESSION, _judas(600.0, 4000.0, 150.0, 750.0, 1000.0, 750.0, 1750.0, risk_factor=0.2)),
    ("judas", "USTEC"):  _cell("m15", _JUDAS_SESSION, _judas(500.0, 3000.0, 120.0, 600.0, 800.0, 600.0, 1400.0, risk_factor=0.2)),
    ("judas", "DE40"):   _cell("m15", _JUDAS_SESSION, _judas(350.0, 2500.0, 90.0, 450.0, 600.0, 450.0, 1000.0, risk_factor=0.2)),
    # FlowRSI (FlowRsiBot) — README block
    ("flowrsi", "EURUSD"): _cell("m15", "All sessions", _flowrsi()),
    # FlowRSI — derived: RSI/RR flags are symbol-agnostic; only the pip-sized filters change per class.
    # JPY pairs widen the SL floor to 18p and wait for 15p before break-even.
    ("flowrsi", "GBPUSD"): _cell("m15", "All sessions", _flowrsi()),
    ("flowrsi", "USDJPY"): _cell("m15", "All sessions", _flowrsi(min_sl=18.0, min_be=15.0)),
    ("flowrsi", "GBPJPY"): _cell("m15", "All sessions", _flowrsi(min_sl=18.0, min_be=15.0)),
    ("flowrsi", "EURJPY"): _cell("m15", "All sessions", _flowrsi(min_sl=18.0, min_be=15.0)),
    ("flowrsi", "USDCAD"): _cell("m15", "All sessions", _flowrsi()),
    ("flowrsi", "AUDUSD"): _cell("m15", "All sessions", _flowrsi()),
    ("flowrsi", "AUDJPY"): _cell("m15", "All sessions", _flowrsi(min_sl=18.0, min_be=15.0)),
    ("flowrsi", "XAUUSD"): _cell("m15", "All sessions", _flowrsi(50.0, 50.0, 800.0, 20.0, min_sl=1500.0, min_be=1000.0)),  # $0.5 / $0.5 / $8 / $0.2, SL $15, BE $10
    ("flowrsi", "US30"):   _cell("m15", "All sessions", _flowrsi(100.0, 60.0, 350.0, 10.0)),         # 10 / 6 / 35 / 1 pt
    ("flowrsi", "USTEC"):  _cell("m15", "All sessions", _flowrsi(80.0, 50.0, 350.0, 10.0)),          # 8 / 5 / 35 / 1 pt
    ("flowrsi", "DE40"):   _cell("m15", "All sessions", _flowrsi(50.0, 40.0, 350.0, 10.0)),          # 5 / 4 / 35 / 1 pt
    ("flowrsi", "UK100"):  _cell("m15", "All sessions", _flowrsi(30.0, 30.0, 350.0, 5.0)),           # 3 / 3 / 35 / 0.5 pt
    ("flowrsi", "BTCUSD"): _cell("m15", "All sessions", _flowrsi(5000.0, 5000.0, 30000.0, 1000.0)),  # $50 / $50 / $300 / $10
    ("flowrsi", "ETHUSD"): _cell("m15", "All sessions", _flowrsi(300.0, 500.0, 2000.0, 100.0)),      # $3 / $5 / $20 / $1
}


def _fmt(value) -> str:
    """Render a param value the way the README writes it: true/false, bare numbers, "quoted strings"."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return f'"{value}"'


# Preset "session" label -> the token `container_name` puts after the symbol, so `docker ps` shows
# which session a bot trades without reading its --SessionName flag.
SESSION_SLUGS: Dict[str, str] = {
    "Tokyo": "tokyo",
    "London": "london",
    "New York": "newyork",
    _JUDAS_SESSION: "KZ-london-ny",
    "All sessions": "all",
}


def container_name(slug: str, strategy: str, symbol: str) -> str:
    session = SESSION_SLUGS[PRESETS[(strategy, symbol)]["session"]]
    return f"cbot-{slug}-{symbol.lower()}-{session}{STRATEGIES[strategy]['suffix']}"


def describe_cell(strategy: str, symbol: str, account_label: str) -> str:
    return f"{STRATEGIES[strategy]['label']} {symbol} {PRESETS[(strategy, symbol)]['period']} — {account_label}"


def build_run_command(account: dict, strategy: str, symbol: str, project_root: str, ctrader_home: str,
                      image: str = DEFAULT_IMAGE) -> str:
    """Single-line `docker run` for one preset cell. `account` needs slug, ctid_email, account_number, label, pwd_file."""
    preset = PRESETS[(strategy, symbol)]
    name = container_name(account["slug"], strategy, symbol)
    parts = [
        "docker", "run", "-d", "--name", name, "--restart", "unless-stopped", "--network", "host",
        "-v", f"{project_root}:/workspace", "-v", f"{ctrader_home}:/root",
        image, "run", f"/workspace/cBot/{STRATEGIES[strategy]['algo']}",
        f"--ctid={account['ctid_email']}", f"--pwd-file={account['pwd_file']}", f"--account={account['account_number']}",
        f"--symbol={symbol}", f"--period={preset['period']}", "--full-access",
        f'--BotId="{name}"', f'--ApiUrl="{API_URL}"', f'--AccountLabel="{account["label"]}"',
    ]
    if strategy == "judas":
        parts += [f'--label="{name}"', f'--DashboardServerUrl="{DASHBOARD_URL}"']
    parts += [f"--{key}={_fmt(value)}" for key, value in preset["params"].items()]
    return " ".join(parts)


def installed_cells(accounts: Iterable[dict], config_names: Collection[str]) -> List[dict]:
    """Preset cells that already have a cbot_configs row, attributed to the account whose slug named them.

    One row per (cell, account): the same cell can be installed for several accounts. Configs not
    named by `container_name` (hand-written, or from a since-deleted account) are not reported.
    Ordered by the grid (PRESETS order) then by `accounts` order, so the UI needs no sorting.
    """
    accounts = list(accounts)
    rows = []
    for (strategy, symbol) in PRESETS:
        for account in accounts:
            name = container_name(account["slug"], strategy, symbol)
            if name in config_names:
                rows.append({
                    "symbol": symbol, "strategy": strategy, "name": name,
                    "account_id": account["id"], "account_label": account["label"],
                    "account_type": account["account_type"],
                })
    return rows


def presets_payload() -> dict:
    """What GET /api/setup/presets returns: the grid, without params."""
    return {
        "strategies": STRATEGIES,
        "symbols": SYMBOLS,
        "cells": [
            {"symbol": symbol, "strategy": strategy, "period": cell["period"], "session": cell["session"]}
            for (strategy, symbol), cell in PRESETS.items()
        ],
    }
