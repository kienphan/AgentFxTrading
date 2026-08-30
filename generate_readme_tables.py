import sqlite3
import re

conn = sqlite3.connect("portfolio.db")
c = conn.cursor()
c.execute("SELECT name, run_command FROM cbot_configs")
bots = {row[0].replace('cbot-', '').upper(): row[1] for row in c.fetchall()}
conn.close()

groups = {
    "Cryptocurrency": ["BTCUSD", "ETHUSD"],
    "Metals & Indices": ["XAUUSD", "US30", "USTEC", "DE40"],
    "Forex Majors": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD"],
    "Forex Crosses": ["GBPJPY", "EURJPY", "AUDJPY"]
}

params = [
    ("SessionName", "Trading Session"),
    ("SessionDstRule", "DST Rule"),
    ("MinDecisiveBreakoutPips", "Min Decisive Breakout"),
    ("MinOrWidthPips", "Min OR Width"),
    ("OrbBufferPips", "ORB Buffer"),
    ("BreakevenTriggerAtr", "Breakeven Trigger"),
    ("BreakevenOffsetAtr", "Breakeven Offset"),
    ("TrailTriggerAtr", "Trail Trigger"),
    ("TrailDistanceAtr", "Trail Distance"),
    ("MinSlAtr", "Min SL / Max SL"),
    ("MaxSlAtr", "Max SL"),
    ("MinTpAtr", "Min TP / Max TP"),
    ("MaxTpAtr", "Max TP"),
    ("MaxGivebackAtr", "Max Giveback"),
    ("period", "Recommended Timeframe"),
    ("EmaPeriod", "EMA Period"),
    ("PostTpPullbackAtr", "Post-TP Gate / Pullback"),
    ("BounceDistanceThreshold", "TDI Bounce Trade"),
    ("PartialCloseRatio", "Partial Close at BE"),
    ("RiskPerTradePercent", "Risk per Trade"),
    ("UseAtr", "Use ATR for SL/TP"),
    ("AtrPeriod", "ATR Period"),
    ("AtrSlMultiplier", "ATR SL Multiplier"),
    ("AtrTpMultiplier", "ATR TP Multiplier")
]

def extract(cmd, param):
    if param == "period":
        match = re.search(r'--period=([a-zA-Z0-9]+)', cmd)
    else:
        match = re.search(rf'--{param}="?([a-zA-Z0-9.\-_]+)"?', cmd)
    return match.group(1) if match else "N/A"

def format_val(val, param):
    if val == "N/A": return "`N/A`"
    if "Pips" in param:
        return f"`{val} pips`"
    if param in ("UseAtr", "AtrPeriod"):
        return f"`{val}`"
    if "Atr" in param:
        return f"`{val}x ATR`"
        return f"`true` (`{val}`)"
    if param == "PartialCloseRatio":
        return f"`{val} ({int(float(val)*100)}%)`"
    if param == "RiskPerTradePercent":
        return f"`{val}%`"
    if param == "SessionName":
        if val == "newyork": return "New York"
        if val == "newyork_index": return "New York (Index)"
        if val == "london": return "London"
        if val == "tokyo": return "Tokyo"
        return val.capitalize()
    if param == "period":
        return f"`{val.upper()}`"
    return f"`{val}`"

output = "### 📊 Recommended Presets by Symbol\n\n"

for group_name, symbols in groups.items():
    output += f"#### {group_name}\n\n"
    output += "| Parameter | " + " | ".join(symbols) + " |\n"
    output += "| :--- | " + " | ".join([":---"] * len(symbols)) + " |\n"
    
    # Combined SL/TP rows
    for p_key, p_label in params:
        if p_key in ["MaxSlAtr", "MaxTpAtr"]: continue
        
        row = f"| **{p_label}** |"
        for sym in symbols:
            cmd = bots.get(sym, "")
            if not cmd:
                row += " N/A |"
                continue
                
            if p_key == "MinSlAtr":
                min_sl = extract(cmd, "MinSlAtr")
                max_sl = extract(cmd, "MaxSlAtr")
                row += f" `{min_sl}x / {max_sl}x ATR` |"
            elif p_key == "MinTpAtr":
                min_tp = extract(cmd, "MinTpAtr")
                max_tp = extract(cmd, "MaxTpAtr")
                row += f" `{min_tp}x / {max_tp}x ATR` |"
            elif p_key == "PostTpPullbackAtr":
                val = extract(cmd, p_key)
                row += f" `true` (`{val}x ATR`) |"
            elif p_key == "SessionName":
                sess = extract(cmd, "SessionName")
                dst = extract(cmd, "SessionDstRule")
                sess_f = format_val(sess, "SessionName")
                row += f" {sess_f} |"
            elif p_key == "SessionDstRule":
                val = extract(cmd, p_key)
                row += f" `{val}` |"
            else:
                val = extract(cmd, p_key)
                row += f" {format_val(val, p_key)} |"
        output += row + "\n"
    output += "\n"

with open("tables.md", "w") as f:
    f.write(output)
print("Updated tables.md")
