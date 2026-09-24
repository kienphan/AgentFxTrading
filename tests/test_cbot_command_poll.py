"""cBot side of the dashboard commands (spec 2026-09-24): poll, close, pause guard.

The Mac has no dotnet, so like the other cBot tests these read the C# source; the build itself is
checked on the VPS with ctrader-console.
"""
import re
from pathlib import Path

import pytest

CBOT = Path(__file__).resolve().parent.parent / "cBot"

# bot file -> how its command executor finds its own positions (never another bot's)
OWN_POSITIONS = {
    "AiAgentBot.cs": "var own = GetBotPositions();",
    "AsianRangeJudasSweepBot.cs": "var own = Positions.FindAll(label, SymbolName);",
    "FlowRsiBot.cs": "var own = GetBotPositions();",
}
BOTS = list(OWN_POSITIONS)


def _src(name):
    return (CBOT / name).read_text(encoding="utf-8")


def _method(src, signature):
    """From `signature` to the next member declared at class-member indentation (8 spaces)."""
    start = src.index(signature)
    rest = src[start + len(signature):]
    nxt = re.search(r"\n {8}(private|protected|public|internal) ", rest)
    return src[start: start + len(signature) + (nxt.start() if nxt else len(rest))]


def _entry_orders(src):
    """Offsets of real ExecuteMarketOrder calls (comment lines skipped)."""
    for m in re.finditer(r"ExecuteMarketOrder\(", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if "//" not in src[line_start:m.start()]:
            yield m.start()


@pytest.mark.parametrize("name", BOTS)
def test_command_poll_parameter_defaults_to_two_seconds(name):
    assert re.search(r'\[Parameter\("Command Poll \(ms, 0=off\)", Group = "[^"]+", DefaultValue = 2000, MinValue = 0\)\]'
                     r'\s*public int CommandPollMs \{ get; set; \}', _src(name))


@pytest.mark.parametrize("name", BOTS)
def test_poll_runs_only_live_and_stops_with_the_bot(name):
    src = _src(name)
    start = _method(src, "private void StartCommandPoll()")
    assert "RunningMode != RunningMode.RealTime" in start and "CommandPollMs <= 0" in start
    assert "new HttpClient { Timeout = TimeSpan.FromSeconds(5) }" in start
    assert "StartCommandPoll();" in _method(src, "protected override void OnStart()")
    assert "StopCommandPoll();" in _method(src, "protected override void OnStop()")


@pytest.mark.parametrize("name", BOTS)
def test_poll_and_result_urls(name):
    src = _src(name)
    assert "/api/cbot/commands?bot_id={Uri.EscapeDataString(BotId)}" in src
    assert "/api/cbot/commands/{Uri.EscapeDataString(commandId)}/result" in src


@pytest.mark.parametrize("name", BOTS)
def test_a_command_id_is_recorded_before_it_runs(name):
    loop = _method(_src(name), "private async Task CommandPollLoopAsync(")
    assert loop.index("_handledCommandIds.Add(cmd.id)") < loop.index("BeginInvokeOnMainThread(() => ExecuteDashboardCommand(")
    assert "_dashboardPaused = poll.paused;" in loop


@pytest.mark.parametrize("name", BOTS)
def test_commands_close_only_the_bots_own_positions_with_a_reason(name):
    src = _src(name)
    body = _method(src, "private void ExecuteDashboardCommand(")
    assert OWN_POSITIONS[name] in body
    assert "CloseWithReason(pos, ManualCloseReason)" in body
    assert 'private const string ManualCloseReason = "Manual close (dashboard)";' in src
    assert "private TradeResult CloseWithReason(Position pos, string reason)" in src


@pytest.mark.parametrize("name", BOTS)
def test_pause_log_is_throttled(name):
    helper = _method(_src(name), "private bool DashboardPauseBlocks(string what)")
    assert "if (!_dashboardPaused) return false;" in helper
    assert "TotalSeconds >= 60" in helper


@pytest.mark.parametrize("name", BOTS)
def test_every_entry_order_is_behind_the_pause_guard(name):
    src = _src(name)
    orders = list(_entry_orders(src))
    assert orders
    previous = -1
    for pos in orders:
        guard = src.rfind("if (DashboardPauseBlocks(", 0, pos)
        assert guard > previous, f"{name}: ExecuteMarketOrder at offset {pos} has no pause guard of its own"
        assert pos - guard < 1500, f"{name}: pause guard too far from the order at offset {pos}"
        previous = pos


def test_judas_command_base_url_prefers_the_dashboard_server():
    body = _method(_src("AsianRangeJudasSweepBot.cs"), "private string BuildCommandBaseUrl()")
    assert body.index("DashboardServerUrl") < body.index("ApiUrl")


def test_judas_drops_a_staged_order_while_paused():
    body = _method(_src("AsianRangeJudasSweepBot.cs"), "private void ExecuteStagedMarketOrder(TradeType tradeType)")
    assert re.search(r"if \(DashboardPauseBlocks\([^)]*\)\)\s*\{\s*ResetStagedOrder\(\);\s*return;", body)


def test_judas_guards_all_six_entry_orders():
    assert len(list(_entry_orders(_src("AsianRangeJudasSweepBot.cs")))) == 6


def test_flowrsi_reports_the_manual_close_reason():
    body = _method(_src("FlowRsiBot.cs"), "private void OnPositionClosed(PositionClosedEventArgs args)")
    assert "_closeReasons.TryGetValue(pos.Id, out botReason)" in body
    assert "reason = hasBotReason ? botReason :" in body
