---
name: Polymarket Bitcoin 5-minute bot — strategy, structure, current state
description: Comprehensive picture of the user's Polymarket BTC 5-min trading bot — strategy thesis, code layout, data sources, current experimental state
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user is building a Python bot that trades on Polymarket's Bitcoin 5-minute markets. It only buys, never sells, and holds positions until market resolution.

## Strategic thesis

Buy at the very start of the trading window at low prices implying ~200% potential return. Because winners pay roughly 3x, the bot can lose on twice as many trades as it wins and still be profitable. Position size: $100 per buy.

**Optimization metric: total dollar PnL, NOT win rate.** A low-win-rate rule with high upside can beat a high-win-rate rule with low upside.

## Original three-phase design (the "ideal" strategy)

1. **0–30 seconds:** limit buys at price ≤ 0.30. Acts even with small "distance" because at this stage opportunity is what's available.
2. **30–40 seconds:** top-up phase if phase 1 didn't fill enough. Willing to buy up to 0.35.
3. **40–120 seconds:** buy only when distance is high. Threshold ≥ 68 had been a good level in earlier experiments.

## Current implementation (as of 2026-04-28)

There are effectively two relevant bot files in play:

- **`BTC_5M_DUAL_DISTANCE_RESEARCH_V6.py`** — the "dual distance research" bot. Tracks both Binance distance and Polymarket distance for comparison/research. The losing run (BOT40 -$1280, BOT120 zero trades at dist≥45) was on this version, which used Polymarket distance for trade decisions.

- **The Binance-distance version brought back to office today** — this is the **agreed starting point going forward**. It's the older, profitable design with Binance distance, plus small adjustments the user made today at the office. ChatGPT had a working dialogue on this version today; the user will bring that dialogue tomorrow.

**Sub-bots inside the V6 design:**
- **BOT40:** covers 0–30s (price 0.29–0.32, no filter) and 30–40s (up to 0.34)
- **BOT120:** covers 41–120s, with a distance threshold (the right value still being swept; 45 was too strict in the last run, and the original "68 worked" finding may relate to the Binance version specifically).

## Critical empirical finding (historical, established)

**Distance** = `target_price - bitcoin_price`. Two BTC price sources have been tested:

- **Binance** BTC price (public WebSocket `wss://stream.binance.com:9443/ws/btcusdt@trade`) → bot was profitable.
- **Polymarket internal** BTC price (from Polymarket WebSocket — `final_price` field, the current BTC at each moment) → bot started losing.

**Timeline:** The bot first ran with Binance-based distance and made money. Then the user discovered Polymarket's internal price feed and switched the distance calculation to use it (it seemed more "correct" — same source as Polymarket's own settlement). From that point the bot started losing. Reason still unknown.

**Conclusion (load-bearing rule):** Use **Binance** price for the distance calculation. Today (2026-04-28) the user reverted to the Binance-based version at the office and made small adjustments — that version is the agreed starting point going forward.

## Code layout

Multiple bot generations live side-by-side in `/root/` on the server:
- `BTC_5M_DUAL_DISTANCE_RESEARCH_V6.py` — current bot (BOT40 + BOT120 + research + sweep summary)
- `מרויח יפה עובד יפה קולולו.py` — older bot, worked well historically, missing recent research fixes
- `WS_BINANCE_POLY_*` (WS recorder) — collects raw Binance + Polymarket data for offline research

**Libraries used (no requirements.txt yet):** asyncio, csv, json, math, os, re, shutil, ssl, sys, time, requests, websockets, playwright, dataclasses, datetime, typing.

## Data sources

**Polymarket:**
- Gamma API: `https://gamma-api.polymarket.com/markets`
- CLOB WebSocket: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- RTDS / Chainlink BTC price feed: `wss://ws-live-data.polymarket.com` (`crypto_prices_chainlink`, `btc/usd`)
- Playwright is used to render Polymarket pages where the API doesn't expose what's needed

**Binance:**
- Public WebSocket only (no API key): `wss://stream.binance.com:9443/ws/btcusdt@trade`
- Updates many times per second; bot reads it on its own loop

## Three-location setup

- **Office machine:** latest dev code, no `.env`
- **Home machine:** has `.env`, code state unclear/older
- **Hetzner server in Germany** (see reference_hetzner_server.md): production target. Currently runs bots manually via SSH.

Sync plan: GitHub as central hub. Office push (source of truth) → home clone → server pulls when ready to deploy.

## Trading status — IMPORTANT

**The bot is NOT live trading yet.** Current bots are research/simulation only. Real-money trading is blocked on:
- Wallet swap not done — Polymarket's built-in custodial wallet can't trade via bot. An external wallet is required (env vars are scaffolded: `MY_PRIVATE_KEY`, `MY_ADDRESS`, `POLYGON_RPC_URL`, `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_SECRET`, `POLY_PASSPHRASE`, `POLY_PRIVATE_KEY`) but real authentication/signing isn't wired up yet.
- Earlier attempts to integrate Alchemy / external wallet weren't completed.

## Current experimental state and TODOs (as of 2026-04-28 evening)

**Last run results:**
- BOT40 (no filter): -$1280, W:1 L:15
- BOT120 (dist ≥ 45): 0 trades — threshold too strict in this run

**Open questions:**
- Is BOT40's loss an "the strategy is bad" problem or a settlement/side bug? Leading hypothesis: strategy bad without a filter.
- What's the right BOT120 threshold? Probably lower than 45 (42–43?), but only the sweep can confirm.

**TODOs for tomorrow morning:**
1. Analyze `research_trades.csv`, `research_sweep_trades.csv`, `research_sweep_summary.csv`
2. Sweep BOT40 across price thresholds 0.29 / 0.30 / 0.31 / 0.32 — pick by dollars
3. Test BOT40 with a distance/flow filter (no-filter is bleeding money)
4. Sweep BOT120 thresholds 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 55 — pick by dollars

## Reports written by the bot

In `/root/data_5m_dual/` and `/root/data_ws_binance_poly_research/`:
- `research_trades.csv`, `research_sweep_trades.csv`, `research_sweep_summary.csv`
- `bot40_virtual_buys.csv`, `bot120_virtual_buys.csv`
- `bot40_settlements.csv`, `bot120_settlements.csv`
- `events.csv`

## Cosmetic open items

Lines `TGT BIN CLOS` and `TGT BIN OPEN` should be removed from the on-screen display — leftover noise.
