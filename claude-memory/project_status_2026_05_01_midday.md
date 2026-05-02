---
name: Mid-day status 2026-05-01 — live trading capability ready
description: User went out around 13:30 IL; came back to find live trading code complete and ready to test
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User went out around 13:30 IL on 2026-05-01 saying he'd return in a few hours. I worked on adding full live trading capability to LIVE_BTC_5M_V1.py while he was out.

## What was added (commit 6e7bede)

### Wallet class
- `Wallet(dry_run, env_paths)` — Polymarket Gnosis Safe (signature_type=2) wrapper
- Loads .env from /root/.env (server) or local .env
- Auto-derives CLOB API credentials on `connect()` (one-time signature)
- Methods: `get_usdc_balance()`, `place_buy(token_id, price, size_shares)`, `cancel(order_id)`, `fetch_open_orders()`
- Inert in dry-run mode — returns None / fake order_id

### CLI
- argparse with `--live` and `--url`
- Default = DRY-RUN (current V3 simulation behavior, zero risk)
- `--live` triggers loud red banner + confirmation prompt requiring user to type "go live" verbatim
- Cancellation if anything else is typed

### Live order placement in `_try_execute_bot_buy`
- Branches on `self.dry_run`
- Dry-run path: V3 simulation unchanged
- Live path: enforces daily kill switch, validates wallet connected, computes shares = spend_cap / price_limit, validates Polymarket CLOB minimum (5 shares), looks up token_id (UP→yes_token, DOWN→no_token), calls `wallet.place_buy()`, records on success / blocks on failure
- All live orders are logged as `LIVE_ORDER_PLACED` / `LIVE_ORDER_REJECTED` events

### Daily kill switch
- `_update_daily_pnl(pnl_delta)` — accumulates per-day realized P&L, auto-resets at midnight
- `_check_and_update_daily_kill()` — returns True if cumulative loss <= -$40, sets `killed_for_daily_loss` flag
- Print status now shows DAILY pnl + cap + state in live mode
- WALLET line shows balance + cap + EOA address prefix

### Removed bug
- The hack `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` at the bottom of V3 was BREAKING Playwright on Windows (Selector loop has no subprocess support). Removed it. The default ProactorEventLoop is correct on Windows for Playwright.

## Bot file stats
- 2026 lines, 91 KB
- `py_compile` passes clean
- `--help` works correctly

## What is RUNNING right now (server, dry-run)

Two bots on Hetzner, both still in dry-run mode (the new code is in git but NOT yet deployed to server — user can decide):

| Bot | Started | Status |
|---|---|---|
| `good_working_bot_kululu_V3.py` | 29/04 09:11 | running 48+h, +$10,400 sim |
| `LIVE_BTC_5M_V1.py` (old version, V3 + params only) | 30/04 17:35 | running 11.4h, +$4,651 sim |

The server still runs the OLD version of LIVE_BTC_5M_V1.py (commit 146b49d). The new live-capable version (commit 6e7bede) is in git but not yet deployed.

## How to deploy when user returns

**Option A: keep server in dry-run, just deploy new code to verify nothing broke**
```
ssh hetzner
cd /root
git --git-dir=/root/polymarket-bot/.git --work-tree=/root/polymarket-bot pull   # if cloned
# OR scp the file from local
# kill old, restart in screen with same dry-run config
```

**Option B: do the first $1-5 live test trade locally on Windows**
```
cd C:\Users\user\polymarket-bot\live\btc_5m
py -m pip install python-dotenv py-clob-client    # if not installed
py LIVE_BTC_5M_V1.py --live
# type "go live" when prompted
# paste a Polymarket BTC 5-min URL
# watch for LIVE_ORDER_PLACED in logs
```

For first test the user should manually edit `MAX_BUY_USD` from 100 to 5 (or smaller) — there is no `--size` CLI flag. Or trust the daily-loss cap to limit damage to $40.

## TODO still pending (V2)

1. **MAKER engine** with 3 simultaneous limit orders at 0.28/0.29/0.30 (still using V3's TAKER pattern).
2. **Order fill tracking** — currently we optimistically assume orders fill at the limit price. Real fills may be lower; reconciliation loop needed.
3. **`__NEXT_DATA__` extraction** wired into target capture — function exists but disconnected (was removed when wire-up didn't work). Now that we removed the SelectorEventLoopPolicy bug, this might just work on Windows + Python 3.14.
4. **NYC hour analysis + activity buckets** that user requested.
5. **Polymarket activity-based regime adaptation** (V2 idea per `project_research_questions.md`).

## Things to ask user when he returns

- Whether to deploy the new code to the server in dry-run (safe, just an upgrade) or wait.
- Whether to do the first $1-5 live test today (recommended) — needs MAX_BUY_USD lowered first.
- Confirmation on the BOT40 distance idea ambiguity: "60 ו-68 זה דיסטנס" or "מחיר".
