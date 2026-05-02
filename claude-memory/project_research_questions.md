---
name: Open research questions to test against accumulated data
description: Investigation hypotheses the user wants checked once we have enough simulation/live data
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
Research questions the user explicitly wants tested against the bot's accumulated data. Ordered by priority. Run these when we have enough hours of recording (V3 already has 32+h, LIVE_BTC_5M_V1 building from 30/04 17:20).

## 1. Hourly profit pattern (asked 2026-04-30 evening)
Break down V3's PnL by hour-of-day (and ideally by day-of-week). The user finds this interesting and wants to see when the bot earns most. Output should be:
- A table or chart of avg PnL per hour (00, 01, ..., 23) in some timezone (probably user's local Israel time, but also worth doing NYC time — see #2).
- Per bot (BOT40 / BOT120 / combined).
- Trade count per hour (so we don't draw conclusions from sparse buckets).

## 2. Day vs night by NYC time + ACTIVITY-BASED ANALYSIS (asked 2026-04-30 evening, expanded later that night)
**The user explicitly asked to save this question, then expanded the framing.** Polymarket markets are NYC-anchored ("April 30, 12:50PM-12:55PM ET" style). Many participants are US-based traders. The user's deeper insight: changes in profitability are likely driven by **participant behavior** — number of participants, volume of trades, depth of order books — and these vary by hour.

So the research isn't just "bucket by hour" — it's **"measure activity level, then correlate with bot performance"**.

### Step A — measure activity per hour
For each hour-of-day across the 32+h V3 dataset (and growing live data), compute:
- **Total order-book updates** observed (proxy for participant activity / order placement rate)
- **Total trade ticks** in the order book (real fills happening)
- **Average order-book depth** at price levels ≤0.35 (USD available)
- **Median spread** between best bid and best ask
- **Markets that had a target captured** (target render reliability sometimes degrades when activity is high — worth checking)

We have all of these in the recorded CSVs (`combined_per_second.csv`, `poly_book_ticks.csv`, etc).

### Step B — bucket the day into 4 quarters of activity
The user explicitly suggested splitting the 24-hour day into 4 buckets — but **NOT necessarily by clock**. Instead, by ACTIVITY level:
- **Q1 (peak):** highest 25% of hours by activity
- **Q2 (high):** next 25%
- **Q3 (moderate):** next 25%
- **Q4 (quiet):** lowest 25%

Then map: which clock hours fall into which quarter? Probably US market open = peak, weekend overnight = quiet, etc. — but **measure, don't assume**. We don't actually know Polymarket's hot hours yet.

### Step C — correlate with bot performance per bucket
For each activity quarter:
- BOT40 win rate, PnL, average entry price, fill rate
- BOT120 win rate, PnL, average distance at entry, average entry price
- Did the strategy work better in high-activity or low-activity periods?

### Step D — propose adjusted parameters
Based on findings, the user suggested the bot might benefit from **different parameters in different activity regimes**, e.g.:
- In high-activity times: maybe a TIGHTER distance threshold (more competition, smaller edge per signal, but more fills)
- In low-activity times: maybe a WIDER distance threshold (sparser opportunities, but each one cleaner)
- In quiet times: maybe the price-cap on BOT120 should be LOWER (no point overpaying when market is illiquid)

This could lead to a **regime-aware bot** that switches parameter sets based on observed Polymarket activity in real time. Not for V1, but a clear path for V2.

### Output for the user
A clean table:
```
Quarter   | Hours (UTC/NYC) | Avg activity/hr | BOT40 PnL | BOT120 PnL | Combined | Win rate | Avg fill price
Q1 peak   | ...             | ...             | ...       | ...        | ...      | ...      | ...
Q2 high   | ...
Q3 mod    | ...
Q4 quiet  | ...
```
Plus a chart of activity-by-hour, and a chart of bot PnL-by-hour overlaid.

## 4. NEW IDEA — BOT40 with distance trigger (asked 2026-05-01 morning)
The user proposed adding a distance-based trigger to BOT40 (which currently only triggers on cheap price). His exact words: "לקנות בבוט 40 בלימיט כבר במחיר 60. לקנות מחיר נמוך. ואם אין אז לחכות ל 68" — buy in BOT40 with limit at 60 already, low price, and if no [fill] then wait for 68.

**Ambiguity:** the "60" and "68" — are these distance values (like BOT120's 68 threshold) or prices? Need user clarification before implementing.

**Most likely interpretation:** BOT40 gains a SECOND entry path in addition to its current "cheap price" path:
- Path A (existing): buy if price ≤ 0.30 (with flow filter if |dist| ≥ 25)
- Path B (new): buy if |distance| ≥ 60 (regardless of price). If no fill at dist 60, wait — when distance reaches 68 (the BOT120 threshold), still try to fill.

This effectively makes BOT40 also a momentum-following bot in the early 0-40s window, not only a cheap-price taker. Could increase fill rate when there's a strong directional signal but order book isn't cheap.

**Risk:** could overlap with BOT120's logic and cause double-buys per market. Need to make sure BOT40+BOT120 don't both fire on the same signal.

Implement in V2 after V1 (current MAKER strategy with CLOB integration) is live and proven. Confirm interpretation with user first.

## 3. Direction asymmetry (no specific user ask but worth noting)
V3's BOT120 buys with the flow (UP if BTC > target, DOWN if BTC < target). Worth checking: is one direction more profitable than the other? In a downtrending market (like the 38h dataset analyzed earlier where DOWN won 68.5%), DOWN-side bets win more often. We should normalize for the trend direction in the period.

## How to compute these

Data sources:
- `/root/data_5m_dual/bot40_settlements.csv` — V3 BOT40 settlements
- `/root/data_5m_dual/bot120_settlements.csv` — V3 BOT120 settlements
- `/root/data_5m_dual/trade_outcomes.csv` — combined per-trade outcomes (probably has per-position detail)
- `/root/data_live_btc_5m_v1/...` — once it has enough data, same analysis for the new strategy

Each settlement row has a timestamp `ts`. Convert to NYC time (US/Eastern, account for DST), bucket, aggregate.
