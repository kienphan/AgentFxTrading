"""
System prompt for autonomous TMS trading agent.

The agent will:
1. Analyze TMS indicators
2. Make trading decisions
3. Output executable orders (entry, SL, TP)
"""

SYSTEM_PROMPT = """You are an AUTONOMOUS trading agent using TMS for BIAS and ORB for ENTRY.

## Strategy Logic

**TMS (H4) = DIRECTIONAL BIAS**
- Determines overall trend direction (bullish/bearish/neutral)
- Based on: TDI Green vs Red position, HA trend, Stochastic direction

**ORB (M15) = ENTRY TRIGGER**
- Only enter when ORB breaks in direction of TMS bias
- ORB breakout is the actual entry signal

## Entry Rules (MUST follow this flow)

### Step 1: Determine TMS Bias (H4)
- **BULLISH bias**: TDI Green > Red, HA green, Stoch K > D
- **BEARISH bias**: TDI Green < Red, HA red, Stoch K < D
- **NEUTRAL**: Mixed signals, no clear trend → NO TRADE

### Step 2: Wait for ORB Breakout (M15)
- **Only enter if ORB breaks in direction of TMS bias**
- BULLISH bias + ORB upside breakout → BUY
- BEARISH bias + ORB downside breakout → SELL
- Any mismatch → HOLD (no trade)

### Entry Examples:
✅ TMS BULLISH + ORB breaks UP → BUY
✅ TMS BEARISH + ORB breaks DOWN → SELL
❌ TMS BULLISH + ORB breaks DOWN → HOLD (counter-trend)
❌ TMS BEARISH + ORB breaks UP → HOLD (counter-trend)
❌ TMS NEUTRAL + any ORB → HOLD (no bias)

## Exit Conditions (from TMS)
- TDI Green goes flat (horizontal)
- TDI Green hooks back (reverses)
- TDI Green forms checkmark (V-shape)

## Risk Management (CRITICAL)

You MUST provide:
- **entry**: ORB breakout level or current price
- **sl**: Stop loss (MANDATORY) — place at OR opposite side or recent swing
- **tp**: Take profit — minimum R:R 1.5:1

## Output Format (MANDATORY)

```json
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0 to 1.0,
  "reason": "Explain TMS bias + ORB breakout alignment",
  "entry": price,
  "sl": price,
  "tp": price,
  "tms_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "orb_breakout": "UP" | "DOWN" | "NONE",
  "alignment": "ALIGNED" | "MISMATCH" | "NO_BIAS"
}
```

## Critical Rules

1. **NEVER trade against TMS bias** — ORB must confirm TMS direction
2. **NEVER trade without SL**
3. **NEVER trade if TMS is NEUTRAL** — wait for clear bias
4. **Only trade if confidence >= 0.7**
5. **R:R must be >= 1.5**

## Decision Flow

1. What is TMS bias? (BULLISH/BEARISH/NEUTRAL)
2. Is there ORB breakout? (UP/DOWN/NONE)
3. Are they aligned? (both bullish or both bearish)
4. If aligned → calculate entry/SL/TP
5. If not aligned → HOLD

Analyze the snapshot and output your decision.
"""


def build_user_prompt(snapshot: dict) -> str:
    """Build user prompt from TMS snapshot."""
    import json

    lines = [
        "## TMS Market Snapshot",
        "",
        f"**Symbol**: {snapshot.get('symbol', 'N/A')}",
        f"**Timeframe**: {snapshot.get('timeframe', 'N/A')}",
        f"**Time**: {snapshot.get('timestamp', 'N/A')}",
        "",
        "### Price",
        f"- Current: {snapshot.get('price', 'N/A')}",
        "",
        "### Account",
    ]

    account = snapshot.get("account", {})
    if account:
        lines.append(f"- Balance: {account.get('balance', 'N/A')}")
        lines.append(f"- Equity: {account.get('equity', 'N/A')}")
        lines.append(f"- Free Margin: {account.get('free_margin', 'N/A')}")
    else:
        lines.append("- No account data available")

    lines.append("")
    lines.append("### TMS BIAS (H4) — Directional Bias")

    bias = snapshot.get("bias", "NEUTRAL")
    if bias == "BULLISH":
        lines.append("- 🟢 **TMS BIAS: BULLISH** (look for ORB upside breakout)")
    elif bias == "BEARISH":
        lines.append("- 🔴 **TMS BIAS: BEARISH** (look for ORB downside breakout)")
    else:
        lines.append("- ⚪ **TMS BIAS: NEUTRAL** (no trade — wait for clear bias)")

    lines.append("")
    lines.append("### Heiken Ashi")

    ha = snapshot.get("ha", {})
    lines.append(f"- Color: {ha.get('color', 'N/A')}")
    lines.append(f"- Trend: {ha.get('trend', 'N/A')}")
    lines.append(f"- Open: {ha.get('open', 'N/A')}")
    lines.append(f"- Close: {ha.get('close', 'N/A')}")
    if ha.get("turned_green"):
        lines.append("- ⚡ **Turned GREEN** (bullish reversal)")
    if ha.get("turned_red"):
        lines.append("- ⚡ **Turned RED** (bearish reversal)")

    lines.append("")
    lines.append("### TDI (Traders Dynamic Index)")

    tdi = snapshot.get("tdi", {})
    lines.append(f"- Green (RSI): {tdi.get('green', 'N/A')}")
    lines.append(f"- Red (Signal): {tdi.get('red', 'N/A')}")
    lines.append(f"- Level: {tdi.get('level', 'N/A')}")

    if tdi.get("cross_up"):
        lines.append("- 🔼 **GREEN CROSSED ABOVE RED** (potential LONG)")
    if tdi.get("cross_down"):
        lines.append("- 🔽 **GREEN CROSSED BELOW RED** (potential SHORT)")

    lines.append(f"- Bars since cross: {tdi.get('bars_since_cross', 'N/A')}")
    lines.append(f"- Cross direction: {tdi.get('cross_direction', 'N/A')}")

    lines.append("")
    lines.append("### Stochastic")

    stoch = snapshot.get("stoch", {})
    lines.append(f"- %K: {stoch.get('k', 'N/A')}")
    lines.append(f"- %D: {stoch.get('d', 'N/A')}")
    if stoch.get("bullish"):
        lines.append("- ✅ Bullish confirmation (K > D)")
    if stoch.get("bearish"):
        lines.append("- ✅ Bearish confirmation (K < D)")

    # ORB section
    orb = snapshot.get("orb", {})
    if orb and orb.get("ok"):
        lines.append("")
        lines.append("### ORB (Opening Range Breakout)")
        lines.append(f"- Session: {orb.get('session', 'N/A')}")
        lines.append(f"- OR High: {orb.get('or_high', 'N/A')}")
        lines.append(f"- OR Low: {orb.get('or_low', 'N/A')}")
        lines.append(f"- OR Width: {orb.get('or_width', 'N/A')}")
        lines.append(f"- OR Complete: {orb.get('or_complete', False)}")
        
        if orb.get("breakout"):
            direction = orb.get("breakout_direction", "N/A")
            lines.append(f"- 🔥 **BREAKOUT: {direction.upper()}**")
            lines.append(f"- Breakout price: {orb.get('breakout_price', 'N/A')}")
            lines.append(f"- Bars since breakout: {orb.get('bars_since_breakout', 'N/A')}")
            lines.append(f"- In entry window: {orb.get('in_entry_window', False)}")
        else:
            lines.append("- No breakout yet")
        
        lines.append(f"- Price position: {orb.get('price_position', 'N/A')}")

    lines.append("")
    lines.append("### Signal Summary")

    signal = snapshot.get("signal", {})
    if signal.get("long_entry"):
        lines.append("- 🟢 **LONG ENTRY SIGNAL DETECTED**")
    if signal.get("short_entry"):
        lines.append("- 🔴 **SHORT ENTRY SIGNAL DETECTED**")
    if signal.get("exit_long"):
        lines.append("- ⚠️ **EXIT LONG signal** (TDI flat/hook/checkmark)")
    if signal.get("exit_short"):
        lines.append("- ⚠️ **EXIT SHORT signal** (TDI flat/hook/checkmark)")

    lines.append(f"- Angle OK (long): {signal.get('angle_ok_long', 'N/A')}")
    lines.append(f"- Angle OK (short): {signal.get('angle_ok_short', 'N/A')}")

    # Current action recommendation from TMS
    action = snapshot.get("action", "WAIT")
    lines.append("")
    lines.append(f"### TMS System Recommendation: **{action}**")

    # Open positions
    positions = snapshot.get("positions", [])
    if positions:
        lines.append("")
        lines.append(f"### Open Positions ({len(positions)})")
        for pos in positions:
            lines.append(
                f"- {pos.get('symbol', 'N/A')}: {pos.get('side', 'N/A')} "
                f"{pos.get('volume', 'N/A')} lots @ {pos.get('entry_price', 'N/A')} "
                f"(P&L: {pos.get('profit', 'N/A')})"
            )
    else:
        lines.append("")
        lines.append("### Open Positions: None")

    # Portfolio state
    portfolio = snapshot.get("portfolio", {})
    if portfolio:
        lines.append("")
        lines.append("### Portfolio Risk Status")
        lines.append(f"- Total Heat (risk): {portfolio.get('heat_pct', 0):.2f}% of equity")
        lines.append(f"- Margin Usage: {portfolio.get('margin_usage_pct', 0):.2f}%")
        lines.append(f"- Currency Exposure: {portfolio.get('currency_exposure', {})}")

        warnings = portfolio.get("correlation_warnings", [])
        if warnings:
            lines.append("")
            lines.append("**⚠️ Correlation Warnings:**")
            for w in warnings:
                lines.append(f"- {w}")

    # Market anomaly (AI-based news detection)
    anomaly = snapshot.get("anomaly", {})
    if anomaly and anomaly.get("detected"):
        lines.append("")
        lines.append("### ⚠️ MARKET ANOMALY DETECTED")
        lines.append(f"- **Type**: {anomaly.get('anomaly_type', 'unknown')}")
        lines.append(f"- **Severity**: {anomaly.get('severity', 'unknown')}")
        lines.append(f"- **Details**: {anomaly.get('details', '')}")
        lines.append(f"- **Confidence**: {anomaly.get('confidence', 0):.0%}")
        lines.append("")
        lines.append("**This likely indicates a news event. Consider avoiding trades or reducing position size.**")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## YOUR TASK")
    lines.append("")
    lines.append("Follow this decision flow:")
    lines.append("")
    lines.append("1. **Check TMS BIAS** (shown above)")
    lines.append("   - If NEUTRAL → output HOLD (no trade)")
    lines.append("   - If BULLISH → only consider BUY if ORB breaks UP")
    lines.append("   - If BEARISH → only consider SELL if ORB breaks DOWN")
    lines.append("")
    lines.append("2. **Check ORB BREAKOUT**")
    lines.append("   - Is there a breakout? (UP/DOWN/NONE)")
    lines.append("   - Is it aligned with TMS bias?")
    lines.append("")
    lines.append("3. **Decision:**")
    lines.append("   - TMS BULLISH + ORB UP → BUY")
    lines.append("   - TMS BEARISH + ORB DOWN → SELL")
    lines.append("   - Any mismatch or NEUTRAL → HOLD")
    lines.append("")
    lines.append("**Remember:**")
    lines.append("- NEVER trade against TMS bias")
    lines.append("- NEVER trade if TMS is NEUTRAL")
    lines.append("- SL is MANDATORY")
    lines.append("- R:R must be >= 1.5")
    lines.append("- Confidence >= 0.7")
    lines.append("")
    lines.append("Output your decision as JSON:")

    return "\n".join(lines)
