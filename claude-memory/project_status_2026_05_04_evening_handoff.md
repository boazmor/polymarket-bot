---
name: End of 04/05/2026 evening — handoff for tomorrow morning 8am
description: User went to sleep. Tracker running on Helsinki, market survey done, all key brainstorm saved. Tomorrow morning report due.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
End of late evening session 04/05/2026. User going to sleep, asked for report tomorrow 8am Israel time when arriving at office.

## Tomorrow morning report items (DO IN THIS ORDER)

**1. Passive lottery tracker results**
- Tracker running on Helsinki: `screen -S passive_lottery`
- Output: `/root/passive_lottery_log.csv`
- Started 20:18 UTC 04/05 with 83 markets pre-loaded, currently +$1.37 net
- By morning expect ~150-200 more markets tracked
- Compute: total markets, fills, wins, net profit, fill rate per side, win rate when filled
- Compare early fills (sec <240) vs late fills (sec ≥240) win rates — verify the suspected pattern with bigger sample

**2. Kalshi+Poly arb analysis with overnight data**
- Re-run the strict-arb analysis from `/tmp/strict_arb.py` on Hetzner
- Both recorders accumulate data overnight (~10 more hours)
- Should have ~16 hours of overlap by morning vs current ~2h
- Compute: opportunities ≥10%, average profit, frequency
- Confirm or refute the 45% profitable observation

**3. New platform survey results**
- See `project_market_survey_04may.md`
- TOP FIND: **OKX Event Contracts** — $0.01 minimum, 15min markets, API
- 3-way arb potential: Polymarket + Kalshi + OKX
- Recommend user check if OKX accepts Israeli signups

## What's running overnight

- Bot at home: LIVE BTC bot with $2/trade dual-buy fix (PID was 14396, may have changed)
- Helsinki: 7×5m recorders + new passive_lottery tracker
- Germany: 28×multi-window recorders + 7×Kalshi recorders + BRM (PID 1139779)
- Cron health check at 8am+8pm Israel auto-restarts any failed recorder
- Backfill cron every minute updates target_binance.csv

## Session's big insights (recap for context)

1. **Recorder fix** — Polymarket parser was dropping 81% of book updates AND WS connections silently died after 3 min. Fixed both. Now real bid/ask data flowing.

2. **Bot fix** — $1/trade was below Polymarket's effective minimum (after commission). Raised to $2. Live bot now actually trades (5/5 wins so far in latest cycle).

3. **Async place_buy** — was blocking the event loop for 1-3 seconds per buy. Now async. Bot more responsive.

4. **BOT120 dual-buy** — limit @ 0.65 + market @ dist≥68 with cap 0.80. Both can fire same market.

5. **Strategy reverse-engineering** — analyzed 153 of our trades + ~3000 counterparty wallets. Top winners use mid-late market trend-following at 0.55-0.70 (Popular-Insurrection style). Lottery at 0.01 is mostly a losing strategy in raw form.

6. **CROSS-PLATFORM ARBITRAGE** — Polymarket DOWN + Kalshi YES is profitable 45% of time, ≥10% in 15% of time, avg 10% profit. HIGH PRIORITY after Poly bot stable.

7. **5-stage strategy framework** — user's mental model for dividing 5min market into time-stage-specific strategies. Saved as `project_brm_5min_strategy_map.md`.

8. **BRM maturity timeline** — don't deepen speculatively. Wait 1 week for per-coin tuning, 1 month for day-of-week. Run as-is, accumulate data.

## Tomorrow when user arrives

Greet, summarize the 3 results above (lottery tracker, arb analysis, market survey). Don't volunteer big plans — let user direct based on what they want to focus on.

Save the morning's analysis numbers to a new dated memory file.
