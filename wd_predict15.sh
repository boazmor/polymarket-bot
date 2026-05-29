#!/bin/bash
# Dedicated watchdog for the flaky predict_15m recorder (WS churn).
# pred15 supplies the 15m strike that LIVE5 needs for its target-sim check,
# so if it stalls the bot cannot trade. Restart if stale > 120s.
F=/root/data_predict_btc_15m/combined_per_second.csv
NOW=$(date +%s)
MT=$(stat -c %Y "$F" 2>/dev/null || echo 0)
AGE=$((NOW-MT))
if [ "$AGE" -gt 120 ]; then
  echo "$(date -u +%FT%TZ) STALE age=${AGE}s -> restart" >> /root/wd_predict15.log
  pkill -f '[P]REDICT_RECORDER_15M_V2'
  sleep 2
  screen -dmS rec_predict_btc_15m bash -c 'cd /root && python3 -u PREDICT_RECORDER_15M_V2.py --data-dir /root/data_predict_btc_15m > /root/rec_predict_btc_15m.log 2>&1'
fi
