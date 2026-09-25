#!/bin/bash
# Copy the cTrader backtest cache (ticks + m15 bars) from the VPS into research/data/<SYMBOL>/.
# Root-owned on the VPS, so tar runs under sudo; paths are spelled out (a remote glob expands as forge).
set -euo pipefail
VPS=forge@204.168.144.232
CACHE=/var/lib/docker/volumes/agentfx-bt-cache/_data/V1/demo_e5322e87
DEST="$(cd "$(dirname "$0")" && pwd)/data"
SYMBOLS=${*:-"AUDJPY AUDUSD BTCUSD DE40 ETHUSD EURJPY EURUSD GBPJPY GBPUSD UK100 US30 USDCAD USDJPY USTEC XAUUSD"}
for s in $SYMBOLS; do
  mkdir -p "$DEST/$s"
  # tar exits 1 when a running backtest is still writing the cache ("file changed as we read it"): not fatal.
  ssh "$VPS" "sudo -n tar cf - -C $CACHE/$s t1 m15 || [ \$? -eq 1 ]" | tar xf - -C "$DEST/$s"
  echo "$s: $(ls "$DEST/$s/t1" | wc -l | tr -d ' ') tick days"
done
