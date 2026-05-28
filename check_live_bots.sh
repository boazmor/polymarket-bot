#!/bin/bash
# Watchdog for live trading bots.
#   Helsinki: arb_v5_live (15-min markets), arb_v7_live (5-min markets)
#   Hetzner:  arb_v6_live (1-hour markets)
#
# Each bot is checked for:
#   1. Process alive (pgrep -f "python3 -u $script")
#   2. Log file updated within $max_stale_sec
# If dead or stale, kill remnants, archive old log, restart under screen.
#
# Append OK / RESTART events to /root/bot_watchdog.log.
# Pass the server flag (helsinki | hetzner) to control which bots to check.
#
# Usage:  bash /root/check_live_bots.sh helsinki
#         bash /root/check_live_bots.sh hetzner

NOW=$(date +%s)
LOG=/root/bot_watchdog.log
HOST=${1:-helsinki}

check_bot() {
  local name=$1
  local script=$2
  local args=$3
  local logfile=$4
  local max_stale_sec=$5

  # Stop file convention: when the bot writes /root/<script-without-.py>.stopped
  # (e.g. arb_v5_3way_live.stopped) the watchdog must leave it alone. The bot
  # writes this when stop-on-loss triggers; resume by deleting the file.
  local stop_file="/root/${script%.py}.stopped"
  if [ -f "$stop_file" ]; then
    echo "$(date -u +%FT%TZ) SKIP_STOPPED $name stop_file=$stop_file" >> "$LOG"
    return
  fi

  local pids=$(pgrep -f "python3.* $script" | tr '\n' ',')
  local restart_reason=""

  if [ -z "$pids" ]; then
    restart_reason="DEAD_PROCESS"
  elif [ -f "/root/$logfile" ]; then
    local mtime
    mtime=$(stat -c %Y "/root/$logfile")
    local age=$((NOW - mtime))
    if [ "$age" -gt "$max_stale_sec" ]; then
      restart_reason="STALE_LOG_${age}s_over_${max_stale_sec}s"
    fi
  else
    restart_reason="NO_LOG_FILE"
  fi

  if [ -n "$restart_reason" ]; then
    echo "$(date -u +%FT%TZ) RESTART $name reason=$restart_reason old_pids=$pids" >> "$LOG"
    pkill -f "python3.* $script" 2>/dev/null
    sleep 2
    screen -wipe > /dev/null 2>&1
    if [ -f "/root/$logfile" ]; then
      mv "/root/$logfile" "/root/${logfile}.bak.${NOW}"
    fi
    screen -dmS "$name" bash -c "cd /root && python3 -u $script $args > $logfile 2>&1"
    sleep 3
    local new_pid
    new_pid=$(pgrep -f "python3.* $script" | head -1)
    echo "$(date -u +%FT%TZ) RESTART $name DONE new_pid=$new_pid" >> "$LOG"
  else
    echo "$(date -u +%FT%TZ) OK $name pid=$pids" >> "$LOG"
  fi
}

case "$HOST" in
  usa)
    # Reverted 13/05 - Polymarket geoblocks US. Live bots now on Europe.
    # US server kept for Limitless recorder + future use only.
    ;;
  helsinki)
    # 15-min bot: log must update within 180s (3 min) - tighter than the
    # default 600s since a hung 15-min bot loses an entire window quickly.
    check_bot arb_v5_3way_live arb_v5_3way_live.py "--max-trades-per-window 1 --invest 7.0" arb_v5_3way_live_v1.log 180
    # consensus_v2 STOPPED 28/05 — loses after fees (72% win vs 75% break-even). Files kept for history.
    # consensus_v3 (V2 + third-platform sim+agree, scans sec 30-270, fires on first match)
    check_bot consensus_v3 live/consensus_v3/CONSENSUS_BTC_V3.py "--invest-usd 2.0 --out-dir /root/live/consensus_v3" live/consensus_v3/consensus_v3.log 600
    # consensus_v3_2 (3-of-4 fast consensus, buy cheapest 5m platform, Kalshi signal-only)
    check_bot consensus_v3_2 live/consensus_v3_2/CONSENSUS_BTC_V3_2.py "--invest-usd 2.0 --out-dir /root/live/consensus_v3_2 --thr 0.60 --similar-gap 200" live/consensus_v3_2/consensus_v3_2.log 900


    # V7 paused 13/05 pending freshness model rollout. Has 2 unhedged Predict
    # fills from 11/05 that need manual reconciliation before resuming.
    # check_bot arb_v7_live arb_v7_live.py "--max-trades-per-window 1 --invest 7.0" arb_v7_live_v6.log 600
    ;;
  hetzner)
    check_bot arb_v6_3way_live arb_v6_3way_live.py "--max-trades-per-window 2 --invest 7.0" arb_v6_3way_live_v1.log 1800
    check_bot arb_v5_3way arb_v5_3way.py "" arb_v5_3way_run.log 900
    check_bot arb_v6_3way arb_v6_3way.py "" arb_v6_3way_run.log 1800
    ;;
  *)
    echo "Usage: $0 usa|helsinki|hetzner" >&2
    exit 1
    ;;
esac
