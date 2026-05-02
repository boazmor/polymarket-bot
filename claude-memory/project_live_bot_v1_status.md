---
name: LIVE_BTC_5M_V1 — current state and pending work
description: Where the live bot stands at end of session 2026-04-30, what's done, what's pending for the next session
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
End of session 2026-04-30 evening, after a long day of reorganizing the repo, building a from-scratch live bot, abandoning it (didn't match V3's UX), and rebuilding from V3 as the base.

## Current state of `live/btc_5m/LIVE_BTC_5M_V1.py`

The file is V3 (`good_working_bot_kululu_V3.py`) **as the base** with parameter changes applied. Still **virtual / simulation only** — does not yet send real orders to Polymarket.

**Strategy parameters in the live bot (decided with user on 2026-04-30):**
- `BOT120_MIN_SEC = 0` (V3 had 41) — BOT120 active for the full 5-minute window
- `MIN_DIST_BOT120 = 68.0` (V3 had 60.0) — distance threshold for BOT120 entry
- `BOT120_MAX_PRICE = 0.80` (V3 had no cap) — refuses to buy BOT120 above this
- `MAX_BUY_USD = 100.0` (V3 had `MAX_VIRTUAL_BUY_USD = 100`) — same value, renamed for live context
- `BOT40_FALLBACK_PRICE = 0.35` (unchanged) — phase 2 cap
- `BOT40_LIMIT_END_SEC = 30` (unchanged) — phase 1/2 boundary
- BOT40 phase 1 effective cap: 0.30 via `_bot40_level_and_mode` returning `max(BOT40_MAKER_LEVELS)`
- `BOT40_MAKER_LEVELS = [0.28, 0.29, 0.30]` — placeholder for the future maker engine
- `BOT40_MAKER_SIZE_USD = 60.0` — placeholder
- `MAX_DAILY_LOSS_USD = 40.0` (user changed from $20 to $40 mid-session) — safety stop, NOT YET ENFORCED in code
- `MAX_WALLET_USD = 200.0` — safety stop, NOT YET ENFORCED in code

The file compiles, but the safety stops are constants only — they aren't yet checked anywhere in the run loop. Live mode will need to wire them up.

## What is NOT yet in the file (pending next session)

1. **CLOB integration** — `py-clob-client` import exists but no `Wallet` / `ClobClient` class. `_try_execute_bot_buy` still does V3-style virtual fill simulation; nothing is sent to Polymarket. About 200 lines of new code needed: load private key, init client, derive API creds, place buy orders, poll fills, handle settlement.

2. **MAKER engine for BOT40** — the user wants 3 simultaneous limit BUY orders at `[0.28, 0.29, 0.30]`, each `BOT40_MAKER_SIZE_USD=$60`, total $180 reserved in book, capped at `MAX_BUY_USD=$100` actual fills, with a top-up at 0.35 if not fully filled by sec 30. Currently the bot uses V3's TAKER pattern (single limit at 0.30 captures fills naturally; same outcome but no orders sit in the book attracting sellers). About 300-500 lines for a real MAKER engine.

3. **Safety enforcement** — daily-loss-cap auto-stop, wallet-cap refuse-to-trade, kill switch. Constants are there but nothing checks them. Maybe 100 lines.

4. **CLI** — `--live` flag with `"go live"` confirmation prompt. Default dry-run. About 30 lines.

## User's decision flow during the session

- Started thinking about $5 test trades. Then realized virtual mode is risk-free regardless of size, and decided to test at full $100 in virtual, then jump straight to live at $100. **Implemented:** MAX_BUY_USD=100 throughout.
- Initial daily-loss cap was $20. User said "$20 is too small, let's do $40, we'll make it back easily." **Implemented:** MAX_DAILY_LOSS_USD=40.
- Asked for 3 simultaneous limit orders at 0.28/0.29/0.30 to "tempt" sellers (maker model). **Not yet implemented** — current code uses V3's TAKER pattern (single 0.30 cap) which functionally captures the same fills but doesn't sit visibly in the book.
- Asked about fees on unfilled limit orders — confirmed there are NONE.
- Said he'd transfer $400-500 to wallet to support $100 trades but is being conservative; OK to start at $100 since virtual is free.

## Plan for the next session

1. **First thing:** read this memory file + `git pull` the repo. The live bot is at `live/btc_5m/LIVE_BTC_5M_V1.py`.
2. **Add CLOB integration** for live mode (Wallet class, place/cancel/poll orders).
3. **Decide with user**: do we build the full 3-order maker engine, or stay with single-limit TAKER pattern (V3-style with 0.30 cap)? The TAKER pattern gives the same fills, just no visible sitting orders. User asked for maker so default to maker unless he changes his mind.
4. **Wire up safety stops** in the run loop.
5. **Add CLI flag + confirmation prompt** for going live.
6. **Test in dry-run on the server** before any real money.

## Things the bot ALREADY does (inherited from V3)

- Connects to Polymarket book WS, Binance WS, Polymarket Gamma API.
- Renders Polymarket page via Playwright to capture target price (when Gamma/HTML don't return it).
- Rolls over markets every 5 minutes.
- BOT40 with flow filter (|dist| >= 25 -> only with flow).
- BOT120 direction-only.
- CSV logging of decisions, virtual buys, settlements, research data.
- Per-bot screen panels (BOT40/BOT120 side by side) with TOTAL_PROFIT.

## File location

- Live bot: `C:\Users\user\polymarket-bot\live\btc_5m\LIVE_BTC_5M_V1.py`
- Wallet env file (private key): `C:\Users\user\polymarket-bot\live\btc_5m\.env` — gitignored, holds `MY_PRIVATE_KEY`, `MY_ADDRESS`, `POLYGON_RPC_URL`
- Reference research V3 (canonical strategy): `C:\Users\user\polymarket-bot\research\good_working_bot_kululu_V3.py`
- GitHub: https://github.com/boazmor/polymarket-bot (commit `2735df2` is the latest as of session end)

## Mood note

Long day, user occasionally frustrated by my over-engineering and from-scratch rewrites. He wants minimal incremental changes on top of V3, not new architecture. **For the next session: stay close to V3's code, do not redesign UX, only add what's strictly needed for live trading.**
