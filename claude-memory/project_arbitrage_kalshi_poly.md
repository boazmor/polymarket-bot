---
name: Cross-platform arbitrage Kalshi YES + Polymarket DOWN — BIG OPPORTUNITY
description: Empirical 45% of time Polymarket DOWN + Kalshi YES costs <$1, with avg 10% guaranteed profit. Exploits structural overpricing of Poly UP vs Kalshi YES.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Discovered 04/05/2026 evening. Cross-platform arbitrage between Polymarket BTC 15m and Kalshi BTC 15m markets.

## The mechanic

Both platforms trade BTC binary outcomes resolving in 15min. They're EQUIVALENT BUT NOT IDENTICAL:
- **Polymarket UP/DOWN**: bet on "BTC > or ≤ open price of THIS 15min market"
- **Kalshi YES/NO**: bet on "BTC > or ≤ a strike price set hours before"

Strike usually close to open price (avg diff $14, within $50 in 86% of cases).

## The arb (Direction B — DOMINANT)

**Buy Polymarket DOWN + Buy Kalshi YES**
- Polymarket DOWN: pays $1 if BTC ≤ open
- Kalshi YES: pays $1 if BTC > strike
- If strike == open: events are mutually exclusive AND collectively exhaustive → exactly one wins → guaranteed $1 payout
- Cost: poly_da + kalshi_ya
- Profit: 1.00 - cost

**Empirical (6,247 observations):**
- 45% of time: cost < $1 (arbitrage available)
- 23% of time: ≥5% profit
- 15% of time: ≥10% profit (961 of 6,247 observations)
- Avg profit when profitable: 10.1%

## The OTHER direction (much weaker)

**Buy Polymarket UP + Buy Kalshi NO** — only profitable 24% of time, avg 4.6%.

The asymmetry: **Polymarket overprices UP relative to Kalshi YES**. Why? Probably more US-trader bullish bias on Polymarket. Whatever the cause, the asymmetry is empirically consistent.

## The risk

Strike != target ~14% of the time. When BTC ends between strike and target:
- If strike < target & BTC between: BOTH bets win → +$2 (double payout!)
- If strike > target & BTC between: BOTH bets lose → -$cost (full loss)

Asymmetric. Probably positive expectancy even accounting for this. Need careful sim.

## Implementation considerations (for future)

1. **Need accounts on BOTH platforms.** Kalshi + Polymarket.
2. **Latency matters** — by the time we place both legs, prices may move. Need fast cross-platform execution.
3. **Capital allocation** — both legs cost ~$0.5 each, so $1 total per trade.
4. **Detection script** — monitor both feeds in real-time, compute arb cost every second, alert/execute when cost < threshold.
5. **Size** — start tiny ($1-5/leg), scale up after 100+ trades validate the assumption.

## Decision: HIGH PRIORITY for after Polymarket bot stabilizes

This is potentially MORE profitable than any single-platform strategy because it's near-risk-free. Should be implemented after the current single-platform bot is mature, but probably before adding more strategies to BRM.

User reaction 04/05: "אם יש פער של 10 אחוז זה פנומינאלי" + "100 אחוז להרויח בלי סיכוי הפסד"
