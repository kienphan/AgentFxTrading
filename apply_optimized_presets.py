import sqlite3
import re
import subprocess
import os

# New preset values: (MinOrWidthPips, MinDecisiveBreakoutPips, OrbBufferPips)
PRESETS = {
    "cbot-usdjpy": {"MinOrWidthPips": 5.0, "MinDecisiveBreakoutPips": 3.0, "OrbBufferPips": 1.0},
    "cbot-audjpy": {"MinOrWidthPips": 5.0, "MinDecisiveBreakoutPips": 3.0, "OrbBufferPips": 1.0},
    "cbot-eurusd": {"MinOrWidthPips": 5.0, "MinDecisiveBreakoutPips": 2.5, "OrbBufferPips": 1.0},
    "cbot-gbpusd": {"MinOrWidthPips": 7.0, "MinDecisiveBreakoutPips": 3.5, "OrbBufferPips": 1.2},
    "cbot-eurjpy": {"MinOrWidthPips": 8.0, "MinDecisiveBreakoutPips": 4.0, "OrbBufferPips": 1.2},
    "cbot-gbpjpy": {"MinOrWidthPips": 10.0, "MinDecisiveBreakoutPips": 5.0, "OrbBufferPips": 1.5},
    "cbot-usdcad": {"MinOrWidthPips": 7.0, "MinDecisiveBreakoutPips": 3.5, "OrbBufferPips": 1.2},
    "cbot-xauusd": {"MinOrWidthPips": 250.0, "MinDecisiveBreakoutPips": 100.0, "OrbBufferPips": 30.0},
    "cbot-de40":   {"MinOrWidthPips": 200.0, "MinDecisiveBreakoutPips": 70.0,  "OrbBufferPips": 35.0},
    "cbot-us30":   {"MinOrWidthPips": 300.0, "MinDecisiveBreakoutPips": 100.0, "OrbBufferPips": 50.0},
    "cbot-ustec":  {"MinOrWidthPips": 250.0, "MinDecisiveBreakoutPips": 80.0,  "OrbBufferPips": 40.0},
    "cbot-btcusd": {"MinOrWidthPips": 300.0, "MinDecisiveBreakoutPips": 150.0, "OrbBufferPips": 50.0},
    "cbot-ethusd": {"MinOrWidthPips": 150.0, "MinDecisiveBreakoutPips": 80.0,  "OrbBufferPips": 25.0},
}

conn = sqlite3.connect("portfolio.db")
c = conn.cursor()
c.execute("SELECT name, run_command FROM cbot_configs")
bots = c.fetchall()

for bot_name, cmd in bots:
    if bot_name in PRESETS:
        p = PRESETS[bot_name]
        new_cmd = cmd
        new_cmd = re.sub(r'--MinOrWidthPips=[0-9.]+', f'--MinOrWidthPips={p["MinOrWidthPips"]}', new_cmd)
        new_cmd = re.sub(r'--MinDecisiveBreakoutPips=[0-9.]+', f'--MinDecisiveBreakoutPips={p["MinDecisiveBreakoutPips"]}', new_cmd)
        new_cmd = re.sub(r'--OrbBufferPips=[0-9.]+', f'--OrbBufferPips={p["OrbBufferPips"]}', new_cmd)
        
        print(f"Updating {bot_name} in DB...")
        c.execute("UPDATE cbot_configs SET run_command = ? WHERE name = ?", (new_cmd, bot_name))
        
        print(f"Recreating container {bot_name}...")
        subprocess.run(["docker", "stop", bot_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", bot_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(new_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

conn.commit()
conn.close()
print("All bot configurations updated in database and containers recreated successfully!")
