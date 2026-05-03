#!/bin/bash
# start_germany_kalshi.sh — start 7 Kalshi 15-min recorders under screen.
# Side-by-side with the existing Polymarket 15-min recorders.

set -e

cd /root/research/multi_coin

for COIN in BTC ETH SOL XRP DOGE BNB HYPE; do
    LOWER=$(echo "$COIN" | tr A-Z a-z)
    SESSION="kalshi_${LOWER}"
    LOG="/root/kalshi_${LOWER}.log"
    DATA_DIR="/root/data_kalshi_${LOWER}_15m"
    # kill old session if exists
    screen -S "$SESSION" -X quit 2>/dev/null || true
    screen -dmS "$SESSION" bash -c "cd /root/research/multi_coin && python3 KALSHI_RECORDER.py --coin $COIN --data-dir $DATA_DIR > $LOG 2>&1"
    echo "  started: $SESSION"
done

sleep 5
echo ""
echo "=== screen sessions ==="
screen -ls | grep kalshi
echo ""
echo "=== sample BTC log ==="
tail -8 /root/kalshi_btc.log 2>/dev/null
