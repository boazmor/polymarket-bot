---
name: reference-gemini-kalshi-recorders
description: How to query and recorder layout for Gemini Predictions and Kalshi BTC binary markets — built 27/05 on Helsinki
metadata: 
  node_type: memory
  type: reference
  originSessionId: cb455403-2a0a-4be9-b453-c1f5e84301bc
---

**Gemini Predictions API.** `GET https://api.gemini.com/v1/prediction-markets/events` (no auth). Returns events array. Filter contracts by ticker prefix `BTC05M` for 5-min, `BTC15M` for 15-min. Ticker format `BTC05MYYMMDDHHMM` encodes RESOLUTION time (e.g., `BTC05M2605271210` resolves 2026-05-27 12:10 UTC; trading window is 12:05–12:10). Each event has a single `Up` contract whose `prices` object holds both directions: `buy.yes` is the ask to bet UP, `buy.no` is the ask to bet DOWN, `sell.yes/sell.no` are bids.

**Gemini availability.** BTC 5-min markets appear only intermittently — observed in ~25% of minute-samples (4-min observation 27/05). Each market visible roughly 5-10 min before its resolution. BTC15M was NOT present during the same sample window. Expect SPARSE coverage; don't gate the bot on Gemini presence.

**Kalshi API.** `GET https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXBTC15M&status=open` returns BTC 15-min binary up/down markets (BRTI CF Benchmarks oracle, 60-sec average). Each market has `floor_strike` (target = BRTI 60-sec avg at window open), `yes_bid_dollars/yes_ask_dollars`, `no_bid_dollars/no_ask_dollars`, depth via `yes_bid_size_fp/yes_ask_size_fp`. NOTE: Kalshi BTC has ONLY 15-min and hourly (KXBTCD) — NO 5-min binary as of 27/05. Use 15-min to cover 3 consecutive Poly 5-min windows (sub_pos = 0, 1, or 2).

**Recorders on Helsinki.** `/root/GEMINI_RECORDER_5M.py` and `/root/KALSHI_RECORDER_15M.py`. Both poll their API every 1 sec, snapshot Binance BTCUSDT in parallel, write to `/root/data_gemini_btc_5m/combined_per_second.csv` and `/root/data_kalshi_btc_15m/combined_per_second.csv`. Schema is aligned with Limitless recorder for the bot's snapshot functions. Running under screens `rec_gemini_btc_5m` and `rec_kalshi_btc_15m`.

**Watchdog.** Both wired into `/root/check_recorders.sh` (cron now `0 * * * *` — hourly). The NO_ASK column-17 check is SKIPPED for these recorders because their schemas don't put ask in column 17. Restart functions: `restart_gemini` and `restart_kalshi_new`.

Related: [[project-consensus-v1-bot]], [[reference-helsinki-server]], [[project-gemini-predictions-find]], [[project-arbitrage-kalshi-poly]].
