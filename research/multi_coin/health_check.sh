#!/bin/bash
# Watchdog — runs at minute :01, :16, :31, :46 of each hour, just after every
# 15-min market boundary. Verifies all recorders are alive and writing fresh
# data, that Predict isn't stuck on an old market, and that bots on Helsinki
# are running. Auto-fixes by killing and restarting whatever is broken.

LOG=/root/health_check.log
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
NOW=$(date +%s)

echo "=== $TS ===" >> $LOG

restart_recorder() {
    local name=$1
    case $name in
        rec_poly)
            cd /root/research/multi_coin && screen -dmS rec_poly bash -c 'python3 MULTI_COIN_RECORDER.py --coin BTC --window 15m --data-dir /root/data_btc_15m_research > /root/rec_poly.log 2>&1'
            ;;
        rec_kalshi)
            cd /root/research/multi_coin && screen -dmS rec_kalshi bash -c 'python3 KALSHI_RECORDER.py --coin BTC --data-dir /root/data_kalshi_btc_15m > /root/rec_kalshi.log 2>&1'
            ;;
        rec_gemini)
            cd /root/research/multi_coin && screen -dmS rec_gemini bash -c 'python3 GEMINI_RECORDER.py --coin BTC --data-dir /root/data_gemini_btc_15m > /root/rec_gemini.log 2>&1'
            ;;
        rec_predict)
            cd /root && screen -dmS rec_predict bash -c 'python3 PREDICT_RECORDER.py --data-dir /root/data_predict_btc_15m > /root/rec_predict.log 2>&1'
            ;;
        sync_helsinki)
            screen -dmS sync_helsinki bash -c '/root/sync_to_helsinki.sh > /root/sync_helsinki.log 2>&1'
            ;;
    esac
}

# 1. Verify each screen is alive
for s in rec_poly rec_kalshi rec_gemini rec_predict sync_helsinki; do
    if ! screen -ls | grep -q "\.$s\b"; then
        echo "  $s DOWN — restarting" >> $LOG
        restart_recorder $s
    fi
done

# 2. Check Predict isn't stuck on an old market (sec_from_open > 1200)
PREDICT_FILE=/root/data_predict_btc_15m/combined_per_second.csv
if [ -f "$PREDICT_FILE" ]; then
    LAST_ROW=$(tail -1 "$PREDICT_FILE")
    if [ -n "$LAST_ROW" ]; then
        SEC=$(echo "$LAST_ROW" | awk -F',' '{print $5}')
        if [[ "$SEC" =~ ^[0-9]+$ ]] && [ "$SEC" -gt 1200 ]; then
            echo "  Predict STUCK sec_from_open=$SEC — wipe + restart" >> $LOG
            screen -S rec_predict -X quit 2>/dev/null
            pkill -9 -f PREDICT_RECORDER 2>/dev/null
            sleep 3
            rm -rf /root/data_predict_btc_15m
            restart_recorder rec_predict
        fi
    fi
fi

# 3. Check each recorder is writing fresh data (mtime within 90 sec)
for d in btc_15m_research kalshi_btc_15m gemini_btc_15m predict_btc_15m; do
    FILE=/root/data_$d/combined_per_second.csv
    if [ -f "$FILE" ]; then
        MTIME=$(stat -c %Y "$FILE")
        AGE=$((NOW - MTIME))
        if [ $AGE -gt 90 ]; then
            echo "  $d STALE age=${AGE}s — restart" >> $LOG
            case $d in
                btc_15m_research)   r=rec_poly ;;
                kalshi_btc_15m)     r=rec_kalshi ;;
                gemini_btc_15m)     r=rec_gemini ;;
                predict_btc_15m)    r=rec_predict ;;
            esac
            screen -S $r -X quit 2>/dev/null
            sleep 2
            restart_recorder $r
        fi
    fi
done

# 4. Helsinki bots — auto-restart any bot that's down. Bots persist OPEN_TRADES
# state to JSON so restart preserves positions.
HELSINKI_STATUS=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 helsinki "
for spec in 'arb_v4_4way:arb_v4_4way.py' 'arb_v5_amiti:arb_v5_amiti.py' 'arb_v5_4s:arb_v5_poly_predict_4seasons.py' 'arb_v5_basic:arb_virtual_bot_v5.py'; do
    s=\${spec%:*}; py=\${spec#*:}
    if ! screen -ls | grep -q \"\\.\$s\\b\"; then
        echo \"  HELSINKI \$s DOWN — restarting\"
        cd /root && screen -dmS \$s bash -c \"python3 /root/\$py > /root/\$s.log 2>&1\"
    fi
done
" 2>&1)
if [ -n "$HELSINKI_STATUS" ]; then
    echo "$HELSINKI_STATUS" >> $LOG
fi

echo "  done" >> $LOG
