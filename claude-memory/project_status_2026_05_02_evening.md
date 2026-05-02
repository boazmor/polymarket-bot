---
name: End of session 2026-05-02 evening — full state for resume
description: Snapshot of every running process, every recent code change, every pending task — so the next session can pick up cleanly
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
End of work day 2026-05-02 (Saturday). User shutting down Claude Code. This file is the complete picture for resume tomorrow or whenever.

## What's running RIGHT NOW (autonomous, doesn't need user)

### User's home computer (Israel)
- **LIVE_BTC_5M_V1_TEST5.py --live** trading bot
  - Position size: $1/trade (Polymarket minimum)
  - Daily loss cap: $50 (effectively disabled at this size)
  - Strategy: BOT40 maker (0.28/0.29/0.30) + BOT40 taker fallback (≤0.35) + BOT120 MAKER LIMIT @ 0.50 (distance ≥ 60)
  - Wallet: $314.40 USDC on Safe `0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B`
  - Status as of last check: running, no purchases yet (market calm — distance never hit 60)

### Hetzner Helsinki (62.238.26.145, ssh helsinki)
- **7 multi-coin recorders** under screen (rec_btc, rec_eth, rec_sol, rec_xrp, rec_doge, rec_bnb, rec_hype) — all 5m window
- Bot NOT running here (still on home PC)

### Hetzner Germany (178.104.134.228, ssh hetzner)
- **kululu V3 bot** — `good_working_bot_kululu_V3.py`, dry-run, started Apr 29
  - PID 797371, ~366min CPU
  - 633 trades, +$10,035 simulated profit, but Saturday alone was -$1,162 (-$33.82 if scaled to $1)
- **28 multi-coin recorders** under screen — 7 coins × 4 windows (15m, 1h, 4h, 1d)
  - Started 2026-05-02 ~19:21 UTC
  - Data dir: `/root/data_<coin>_<window>_research/`
  - Will accumulate ~30MB/day total — plenty of disk

**Total: 37 autonomous processes across 2 servers + 1 home PC**

## Critical settings (DO NOT change without thinking)

In `live/btc_5m/LIVE_BTC_5M_V1_TEST5.py`:
- `SAFE_ADDRESS = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"` — the REAL Safe (proxy), found via /data/trades. NOT `0x4cd0...` (that was wrong).
- `MAX_BUY_USD = 1.0` (test mode)
- `MAX_DAILY_LOSS_USD = 50.0` (effectively unlimited at $1/trade)
- `MIN_DIST_BOT120 = 60.0` (was 68 earlier)
- `BOT120_LIMIT_PRICE = 0.50` (BOT120 is now MAKER, sits on book at 0.50)
- `SCREEN_REFRESH_EVERY_SEC = 2`
- The flicker fix: `print_status` redirects stdout to a StringIO buffer, ONE write to terminal — flicker eliminated

## Latest commits (most recent first)

```
03728f6 fix calendar slug ET timezone — daily slug was off by one day
51dce08 add start_germany_4windows.sh — kills duplicate 5m, starts 28 new
b88b8cc MULTI_COIN_RECORDER: add --window flag (5m/15m/1h/4h/1d)
653108f raise daily loss cap from $15 to $50
8e547cc add analyze_pnl_by_time.py — segments PnL by hour, day-of-week
fd874d9 BOT120 maker @ 0.50 + dist threshold 60 + flicker-free screen
79bfdb7 V2 fix + Helsinki migration + multi-coin research
```

## Pending tasks

### #13 — Refactor LIVE bot to modular architecture (15-20 hours, multi-session)

User-approved plan in `project_multi_coin_architecture_plan.md`:
- 11 components (Binance fetcher, Reports, Screen, Calculations, Wallet, Master, **Params file**, Cross-Coin Engine, Capital Allocator, State Persistence, Alerts)
- Split BOT40 into BOT_30 (maker) + BOT_40 (taker fallback). BOT_120 stays parallel.
- 5 stages, ~3-4 hours each:
  1. **Externalize params to bot_config.py** (1-2h, easy win — bot keeps working, just reads from external file)
  2. Split BOT40 → BOT_30 + BOT_40 (2-3h, pure refactor no logic change)
  3. Extract each component to its own file under bot_engine/ (5-7h)
  4. Multi-coin: master controller orchestrates 7 strategy instances, shared wallet (3-4h)
  5. (Optional, gated on report) Cross-Coin Signal Engine (4-5h)

User wants to do this incrementally. Start with stage 1 next session.

### Other follow-ups (lower priority)

- **Bot migration to Helsinki** — when home bot is stable for 24h+, migrate trading to Helsinki for 24/7. The script `start_bot_helsinki.sh` already exists on Helsinki — just need to stop home bot and run it.
- **NYC time filter in bot** — add a `--block-hours` flag or hardcode skip for NYC 05-07 (the confirmed losing hours from analysis).
- **Daily report email/telegram** — once #13 stage 4 is done.
- **Cancel Germany server** — only after #13 stage 4 + Helsinki has ALL 35 recorders + bot running stable for 1 week. Cost saving: ~7€/month.

## How to resume in the next session

If you (Claude) are reading this in a new session:

1. **Check current bot health** — is it still running?
   ```
   # From user's home PC, check the terminal that has the bot running
   # OR look at recent data: 
   Get-Item C:\Users\user\polymarket-bot\live\btc_5m\data_live_btc_5m_v1\bot40_signals.csv | Select LastWriteTime
   ```

2. **Check Helsinki recorders**
   ```
   ssh helsinki "screen -ls | wc -l"   # should be 8 (7 + the 'No screen' header)
   ```

3. **Check Germany 28 recorders + kululu V3**
   ```
   ssh hetzner "screen -ls | wc -l"   # should be 29 (28 recorders + header)
   ssh hetzner "ps -p 797371"          # kululu V3 bot
   ```

4. **Run the cross-coin / time-of-day analysis on accumulated data**
   - Multi-coin: `py C:\Users\user\polymarket-bot\research\multi_coin\analyze_outcomes.py`
   - Per-time: `ssh hetzner "python3 ~/x.py"` (need to re-upload analyze_pnl_by_time.py first)

5. **Start stage 1 of the refactor** if user is ready
   - Extract all `MAX_BUY_USD`, `MIN_DIST_BOT120`, `BOT40_MAKER_LEVELS`, `MAX_DAILY_LOSS_USD`, etc. into a new file `live/btc_5m/bot_config.py`
   - Bot imports them from there
   - Result: change params without editing the main bot file

## How to access from a different machine (office)

User has 2 dev machines: home + office. The current state lives on home PC.
- All code is in github.com/boazmor/polymarket-bot (private)
- Memory files in `C:\Users\user\.claude\projects\C--Users-user\memory\` are NOT in git (yet)
- To work from office: `git pull` gives you the code. The MEMORY (these .md files) needs to be manually synced or pushed to git.

## User preferences for the next session

- **Reply in Hebrew, RTL-aware** — never put English in middle of Hebrew sentence; use code blocks or backticks for English.
- **No yes/no questions** — just act when next step is obvious. Edge cases (live trading, destructive ops) are exceptions.
- **He might use `claude --dangerously-skip-permissions`** to avoid permission prompts. If he does, I can act freely.
- **Position size for actual trading: $1** during testing. Will scale to $50 then $100 when bot is stable.
- **The bot only trades from non-blocked locations**. Israel home + Hetzner Helsinki work. Hetzner Germany is blocked for trading (used only for recording).
