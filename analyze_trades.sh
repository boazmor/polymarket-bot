#!/bin/bash
# Weekly trade analysis script
# Usage:
#   1. Download fresh data: scp root@178.104.134.228:/root/data_5m_dual/trade_outcomes.csv .
#   2. Run: bash analyze_trades.sh trade_outcomes.csv
# (Or just `bash analyze_trades.sh` if file is in current dir)

F="${1:-trade_outcomes.csv}"
[ -f "$F" ] || { echo "ERROR: $F not found. Download it first: scp root@178.104.134.228:/root/data_5m_dual/trade_outcomes.csv ."; exit 1; }

echo "================================================================"
echo "TRADE ANALYSIS — $(date) — $F ($(wc -l < "$F") rows)"
echo "================================================================"

echo
echo "=== 1. PnL BY DAY ==="
awk -F, 'NR>1{d=substr($1,1,10); c[d]++; if($8=="WIN")w[d]++; p[d]+=$13} END{for(d in c) printf "%s | %3d trades | W=%3d L=%3d | win%%=%4.1f | PnL=$%+.2f\n", d, c[d], w[d]+0, c[d]-w[d], 100*w[d]/c[d], p[d]}' "$F" | sort

echo
echo "=== 2. PnL BY BOT BY DAY ==="
awk -F, 'NR>1{d=substr($1,1,10); k=$3"_"d; c[k]++; if($8=="WIN")w[k]++; p[k]+=$13} END{for(k in c){split(k,a,"_"); printf "%-7s %s | %3d t | W=%3d L=%3d | win%%=%4.1f | PnL=$%+.2f\n", a[1], a[2], c[k], w[k]+0, c[k]-w[k], 100*w[k]/c[k], p[k]}}' "$F" | sort -k2

echo
echo "=== 3. DAY OF WEEK ==="
for BOT in BOT40 BOT120; do
  echo "--- $BOT ---"
  awk -F, -v BOT=$BOT 'NR>1 && $3==BOT{d=substr($1,1,10); cmd="date -d \""d"\" +%u"; cmd|getline dow; close(cmd); c[dow]++; if($8=="WIN")w[dow]++; p[dow]+=$13} END{days[1]="Mon";days[2]="Tue";days[3]="Wed";days[4]="Thu";days[5]="Fri";days[6]="Sat";days[7]="Sun"; for(i=1;i<=7;i++) if(c[i]) printf "%s | %3d t | W=%3d L=%3d | win%%=%4.1f | PnL=$%+.2f\n", days[i], c[i], w[i]+0, c[i]-w[i], 100*w[i]/c[i], p[i]}' "$F"
done

echo
echo "=== 4. NY HOUR (UTC-4 in DST) ==="
for BOT in BOT40 BOT120; do
  echo "--- $BOT ---"
  awk -F, -v BOT=$BOT 'NR>1 && $3==BOT{h=(substr($1,12,2)+20)%24; c[h]++; if($8=="WIN")w[h]++; p[h]+=$13} END{for(h=0;h<24;h++) if(c[h]) printf "NY %02d:00 | %3d t | win%%=%4.1f | PnL=$%+.2f\n", h, c[h], 100*w[h]/c[h], p[h]}' "$F" | sort
done

echo
echo "=== 5. SEC FROM START (BOT40 most relevant) ==="
for BOT in BOT40 BOT120; do
  echo "--- $BOT ---"
  awk -F, -v BOT=$BOT 'NR>1 && $3==BOT{s=$5; b=int(s/5)*5; c[b]++; if($8=="WIN")w[b]++; p[b]+=$13} END{for(b=0;b<=120;b+=5) if(c[b]) printf "sec %3d-%3d | %3d t | win%%=%4.1f | PnL=$%+.2f\n", b, b+4, c[b], 100*w[b]/c[b], p[b]}' "$F"
done

echo
echo "=== 6. ECONOMICS (avg win/loss, breakeven) ==="
for BOT in BOT40 BOT120; do
  awk -F, -v BOT=$BOT 'NR>1 && $3==BOT{c++; if($8=="WIN"){w++; win_pnl+=$13} else {loss_pnl+=$13}} END{a_w=(w>0?win_pnl/w:0); a_l=(c-w>0?loss_pnl/(c-w):0); be=(a_w-a_l!=0?-a_l/(a_w-a_l)*100:0); printf "%-7s | n=%4d | avg_win=$%+7.2f | avg_loss=$%+7.2f | breakeven=%4.1f%% | actual=%4.1f%% | total=$%+.2f\n", BOT, c, a_w, a_l, be, 100*w/c, win_pnl+loss_pnl}' "$F"
done

echo
echo "=== 7. LAST 60 W/L STREAK ==="
tail -60 "$F" | cut -d, -f8 | tr '\n' ' ' | sed 's/WIN/W/g; s/LOSS/L/g; s/PUSH/P/g'
echo

echo
echo "=== 8. AVG |distance| ON LOSSES BY DAY ==="
awk -F, 'NR>1 && $8=="LOSS"{d=substr($1,1,10); v=$16<0?-$16:$16; if(v=="" || v==0) next; s[d]+=v; c[d]++} END{for(d in c) printf "%s | %3d losses | avg |dist|=%.1f\n", d, c[d], s[d]/c[d]}' "$F" | sort
echo
echo "================================================================"
echo "Done. Reminders:"
echo "  - <3 weeks of data = sample-size noise possible"
echo "  - BOT40 breakeven ~31%, BOT120 breakeven ~65%"
echo "  - Sec 25-39 dominates BOT40 wins"
echo "  - Saturday is BOT40 disaster (per 1 weekend, needs more data)"
echo "================================================================"
