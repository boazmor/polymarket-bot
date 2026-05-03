---
name: Morning resume 2026-05-03 — data scan + new platform research
description: Autonomous session results after user said "המשך". Recorder data audit + Hyperliquid/Limitless platform discovery + Kalshi 1h recorder built (paused).
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Session resumed 2026-05-03 ~05:30 Israel (02:30 UTC). User went off-screen and asked me to work autonomously. Three things to remember next time:

## What I verified

**44 processes still running.** Quick one missing from `screen -ls` (kululu V3) is actually alive as PID 797371 — runs in pts/0, not screen.

**Data scan across all 35 recording dirs (28 Polymarket Germany + 7 Polymarket Helsinki):**
- Coverage: BTC 5m has 21h, other 5m have 10.5h, all 15m/1h/4h/1d have 7.1h
- **DATA QUALITY ISSUE**: `MULTI_COIN_RECORDER.py` leaves `target_price` and `distance_abs` columns BLANK for 5m and 15m windows. Only 1d windows have these populated (BTC 1d shows 84% of rows have distance_abs ≥ 60).
- Liquidity at low prices (≤0.35) is < 10% of seconds for ALL coins. Bot will idle most of the time.
- Per-coin direction bias from up/down midpoint average (5m windows):
  - BTC 0.77/0.24, SOL 0.80/0.22, DOGE 0.71/0.33 — moderate UP
  - BNB 0.93/0.08, HYPE 0.93/0.08 — extreme UP (one-sided, no edge)
  - ETH 0.57/0.47 — balanced
  - XRP 0.43/0.59 — only DOWN-leaning coin

## Critical multi-coin architecture issues found

1. **`BOT_120_DISTANCE_THRESHOLD = 60` is dollar-absolute** — meaningless for DOGE ($0.20) or BNB ($600). Must be % of price or per-coin scaled before enabling other coins.
2. **Recorder distance gap** — must fix `MULTI_COIN_RECORDER.py` to fill `target_price` for short windows before any 5m/15m strategy can use distance gating.
3. **BNB and HYPE markets are one-sided** (extreme UP bias on Polymarket) — don't waste a slot enabling them in COIN_PARAMS until Polymarket pricing normalizes.

## New platform: Hyperliquid HIP-4 (verified)

- Launched mainnet 02/05/26. Currently ONE market: BTC daily binary, target $78,213, expiry 03/05 06:00 UTC, ~50/50 pricing
- API: `POST https://api.hyperliquid.xyz/info` with `{"type":"outcomeMeta"}` for meta, `{"type":"allMids"}` for prices, `{"type":"l2Book","coin":"#0"}` for orderbook
- Encoding: `#0` = Yes side of outcome 0, `#1` = No side. Formula: `10*outcome + side`
- ZERO entry fees. CLOB. No KYC. Israel not blocked (verified from home PC + Germany server)
- **Limitation**: only daily for now. Bot's 5m/15m strategy can't use it yet. Plan to add more windows in phased rollout.

## Other platforms researched

- **Limitless Exchange (Base L2)**: hourly + daily crypto markets, $200M Jan 2026 volume. Worth deeper investigation next session — needs Israel access check + API map.
- **Drift BET (Solana)**: focused on real-world events, not crypto price short-term. Skip.
- **XO Market, Predict.fun, Myriad, Hxro, Buffer Finance**: previously rejected (no 5/15min, low liquidity, or non-existent).

## Kalshi 1h recorder — built but PAUSED

User asked me to add Kalshi 1h parallel to Polymarket 1h. I built `KALSHI_RECORDER_1H.py` on Germany at `/root/research/multi_coin/`. Smoke-tested OK with BTC (picked KXBTCD-T78199.99 at-the-money strike). NOT deployed to screens yet because user noted Kalshi 1h is structurally different from Polymarket 1h:

- Polymarket 1h = single binary "up or down vs open price"
- Kalshi 1h = ~50 strikes per hourly event ("BTC > $X at close?")

Recorder picks the highest-volume strike (≈ATM) per event. Imperfect comparison to Polymarket but useful for liquidity / volume tracking. User asked for 3 options (skip / ATM / all strikes) — awaiting decision. If go-ahead given, deployment is `for c in btc eth sol xrp doge bnb hype; do screen -dmS kalshi_${c}_1h python3 ~/research/multi_coin/KALSHI_RECORDER_1H.py --coin ${c^^}; done`.

## Open tasks (in TaskList)
- #4 Document findings (this file is part of it; will add platform memory next)
- #7 Fix `MULTI_COIN_RECORDER.py` distance_abs gap for 5m/15m
- #9 Decide on Kalshi 1h recorder deployment (paused)

## Suggested next-session priorities (in order)

1. **Decide on Kalshi 1h** — 1 minute to deploy or skip
2. **Fix the recorder distance gap** — required before any non-BTC strategy work
3. **Make BOT_120_DISTANCE_THRESHOLD per-coin** — required before enabling ETH/SOL/XRP/DOGE in COIN_PARAMS
4. **Verify Limitless from Israel** — could be the next major platform
5. **Wait for Hyperliquid 5m/15m markets** — keep an eye on their HIP-4 expansion
