#!/bin/bash
# start_bot_helsinki.sh — start the LIVE trading bot in a screen session

# kill old session if exists
screen -S bot_btc -X quit 2>/dev/null || true
sleep 1

# start in screen, auto-confirm "go live" via stdin
cd /root/live/btc_5m
screen -dmS bot_btc bash -c "echo 'go live' | python3 LIVE_BTC_5M_V1_TEST5.py --live > /root/bot_btc.log 2>&1"

sleep 5
echo "=== screen sessions ==="
screen -ls
echo ""
echo "=== first 30 lines of bot log ==="
head -30 /root/bot_btc.log
