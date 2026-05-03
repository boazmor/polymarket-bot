#!/bin/bash
# Check whether buying both sides cheap (sum < $1) is ever possible
# across all recorded coins/windows on the server.
#
# Predicted outcome: NEVER, on any coin. No-arbitrage principle is
# universal in binary prediction markets. The only difference between
# coins should be how often the spread is wider than 1.02 (low-liquidity
# coins probably wider).
#
# Run on the server: bash /root/check_arbitrage_all_coins.sh
# Or scp it and run there.

THRESHOLD_SUM=0.70   # buying both sides for less than this = arbitrage opportunity

DIRS=$(ls -d /root/data_*_research/ /root/data_5m_dual/ /root/data_kalshi_*/ 2>/dev/null)

echo "=========================================="
echo "ARBITRAGE OPPORTUNITY CHECK ACROSS COINS"
echo "Looking for moments when UP_ask + DOWN_ask < $THRESHOLD_SUM"
echo "(this would mean both sides simultaneously buyable cheap)"
echo "=========================================="

for D in $DIRS; do
  COIN=$(basename "$D")
  # find the signals/order-book CSV
  SIG=""
  for candidate in "$D"*signals*.csv "$D"*book*.csv "$D"*order*.csv "$D"bot40_signals.csv "$D"bot120_signals.csv; do
    [ -f "$candidate" ] && { SIG="$candidate"; break; }
  done
  [ -z "$SIG" ] && { echo "$COIN: no signals file found, skipping"; continue; }

  echo ""
  echo "--- $COIN ($(basename "$SIG")) ---"

  # Detect column positions for up_best_ask and down_best_ask
  HEADER=$(head -1 "$SIG")
  UP_COL=$(echo "$HEADER" | tr ',' '\n' | grep -n -i "up.*ask\|yes.*ask" | head -1 | cut -d: -f1)
  DN_COL=$(echo "$HEADER" | tr ',' '\n' | grep -n -i "down.*ask\|no.*ask" | head -1 | cut -d: -f1)

  if [ -z "$UP_COL" ] || [ -z "$DN_COL" ]; then
    echo "  (column names not detected — header: $HEADER)"
    continue
  fi

  awk -F, -v UC="$UP_COL" -v DC="$DN_COL" -v T="$THRESHOLD_SUM" '
    NR==1 {next}
    {
      ua = $UC + 0; da = $DC + 0;
      if(ua<=0 || da<=0) next;
      total++;
      sum = ua + da;
      if(sum < T) cheap++;
      if(sum < min || min == 0) { min = sum; min_t = $1; min_ua = ua; min_da = da; }
      if(sum < 1.00) sub_one++;
    }
    END {
      if(total == 0) { print "  no rows with both asks > 0"; exit }
      printf "  rows=%d | both<%.2f: %d (%.2f%%) | sum<1.00: %d (%.4f%%) | min sum: %.3f at %s (UP=%.2f DN=%.2f)\n",
             total, T, cheap+0, 100*cheap/total, sub_one+0, 100*sub_one/total, min, min_t, min_ua, min_da;
    }
  ' "$SIG"
done

echo ""
echo "=========================================="
echo "Done. Expected result: '0 (0.00%)' for the both<$THRESHOLD_SUM column on every coin."
echo "If any coin shows >0%, that's a discovered arbitrage signal worth investigating."
echo "=========================================="
