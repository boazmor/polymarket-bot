#!/bin/bash
# Pull the recorder files LIVE5 reads from Helsinki -> Germany, every 2s.
# Recorders stay on Helsinki; live bot runs here on the synced copies.
KEY=/root/.ssh/id_sync
SRC=root@62.238.26.145
SSHOPT="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=8"
FILES=(
  /root/research/multi_coin/data_btc_5m_research/combined_per_second.csv
  /root/research/multi_coin/data_btc_5m_research/markets.csv
  /root/research/multi_coin/data_btc_5m_research/market_outcomes.csv
  /root/data_predict_btc_5m/combined_per_second.csv
  /root/data_predict_btc_5m/markets.csv
  /root/data_limitless_btc_5m/combined_per_second.csv
  /root/data_limitless_btc_5m/markets.csv
  /root/data_okx_btc_5m/combined_per_second.csv
  /root/data_gemini_btc_5m/combined_per_second.csv
  /root/data_gemini_btc_5m/markets.csv
  /root/data_kalshi_btc_15m/combined_per_second.csv
  /root/data_kalshi_btc_15m/markets.csv
  /root/data_predict_btc_15m/combined_per_second.csv
  /root/data_limitless_btc_15m/combined_per_second.csv
  /root/data_limitless_btc_15m/markets.csv
  /root/data_okx_btc_15m/combined_per_second.csv
)
SRCS=()
for f in "${FILES[@]}"; do SRCS+=("$SRC:$f"); done
while true; do
  rsync -aR -e "$SSHOPT" "${SRCS[@]}" / 2>>/root/sync_recorders.err
  sleep 2
done
