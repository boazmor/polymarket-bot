#!/bin/bash
# Health check + auto-fix for all Polymarket recorders.
# Writes status to /root/recorder_health.log (replaces last run).
# When a recorder is stale/missing data, it is automatically restarted.
# Cron: twice daily.

OUT=/root/recorder_health.log
NOW=$(date +%s)
NOW_STR=$(date +"%Y-%m-%d %H:%M:%S UTC")
HOST=$(hostname)

restart_helsinki() {
  local coin_lower="$1"
  local coin_upper=$(echo "$coin_lower" | tr a-z A-Z)
  screen -X -S "rec_${coin_lower}" quit 2>/dev/null
  pkill -f "MULTI_COIN_RECORDER.*--coin ${coin_upper}.*--window 5m" 2>/dev/null
  sleep 1
  screen -dmS "rec_${coin_lower}" bash -c "cd /root/research/multi_coin && python3 MULTI_COIN_RECORDER.py --coin ${coin_upper} --window 5m > /root/rec_${coin_lower}.log 2>&1"
  echo "  RESTARTED: rec_${coin_lower}"
}

restart_germany() {
  local coin_lower="$1"
  local window="$2"
  local coin_upper=$(echo "$coin_lower" | tr a-z A-Z)
  screen -X -S "rec_${coin_lower}_${window}" quit 2>/dev/null
  pkill -f "MULTI_COIN_RECORDER.*--coin ${coin_upper}.*--window ${window}" 2>/dev/null
  sleep 1
  screen -dmS "rec_${coin_lower}_${window}" bash -c "cd /root/research/multi_coin && python3 MULTI_COIN_RECORDER.py --coin ${coin_upper} --window ${window} --data-dir /root/data_${coin_lower}_${window}_research > /root/rec_${coin_lower}_${window}.log 2>&1"
  echo "  RESTARTED: rec_${coin_lower}_${window}"
}

restart_kalshi() {
  local coin_lower="$1"
  local coin_upper=$(echo "$coin_lower" | tr a-z A-Z)
  screen -X -S "kalshi_${coin_lower}" quit 2>/dev/null
  pkill -f "KALSHI_RECORDER.*--coin ${coin_upper}" 2>/dev/null
  sleep 1
  screen -dmS "kalshi_${coin_lower}" bash -c "cd /root/research/multi_coin && python3 KALSHI_RECORDER.py --coin ${coin_upper} > /root/kalshi_${coin_lower}.log 2>&1"
  echo "  RESTARTED: kalshi_${coin_lower}"
}

restart_gemini() {
  local coin_lower="$1"
  local coin_upper=$(echo "$coin_lower" | tr a-z A-Z)
  screen -X -S "rec_gemini_${coin_lower}_5m" quit 2>/dev/null
  pkill -f "GEMINI_RECORDER_5M.*--coin ${coin_upper}" 2>/dev/null
  sleep 1
  screen -dmS "rec_gemini_${coin_lower}_5m" bash -c "cd /root && python3 GEMINI_RECORDER_5M.py --coin ${coin_upper} --window 5m --data-dir /root/data_gemini_${coin_lower}_5m > /root/rec_gemini_${coin_lower}_5m.log 2>&1"
  echo "  RESTARTED: rec_gemini_${coin_lower}_5m"
}

restart_kalshi_new() {
  local coin_lower="$1"
  local coin_upper=$(echo "$coin_lower" | tr a-z A-Z)
  screen -X -S "rec_kalshi_${coin_lower}_15m" quit 2>/dev/null
  pkill -f "KALSHI_RECORDER_15M.*--coin ${coin_upper}" 2>/dev/null
  sleep 1
  screen -dmS "rec_kalshi_${coin_lower}_15m" bash -c "cd /root && python3 KALSHI_RECORDER_15M.py --coin ${coin_upper} --window 15m --data-dir /root/data_kalshi_${coin_lower}_15m > /root/rec_kalshi_${coin_lower}_15m.log 2>&1"
  echo "  RESTARTED: rec_kalshi_${coin_lower}_15m"
}


{
  echo "=========================================="
  echo "RECORDER HEALTH CHECK + AUTO-FIX — $NOW_STR"
  echo "=========================================="
  echo

  PROBLEMS=0
  FIXED=0

  check_and_fix() {
    local label="$1"
    local file="$2"
    local stale_sec="$3"
    local fix_kind="$4"  # helsinki | germany | kalshi
    local coin="$5"
    local window="$6"
    local broken=0

    if [ ! -f "$file" ]; then
      echo "[MISSING] $label — file not found"
      broken=1
    else
      local mtime=$(stat -c %Y "$file" 2>/dev/null)
      local age=$((NOW - mtime))
      local lines=$(wc -l < "$file" 2>/dev/null)
      if [ "$age" -gt "$stale_sec" ]; then
        echo "[STALE] $label — $lines lines, last write ${age}s ago (threshold ${stale_sec}s)"
        broken=1
      else
        # Check non-zero up_ask in last 100 rows (only for combined_per_second files)
        local f_combined="$(dirname $file)/combined_per_second.csv"
        if [ -f "$f_combined" ] && [ "$fix_kind" != "kalshi" ] && [ "$fix_kind" != "kalshi_new" ] && [ "$fix_kind" != "gemini_btc" ]; then
          local hits=$(tail -100 "$f_combined" 2>/dev/null | awk -F, '$17 != "" && $17 != 0 { n++ } END { print n+0 }')
          if [ "$hits" -lt 1 ]; then
            echo "[NO_ASK] $label — last 100 rows all empty/zero up_ask"
            broken=1
          else
            echo "[OK]    $label — $lines lines, age ${age}s"
          fi
        else
          echo "[OK]    $label — $lines lines, age ${age}s"
        fi
      fi
    fi

    if [ "$broken" = "1" ]; then
      PROBLEMS=$((PROBLEMS + 1))
      case "$fix_kind" in
        helsinki) restart_helsinki "$coin"; FIXED=$((FIXED + 1)) ;;
        germany)  restart_germany "$coin" "$window"; FIXED=$((FIXED + 1)) ;;
        kalshi)   restart_kalshi "$coin"; FIXED=$((FIXED + 1)) ;;
        predict_btc_5m)
          screen -X -S rec_predict_btc_5m quit 2>/dev/null
          pkill -f "PREDICT_RECORDER_5M" 2>/dev/null
          sleep 1
          screen -dmS rec_predict_btc_5m bash -c "cd /root && python3 PREDICT_RECORDER_5M.py --data-dir /root/data_predict_btc_5m > /root/rec_predict_btc_5m.log 2>&1"
          echo "  RESTARTED: rec_predict_btc_5m"
          FIXED=$((FIXED + 1))
          ;;
        gemini_btc)
          restart_gemini "$coin"; FIXED=$((FIXED + 1)) ;;
        kalshi_new)
          restart_kalshi_new "$coin"; FIXED=$((FIXED + 1)) ;;
        limitless_multi)
          coin_upper=$(echo "$coin" | tr a-z A-Z)
          screen -X -S "rec_limitless_${coin}_5m" quit 2>/dev/null
          pkill -f "LIMITLESS_RECORDER_WS.*--coin ${coin_upper}" 2>/dev/null
          sleep 1
          screen -dmS "rec_limitless_${coin}_5m" bash -c "cd /root && python3 LIMITLESS_RECORDER_WS.py --coin ${coin_upper} --window 5m --data-dir /root/data_limitless_${coin}_5m > /root/rec_limitless_${coin}_5m.log 2>&1"
          echo "  RESTARTED: rec_limitless_${coin}_5m"
          FIXED=$((FIXED + 1))
          ;;
      esac
    fi
  }

  if [ "$HOST" = "bot-helsinki" ]; then
    BASE=/root/research/multi_coin
    echo "--- Helsinki 5m recorders ---"
    for c in btc eth sol xrp doge bnb hype; do
      d=$BASE/data_${c}_5m_research
      check_and_fix "${c}_5m" "$d/combined_per_second.csv" 1800 helsinki "$c" "5m"
    done
    echo "--- Helsinki Predict.fun recorder (BTC only) ---"
    check_and_fix "predict_btc_5m" "/root/data_predict_btc_5m/combined_per_second.csv" 1800 predict_btc_5m "btc" "5m"
    echo "--- Helsinki Limitless recorders (BTC ETH SOL XRP DOGE) ---"
    for c in btc eth sol xrp doge; do
      check_and_fix "limitless_${c}_5m" "/root/data_limitless_${c}_5m/combined_per_second.csv" 1800 limitless_multi "$c" "5m"
    done
    echo "--- Helsinki Gemini 5m recorder (BTC only) ---"
    check_and_fix "gemini_btc_5m" "/root/data_gemini_btc_5m/combined_per_second.csv" 3600 gemini_btc "btc" "5m"
    echo "--- Helsinki Kalshi 15m recorder (BTC only) ---"
    check_and_fix "kalshi_btc_15m" "/root/data_kalshi_btc_15m/combined_per_second.csv" 1800 kalshi_new "btc" "15m"
  else
    echo "--- Germany multi-window recorders ---"
    for c in btc eth sol xrp doge bnb hype; do
      for w in 15m 1h 4h 1d; do
        case "$w" in
          15m) thr=900   ;;
          1h)  thr=3600  ;;
          4h)  thr=14400 ;;
          1d)  thr=86400 ;;
        esac
        d=/root/data_${c}_${w}_research
        check_and_fix "${c}_${w}" "$d/poly_book_ticks.csv" $thr germany "$c" "$w"
      done
    done
    echo
    echo "--- Germany Kalshi 15m recorders ---"
    for c in btc eth sol xrp doge bnb hype; do
      d=/root/data_kalshi_${c}_15m
      check_and_fix "kalshi_${c}" "$d/combined_per_second.csv" 900 kalshi "$c" "15m"
    done
  fi

  echo
  echo "=========================================="
  echo "PROBLEMS FOUND: $PROBLEMS"
  echo "AUTO-RESTARTED: $FIXED"
  echo "=========================================="
} > $OUT 2>&1

if [ -t 1 ]; then
  cat $OUT
fi
