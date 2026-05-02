---
name: Activity bucket analysis — quiet hours dominate
description: Discovery 2026-05-01 that V3's profit is concentrated in low-activity hours; PEAK hours have weakest ROI
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User asked 2026-04-30 evening to analyze the bot by NYC time AND by activity level (split into 4 quartiles). Computed on 2026-05-01 from 368 V3 settlements (3 days of data).

## Activity quartiles by trade volume per hour

| Quartile | NYC hours | IL hours | Trades | Win% | PnL | $/trade | ROI |
|---|---|---|---|---|---|---|---|
| Q1 PEAK | 0,10,11,13,20,23 | 3,6,7,17,18,20 | 129 | 67.4% | +$2,927 | $22.69 | +23.5% |
| Q2 HIGH | 3,5,6,9,16,22 | 5,10,12,13,16,23 | 102 | 65.7% | +$2,109 | $20.68 | +21.1% |
| Q3 MODERATE | 1,2,8,12,14,15 | 4,8,9,15,19,21 | 78 | 74.4% | +$1,849 | $23.71 | +24.5% |
| **Q4 QUIET** | **4,7,17,18,19,21** | **11,14,00,01,02,04** | 59 | **83.1%** | **+$3,741** | **$63.42** | **+63.6%** |

**Headline finding:** Q4 (the quietest hours) is **3x more profitable per trade** than Q1 (peak hours), with much higher win rate (83% vs 67%).

## Best individual hours

- **NYC 19:00 (IL 02:00)**: 11 trades, **100% win**, +$1,259 total, $114/trade ⭐
- **NYC 17:00 (IL 00:00)**: 8 trades, **100% win**, +$697 total, $87/trade ⭐
- **NYC 16:00 (IL 23:00)**: 17 trades, 82% win, +$1,175
- **NYC 15:00 (IL 22:00)**: 12 trades, 83% win, +$393
- **NYC 18:00 (IL 01:00)**: 10 trades, 80% win, +$580
- **NYC 21:00 (IL 04:00)**: 10 trades, 80% win, +$584

## Worst hour — bot should consider stopping or tightening

- **NYC 06:00 (IL 13:00)**: 18 trades, only 50% win, **-$748 total, -$42/trade** 🔴

This is the single losing hour. NYC 06:00 = US "pre-market" (1-2 hours before US market open). High activity (Q2 quartile) but bad outcomes — likely volatile pre-market BTC moves.

## What to do with this finding

### Option A — simple time gating (V2)
Add to bot: refuse to trade in NYC 06 hour (US pre-market).
Estimated impact: removes 18 trades (-$748 loss) and recoups +$42/trade × 18 = +$756 of relative gain.

### Option B — regime-aware parameters
- During Q1/Q2 PEAK/HIGH hours: use TIGHTER parameters (higher distance threshold, lower price cap) since competition is fiercer and edges are smaller.
- During Q4 QUIET hours: use the current parameters (or even more aggressive) — these are the golden hours.
- During NYC 06: stop entirely.

### Option C — sizing adjustment
Trade SMALL in PEAK hours, LARGE in QUIET hours. Same parameters, scaled position size by hour-of-day.

## Why this happens (hypothesis)

In QUIET hours:
- Fewer participants on Polymarket (Asian overnight, US late night)
- Order books are thinner — less liquidity
- Price discovery slower → BTC momentum signal becomes more reliable for the 5-min window
- Sophisticated traders less active → bot's edge larger

In PEAK hours:
- Many traders, deep books, fast price discovery
- Bot's signal less unique
- Edges compressed
- Still profitable but less dramatically

## Connection to existing strategy decisions

- **BOT120's `flow with direction` rule** works because momentum carries through. Quieter hours = momentum less interrupted by noise. Hence higher win rate.
- **BOT40's cheap-price strategy** works when sellers are willing to drop prices. Quieter hours = more "panic" sellers willing to hit low bids.

## LIVE BOT NYC HOURLY (17h, 83 settlements)

| NYC hour | IL hour | Trades | Win% | PnL |
|---|---|---|---|---|
| 00 | 07 | 6 | 83.3% | +$548 |
| 01 | 08 | 1 | 0% | -$100 (one bad sample) |
| 02 | 09 | 4 | 75% | +$404 |
| 03 | 10 | 6 | 33% | +$10 (mediocre) |
| 04 | 11 | 5 | 60% | +$112 |
| **05** | **12** | **4** | **0%** | **-$400** 🔴 |
| **06** | **13** | **6** | **0%** | **-$600** 🔴 |
| 13 | 20 | 3 | 100% | +$257 |
| 14 | 21 | 5 | 80% | +$321 |
| 15 | 22 | 4 | 75% | +$236 |
| 16 | 23 | 5 | 80% | +$408 |
| 18 | 01 | 4 | 75% | +$148 |
| 19 | 02 | 5 | 100% | +$879 ⭐ |
| 20 | 03 | 7 | 71% | +$430 |
| 21 | 04 | 6 | 83% | +$560 |
| 22 | 05 | 4 | 75% | +$391 |
| 23 | 06 | 8 | 87.5% | +$573 |

**Total LIVE: $4,178 in 17h. Of which -$1,000 came from just NYC 05-06 (10 trades, 0 wins).**

If we time-filter NYC 05-06 OUT, the bot would have made **$5,178 instead of $4,178** — a +24% boost from blocking 2 hours of bad trading.

NYC 05-06 = IL 12:00-14:00 (lunch hour) = US 1-3 hours BEFORE market open — typically very volatile pre-market BTC moves.

## REAL-TIME VALIDATION (added 2026-05-01 ~17h after LIVE bot started)

While analyzing V3's history I predicted NYC 06:00 (IL 13:00) is the bot's worst hour. Within the same hour I checked live LIVE_BTC_5M_V1 trades and found **the prediction validating in real time**:

The LIVE bot's last 25 BOT40 trades showed a **9-loss streak** spanning NYC 05:30-06:35 (IL 12:30-13:35), each one a -$100 loss in simulation. Cumulative damage in this streak: **$-900**.

If LIVE mode had been on with $100/trade real money:
- Without daily kill switch: $-900 real loss in 70 minutes.
- With $40 daily kill switch (now in code): would have stopped after the 1st or 2nd loss, capping damage at ~$100-200.

This is empirical proof that:
1. The activity-bucket analysis is predictive, not just descriptive.
2. The daily kill switch I added today is essential — without it, sustained losing streaks during predicted-bad hours could drain real money fast.
3. The bot **must** implement Option A (NYC 06 time filter) for V2 — it's the highest-value-per-line change available.

## Pending work this enables

- **V2 priority 1:** time-based filter — refuse to trade NYC 05:00-07:00 ET window (or whatever the precise bad window is once we have more data).
- V2 of the bot can implement Option B (regime-aware parameters) on top.
- Worth re-running this analysis on the LIVE bot data (with new params) once it has 24+h.
- Worth segmenting by BTC trend direction (was the period UP-trending or DOWN-trending?) to see if the quiet-hours edge holds in both regimes.
