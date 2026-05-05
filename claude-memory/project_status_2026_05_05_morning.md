---
name: 05/05/2026 morning — BRM target fixed, recorder data-loss bug fixed, arb bot live
description: Major morning session. Chainlink target fix for BRM, recorder no longer wipes data on restart, arb tracker + tiered virtual arb bot deployed on Germany.
type: project
---

## What got fixed today

### 1. BRM target capture (commits 6574197, master/market_manager)
BRM ran 21h overnight without target — only 1 of 247 markets had target captured. Root cause: `next_data` fallback I added on 04/05 didn't work on rollover markets (only worked for the initial --url market). BOT120 never traded because no distance available.

**Fix:** added `bot_engine/chainlink.py` — ChainlinkClient subscribes to wss://ws-live-data.polymarket.com (Polymarket's own resolution source). Master spawns one chainlink task per coin alongside binance task. MarketManager.capture_chainlink_target() called on every load_initial_market_from_url and move_to_next_market. resolve_target_in_use() now prefers `target_chainlink_at_open` over all other sources.

**Verified working:** events.csv shows TARGET_CHAINLINK_AT_OPEN entries for every new market post-restart.

### 2. Recorder no longer wipes data on restart (commit f9fdeff)
**The big find:** MULTI_COIN_RECORDER's `init_clean()` did `shutil.rmtree(self.data_dir)` every startup. With cron auto-restarts at 8am+8pm Israel, this destroyed days of recordings silently. BTC 15m today was wiped at 05:00 UTC — 70+ hours lost.

**Fix:** init_clean now `init-or-resume` — preserves existing files. Headers only written for missing/empty files. raw_poly opened append-only. Logs RECORDER_RESUME event.

Applies to ALL recorders. From next restart, data preserved across restarts.

### 3. Cross-platform arbitrage tooling

#### Discovery (recap from 04/05 evening)
Polymarket BTC 15m vs Kalshi BTC 15m — buying both sides at <$1 = guaranteed profit when strike ≈ target.
- Empirical: 42-43% of seconds have profitable arb.
- Direction A (PolyUP + KalshiNO): currently 13.8% avg profit when profitable
- Direction B (PolyDOWN + KalshiYES): currently 7.8% avg profit when profitable

#### arb_tracker.py (commit 5cfdd32, deployed)
Logs distinct opportunities (start + end + duration) to /root/arb_tracker_log.csv. Threshold cost ≤ 0.92.

In first 8 minutes of running on 05/05 morning: 6 distinct opportunities in Direction A on a single market. Durations 2s to 237s. Profits 8.5-12.2%.

#### arb_virtual_bot.py (commits 256cfb1, ddd1e1a, 07a62ec, deployed)
Virtual trader. When opportunity detected, simulates buying $50 on each side. Tracks open trades. Settles when poly market_outcomes.csv writes the winner.

**Tiered entry (final form, commit 07a62ec):**
- T1: cost ≤ 0.90 (10%+ profit)
- T2: cost ≤ 0.85 (15%+ profit)
- T3: cost ≤ 0.80 (20%+ profit)
- Each tier opens at most once per (direction, market). Once T2 opens, T1 won't re-open. Only higher-profit tiers going forward.
- Max 6 trades per market (3 per direction) = $600 capital

Live status screen V3-style: header, tier definitions, live prices, current cost & active tier markers, open trades list with tier, last 10 closed trades with PnL, totals (W/L/PnL/ROI).

Output: /root/arb_virtual_trades.csv.

## Current state on Germany server (05/05 ~10:30 UTC)

**Running:**
- BRM (`screen brm`) — running with chainlink fix, BOT120 has target now
- arb_tracker (`screen arb_tracker`) — logging opportunities since 08:51
- arb_virtual (`screen arb_virtual`) — logging virtual trades. 2 OPEN trades for market 1777972500 (Direction B at T1 09:38, Direction A at T1 10:15). Both pending settlement.
- 28 multi-coin recorders + 7 Kalshi recorders + V3 retired

**Issues identified:**
1. **Trade #2 opened on stale data.** Bot saw cost=0.88 in market 1777972500 at 10:15 UTC, but the recorder for poly 15m had been DEAD since 09:26. Market 1777972500 actually ended at 09:50 (the trade was on a closed market). Lesson: bot needs freshness check on data feeds.
2. **Recorder for BTC 15m died sometime after 09:26 UTC.** Restarted manually at 10:27 UTC. With recorder fix, data is preserved.
3. **Settlement of trades #1 and #2 may never happen.** Recorder was dead during transition from 1777972500 → next, so 1777972500's outcome was never written to market_outcomes.csv. Trades stuck open.

## How to pull reports anytime, anywhere

**SSH access from any machine with the office private key (`C:\polybot\keys\office_key`):**
- Germany: `ssh -i C:\polybot\keys\office_key root@178.104.134.228`
- Helsinki: still blocked from office (need to copy key from home).

**Reports available on Germany:**
```bash
# arb tracker log (every distinct opportunity, START/END events)
tail /root/arb_tracker_log.csv

# arb virtual bot trades (full ledger)
cat /root/arb_virtual_trades.csv

# BRM trades (V3-style sim trade outcomes)
tail /root/polybot_repo/live/btc_5m/data_live_btc_5m_multicoin/trade_outcomes.csv

# Live screen of any bot
screen -S <name> -X hardcopy /tmp/snap.txt && cat /tmp/snap.txt
```

**Key screens:** `brm`, `arb_tracker`, `arb_virtual`. Plus 35+ recorder screens (rec_*, kalshi_*).

## Open items for next session

1. **Settlement of stuck trades #1 #2** — manually compute outcome from binance closing price at 09:50 UTC May 5, vs target $80,667.08. OR add fallback in bot for missing market_outcomes.
2. **Freshness check** — bot should refuse to open trades if data file's last_ts is more than ~30s old.
3. **Helsinki SSH access from office** — still pending. User needs to add office pubkey to Helsinki authorized_keys from home tonight: `ssh helsinki "echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPd32S2ZlxzomhVggp4tWy3l2SKZ4Gjn4Fcpy6XQ1F2S office' >> ~/.ssh/authorized_keys"`
4. **Live conversion of arb bot** — when virtual data validates the strategy. Need Kalshi account + Polymarket trading capability (Helsinki only).
5. **BRM threshold tuning** — analysis on V3 backup showed dist=50 + price≤0.70 gives ×8 vs current dist=60 + price≤0.50. Pending more data + decision.
