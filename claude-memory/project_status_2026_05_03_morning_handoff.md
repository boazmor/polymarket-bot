---
name: End of 03/05 morning session — handoff for office continuation
description: Bot fixes shipped, multi-coin analysis done. User going to sleep, will resume from office. Everything pushed to GitHub.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
End of session ~06:30 Israel time, 03/05/26. User going to sleep, will resume work from office machine later. All changes committed and pushed.

## What was shipped this session (commit e5dcaea)

**bot_config.py:**
- `MAX_BUY_USD`: 1.0 → 2.0
- `BOT40_MAKER_SIZE_USD`: 1.0 → 2.0
- Reason: Polymarket $1 minimum is NET of commission, so $1.00 rounds to $0.999 notional and gets rejected. See `project_polymarket_min_order_with_commission.md`.

**LIVE_BTC_5M_V1_TEST5.py:**
- `_try_execute_bot_buy` made `async`
- `wallet.place_buy(...)` wrapped in `await asyncio.to_thread(...)`
- 4 call sites in `stream_current_market` loop updated to `await`
- Reason: place_buy was a synchronous HTTP call inside the async event loop — froze the entire bot for 1-3s per buy attempt. V3 (dry-run) didn't have this blocker. User confirmed seeing "big delay at start of trading."

## Live state of the bot

User started the LIVE bot manually around 06:25 Israel time after my earlier autonomous restart attempts failed (Cloudflare blocked when launched detached, AVG flagged the stdin file). Manual start works because user types "go live" + URL interactively. **As of session end: bot is running and trading-capable, just waiting for opportunities.**

## Multi-coin recording — all working, target backfilled

35 recorders alive. `target_binance.csv` regenerated every minute by cron on both servers (`/root/cron/backfill_target.py`). Coverage: 100% for 6 coins, 0% for HYPE (no Binance pair). Distance can now be computed for 5m/15m markets that previously had blank `target_price`.

## Multi-coin analysis findings (11.8 hr aligned cross-coin data)

**Correlation matrix (per-second binance returns):**
- BTC↔ETH: 0.62 (strong)
- ETH↔BNB: 0.47, BTC↔BNB: 0.44 (moderate)
- All others: 0.17–0.35 (weak)
- SOL and DOGE most independent

**Lead-lag at 1-second resolution: NONE.** All 15 pairs have best lag = 0. Means HFT arb between exchanges aligns prices faster than 1s. Need ms-level data to detect leading.

**Per-coin distance percentiles (max % move per 5m market, 143 mkts each):**
| coin | p75 (=triggers 25% mkts) |
|---|---|
| DOGE | 0.166% |
| SOL  | 0.095% |
| XRP  | 0.093% |
| ETH  | 0.090% |
| BTC  | 0.064% |
| BNB  | 0.063% |

DOGE is ~2.5x more volatile than BTC. Means: a single % threshold doesn't work either — each coin needs its OWN number. The current `MIN_DIST_BOT120 = 60` ($) is BTC-equivalent of 0.064% which matches p75 nicely — by coincidence the user picked the right value for BTC.

## Resume from office — checklist

1. **Pull latest:** `git pull origin main` — gets the bot fix (commit e5dcaea)
2. **Verify bot is alive at home:** ssh into home PC if reachable, OR ask user to confirm process is up
3. **What to work on next** (in priority order):
   - **Make BOT_120 distance threshold per-coin** in `bot_config.py` — replace single `MIN_DIST_BOT120 = 60` with `COIN_PARAMS[coin].dist_threshold_pct`. Use the p75 numbers above. THIS UNBLOCKS enabling other coins.
   - Hourly-pattern analysis user requested earlier (good vs bad NYC hours per coin)
   - Disk rotation strategy for Germany (~13 days runway, see task #10)
   - Verify bot ACTUALLY traded overnight (settlements.csv should have new rows after the morning fix)
4. **Office-specific setup:** memory says office machine "needs its own SSH key set up next time we work there" — may need to generate ed25519 key and add to hetzner+helsinki authorized_keys

## Tasks still open

- #10 Plan disk-rotation strategy for Germany server (13 days runway)

## Files NOT in repo (live on servers only)

- `/root/cron/backfill_target.py` (both servers) — regenerates target_binance.csv every minute
- `/root/research/multi_coin/KALSHI_RECORDER_1H.py` (Germany only) — built but DECISION made to NOT deploy. Keep for future if we change mind.

## Key things NOT to forget when waking up

- The bot is LIVE on home PC with $2/trade. Real money. $299.85 USDC in wallet.
- If anything seems off, FIRST verify the bot is still alive before assuming worst-case.
- User explicitly authorized me to kill/restart processes autonomously (this session). Save that as feedback if not already.
