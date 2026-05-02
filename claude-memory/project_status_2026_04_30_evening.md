---
name: End-of-day status 2026-04-30 evening + tomorrow's TODO
description: What's running, what was learned, what to do first thing next session
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
End of long session 2026-04-30 (continued from morning). Bot deployed and running on server in dry-run; local machine has Playwright/Python-3.14 issue we'll address tomorrow.

## What's running RIGHT NOW

**Two bots, both on the Hetzner server, both in simulation/dry-run mode:**

1. **`good_working_bot_kululu_V3.py`** (the original "office bot") — running since 29/04 09:11.
   - Data dir: `/root/data_5m_dual/`
   - Old strategy: BOT40 limit 0.31, BOT120 41-120s, dist >= 60, no price cap
   - PID 797371, screen session attached to office-machine pts/0

2. **`LIVE_BTC_5M_V1.py`** — restarted 30/04 ~17:20 with new params.
   - Data dir: `/root/data_live_btc_5m_v1/`
   - New strategy: BOT40 limit 0.30 (caps 0.28-0.30 fills naturally), BOT120 0-120s full window, dist >= 68, price cap 0.80
   - In screen session `livebot` (attach with `screen -r livebot`)
   - File matches GitHub commit `146b49d` (V3 + parameters only, no extra integrations)

## V3 results so far (32 hours, simulation)

| Bot | Trades | Wins | Losses | PnL |
|---|---|---|---|---|
| BOT40 | 87 | 41 | 46 | **+$4,228.11** |
| BOT120 | 166 | 141 | 25 | **+$2,627.45** |
| **Combined** | **253** | **182** | **71** | **+$6,855.56** |

Win rate 71.9%. ~$214/hour simulated profit. Per-trade size $100. These are simulation numbers — actual live trading would differ for fees, slippage, fill probability.

## Today's discoveries (good and bad)

### 🟢 GOOD — `__NEXT_DATA__` is the answer for browser-free target extraction
Polymarket pages embed all market data in a `<script id="__NEXT_DATA__">` JSON tag.
Plain HTTP fetch + JSON parse extracts `priceToBeat` reliably. **No browser required.**
Diagnosed in `live/btc_5m/diag_no_browser.py` — confirmed working on the user's Windows machine.
The function `extract_target_from_next_data()` is already in `LIVE_BTC_5M_V1.py` but not wired in
(the wire-up I tried wasn't behaving as expected when invoked from the bot's async context — needs
a fresh attempt tomorrow).

### 🔴 BAD — Windows + Python 3.14 + Playwright doesn't work
The user's home machine has Python 3.14, and Playwright 1.58 hits a `NotImplementedError` on
`asyncio.create_subprocess_exec` when trying to launch Chromium. This affects BOTH V3 and the
live bot when run locally. The same code works fine on the Linux server (Python 3.12).

**Setting `WindowsSelectorEventLoopPolicy` makes it WORSE** (selector loop doesn't support
subprocess on Windows at all). The default ProactorEventLoop is correct but Playwright 1.58
still fails on Python 3.14 specifically.

Two paths forward (decide tomorrow):
- (a) Install Python 3.12 alongside 3.14, run the bot with Python 3.12.
- (b) Wire `__NEXT_DATA__` into the bot properly so Playwright is unnecessary.

## TOMORROW MORNING — first things to do

1. **Read this memory file** (`project_status_2026_04_30_evening.md`) and `git pull`.
2. **Show user hourly averages** of V3 PnL — break down the 32-hour session into per-hour buckets,
   so we can see when the bot makes most money. User specifically asked for this.
3. **Show user day-vs-night PnL by NYC time** — Polymarket markets are NYC-time anchored. Many
   participants are US traders. Hypothesis worth testing: is the strategy more profitable during
   US daytime hours (high participation) or overnight (low participation)? User suggested this
   as worth investigating.
4. **Wire `__NEXT_DATA__` into the bot properly** so local testing works on Windows + Python 3.14.
5. **(Then)** continue with CLOB integration, MAKER engine, safety enforcement — the items already
   listed in `project_live_bot_v1_status.md` from the previous session.

## Don't forget

- The user wants the bot to be a **MAKER** model (3 simultaneous limit orders at 0.28/0.29/0.30),
  not a TAKER. Currently it's V3-style TAKER. The MAKER engine is still pending implementation.
- User has $15.40 in his Polymarket wallet (after Redeem). Needs ~$200 to cover live MAKER orders.
- User said he'd transfer $400-500 once live trading is ready.
- Daily loss cap was raised from $20 to $40 mid-session.
- $5 test size was DROPPED — going straight to $100 in virtual, then $100 live (since virtual is
  risk-free anyway).
