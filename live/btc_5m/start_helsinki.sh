#!/bin/bash
# start_helsinki.sh — start all bot+recorder screens on Helsinki

set -e

# 1. install screen if missing
which screen >/dev/null 2>&1 || apt-get install -qq -y screen

# 2. start 7 coin recorders, one screen each
cd /root/research/multi_coin
for COIN in BTC ETH SOL XRP DOGE BNB HYPE; do
    LOWER=$(echo "$COIN" | tr A-Z a-z)
    LOG="/root/rec_${LOWER}.log"
    SESSION="rec_${LOWER}"
    # kill old session if exists
    screen -S "$SESSION" -X quit 2>/dev/null || true
    # start new
    screen -dmS "$SESSION" bash -c "cd /root/research/multi_coin && python3 MULTI_COIN_RECORDER.py --coin $COIN > $LOG 2>&1"
    echo "started recorder: $SESSION"
done

# 3. wait a bit for recorders to initialize
sleep 5

# 4. show status
echo ""
echo "=== screen sessions ==="
screen -ls
echo ""
echo "=== first lines from each log ==="
for LOWER in btc eth sol xrp doge bnb hype; do
    echo "--- $LOWER ---"
    head -5 "/root/rec_${LOWER}.log" 2>/dev/null || echo "(no log yet)"
done
