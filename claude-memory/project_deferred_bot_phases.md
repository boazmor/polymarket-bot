---
name: Deferred bot upgrade phases — work-to-do list with target dates
description: Track the bot improvements approved 13/05 but not done yet. On every new session, scan this file and remind the user what's overdue. As of 13/05 phase 1 safety fixes are deployed; phases 2-3 pending.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
Last updated: 2026-05-13 (Israel time, ~02:00). Phase 1 safety fixes were just deployed (commit forthcoming).

**REMINDERS to surface at the start of every new session until done:**

## Phase 2A — Oracle divergence filter — TARGET 2026-05-14

What: skip cross-oracle candidates (A_POLY, B_POLY, A_LIM, B_LIM) when |Chainlink - Binance| > 0.3% of strike. Same-oracle pairs (PolyUP_LimDN, LimUP_PolyDN) remain unfiltered.

Blocker: Binance WebSocket is geo-blocked from the US server. Need to source the Binance reference price from somewhere reachable.

Options to try:
1. Read `strike` from Predict.fun's latest.json (Predict's strike IS Binance @ market_open second, so we already have it from the existing Predict recorder)
2. Use Coinbase or Kraken BTC/USD as a proxy (US-reachable)
3. Use Pyth Network feed (multi-chain, US-reachable)

Decision: try option 1 first since the data is already on disk on the US server. ~30 minutes of code.

## Phase 2B — WebSocket direct + asyncio refactor — TARGET 2026-05-15 or 16

What: replace the recorder→file→bot polling architecture with the bot subscribing directly to Polymarket, Predict.fun and Limitless WebSocket order-book feeds. Expected gain: 150-300 ms cut from detect-to-fire. Also replace ThreadPoolExecutor with asyncio + aiohttp persistent sessions; cuts another 50-100 ms.

Why deferred: 4-6 hour rewrite of bot core. Want to ship Phase 1 safety fixes first, run 1-2 days, verify stable, then take on the bigger refactor. If both shipped together and bot misbehaves, can't isolate the cause.

Files affected: arb_v5_3way_live.py, arb_v6_3way_live.py, limitless_trader.py (needs native async), predict_trader.py (needs native async). New ws_feeds.py module to centralize subscriptions.

## Phase 3 — Operational improvements (not safety-critical)

- **Separate wallets per bot** — currently all 3 bots share one EOA. Risk: nonce contention, wealth-snapshot pollution. Effort: 2-3 hours to set up new wallets, bridge funds, update .env. Target: when comfortable.
- **YAML config + --dry-run mode** — already started (`--dry-run` added in Phase 1). Externalize all hardcoded constants to bot_config.yaml so we can tune without redeploying. Effort: 2 hours.
- **Telegram bot for alerts and daily report** — morning report (PnL, balances, fill rate, anomalies). Instant alerts for: bot down, unhedged position, balance below threshold, repeated API errors. Effort: 3-4 hours.
- **Metrics tracking** — realized vs displayed edge, fill latency, quote age, oracle divergence at fill, adverse selection rate. Effort: 2 hours after the bot is asyncio so we can add timing decorators cleanly.

**How to recall**: This memory file is auto-loaded each session via MEMORY.md. As long as MEMORY.md links to it, Claude reads it at the start of each new session and surfaces overdue items.
