---
name: BRM 5-minute strategy map — brainstorm 04/05/2026
description: Conceptual framework dividing the 5-minute market into 5+ time stages, each with its own buy strategy. Brainstorm phase, awaits validation against recordings before building.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User-led brainstorm 04/05/2026 evening. Goal: divide the 300-second 5-min market into time stages, each with its own optimal strategy.

**User's principle:** "לכל מקום על פני ה-5 דקות יש אסטרטגיה" — every point in the market lifecycle has an appropriate strategy. Each stage uses different relationship between distance + price + reversal probability.

## The 5+ Stage Framework

**Stage 1 — Opening (sec 0-30):** Already implemented as BOT_30/40 maker phase
- Maker orders at price levels [0.28, 0.29, 0.30]
- BOT40_FLOW_DIST_THRESHOLD = 25 (only fires if distance ≥ 25 in the buy direction)
- Bet: cheap entry assuming 50% chance + ~250% payout

**Stage 2 — Late opening (sec 30-40):** Already implemented as BOT_40 fallback
- Fallback price 0.35
- Same distance filter ≥ 25
- Bet: slightly more expensive entry, still cheap-buy philosophy

**Stage 3 — Early-mid breakthrough (sec 0-120):** Already implemented as BOT_120
- distance ≥ 60 (absolute), maker @ 0.65 limit (raised from 0.50 today)
- Bet: large directional move continues — buy the side that's moving

**Stage 4 — Trend confirmation (sec 120-200):** TO BE BUILT — BOT_FOLLOW
- distance ≥ +50 in direction we buy, price ≤ 0.70
- Bet: market has decided, buy the favorite (Popular-Insurrection's strategy)
- **User skeptical, wants recording validation before building**

**Stage 5a — Mid-late reversal opp (sec 240-280):** TO BE BUILT
- distance small (|distance| ≤ 30), price ≤ 0.05-0.10
- Bet: market expects "X side wins" but a sudden BTC swing could flip it

**Stage 5b — Final-second deep lottery (sec 280-300):** TO BE BUILT
- limit @ 0.01 (the deepest possible)
- Bet: panic seller will dump cheap, our limit catches it; if BTC swings → 100x payout
- Per user: "אפשר לשים לימיט 0.01 ויהיה מי שימכור במרקט"

## ⭐ TOP PRIORITY: BOT_PASSIVE_LOTTERY (added 04/05 evening)

User insight: Popular-Insurrection's strategy is **passive limit @ 0.01 placed at market open**, NOT timed action. He plants the order at sec 0-5, waits the entire 5 minutes for a panic-seller to dump cheap onto his limit.

**Why this works:**
- Cost = $0 if not filled (limit didn't match → cancelled at rollover)
- Cost = $1 if filled (Polymarket minimum)
- Win payout when right = $100 (1/0.01 = 100x)
- Empirically (49-market sample): 0.01 ASK appears in ~3% of markets, when prior distance was ≥30 same direction.
- Of fills, ~32% win (second reversal happens) → expected return per fill ≈ +3000%

**Implementation spec:**
- At sec 0-5 of every new 5-min market: place 2 limit BUY orders
  - UP token at 0.01, size 100 shares ($1)
  - DOWN token at 0.01, size 100 shares ($1)
- Order type: GTC limit (sit until filled or cancelled)
- At rollover: cancel any unfilled limits
- If filled: position recorded, settles automatically at market resolution
- Total capital tied up per market: $2 max (and only when filled)

**Theoretical scale (assuming numbers hold):**
- 288 markets/day × 30 days = 8,640 markets/month per coin
- 3% fill rate × 2 sides = 6% × 8640 = ~518 fills/month
- 32% win × 518 = 166 wins × $99 profit = $16,434
- Minus 352 losses × $1 = $352
- Net: ~$16,000/month per coin (theoretical, before market reaction to multiple bots doing this)

**Risks:**
- Tiny sample (31 opportunities, 1 with 0.01 fill) — actual fill rate could be much lower
- Other bots doing the same compete for the same liquidity
- Polymarket might add minimum-trade-size enforcement
- Sudden market freeze prevents cancellation

**This is the PRIMARY strategy to validate and build first. Higher priority than Stage 4/5a above.**

## Validation Required (before building any new module)

1. **Stage 4** — query recordings: of markets where dist ≥ +50 occurred in sec 120-200, what % of the same direction WON the final outcome? Need ≥ 50 markets sample.

2. **Stage 5a/5b** — query recordings: opportunities + win rate per (sec window, distance threshold, price level). Initial 49-market sample showed +173% expected return at sec 240-300 with dist ≤ 30 and price ≤ 0.10. Confirm with larger sample.

3. **Competitor scan** — for each new stage, identify the wallets currently using that strategy on Polymarket. Understand their exact entry conditions to avoid being "front-run" by them.
   - Stage 4 candidate: Popular-Insurrection (validated)
   - Stage 5b candidate: many lottery wallets identified, most LOSE money (only ~2 actually profitable in their lottery activity)

## Implementation Plan (when ready)

1. Wait for ≥ 7 days of fresh recordings across all coins (recorder fixed today, currently <1 day of data with full book).
2. Run validation backtests for Stage 4 + Stage 5a + Stage 5b with confidence intervals.
3. Build modules ONE AT A TIME in `bot_engine/strategy.py`. Add display panel per module in BRM.
4. Each module per coin enabled/disabled separately via COIN_PARAMS.
5. Start with BTC only, all new modules at $1 size, observe for a week before scaling.

## Key Constraint

User wants HIGH-PAYOUT strategies, not scalping. Stages 4 and 5 must keep this philosophy:
- Stage 4 = +8% per trade (lower payout than ours but higher win rate)
- Stage 5 = +173% per trade (true asymmetric)
- Together they fill the gaps in our 0-120s coverage with complementary risk profiles.
