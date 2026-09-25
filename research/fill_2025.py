"""Queue the cTrader backtests that fill the 2025 tick cache for the 8 pairs that lack it.
Live presets, $2000, ticks. Run on the VPS:  ssh forge@204.168.144.232 'python3 -' < research/fill_2025.py"""
import json
import urllib.request

API = "http://127.0.0.1:8000/api/backtests"
PAIRS = ["BTCUSD", "DE40", "ETHUSD", "UK100", "US30", "USDCAD", "USTEC", "XAUUSD"]

for symbol in PAIRS:
    body = {"bot_name": f"cbot-demo-demo-{symbol.lower()}-all-flowrsi", "start": "2025-01-01",
            "end": "2025-12-31", "data_mode": "ticks", "balance": 2000.0,
            "note": "cache fill 2025", "overrides": {}}
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        print(resp.status, resp.read().decode(), symbol)
