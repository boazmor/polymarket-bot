#!/bin/bash
# start_germany_4windows.sh — kill duplicate 5m recorders, start 7×4=28 new
# recorders for windows 15m, 1h, 4h, 1d.

set -e

# 1. kill old 5m recorders (duplicates of Helsinki)
echo "=== killing old 5m recorder screens ==="
for c in btc eth sol xrp doge bnb hype; do
    if screen -S "rec_${c}" -X quit 2>/dev/null; then
        echo "  killed: rec_${c}"
    fi
done
sleep 2

# 2. start 28 new recorders
echo ""
echo "=== starting 28 new recorders (4 windows × 7 coins) ==="
cd /root/research/multi_coin
for COIN in BTC ETH SOL XRP DOGE BNB HYPE; do
    LOWER=$(echo "$COIN" | tr A-Z a-z)
    for WIN in 15m 1h 4h 1d; do
        SESSION="rec_${LOWER}_${WIN}"
        LOG="/root/rec_${LOWER}_${WIN}.log"
        # kill old session if exists
        screen -S "$SESSION" -X quit 2>/dev/null || true
        # start new
        screen -dmS "$SESSION" bash -c "cd /root/research/multi_coin && python3 MULTI_COIN_RECORDER.py --coin $COIN --window $WIN --data-dir /root/data_${LOWER}_${WIN}_research > $LOG 2>&1"
        echo "  started: $SESSION"
    done
done

# 3. verify
sleep 5
echo ""
echo "=== screen sessions running ==="
screen -ls | head -35
echo ""
echo "=== sample log lines (BTC 15m) ==="
sleep 3
tail -15 /root/rec_btc_15m.log 2>/dev/null
