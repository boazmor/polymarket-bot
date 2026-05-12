---
name: 3-way arb live strategy spec (final, 12/05)
description: Final user-approved spec for arb_3way_live.py covering ordering, parallel-vs-sequential by liquidity, top-up from 3rd platform, emergency-sell threshold. Use this when building or modifying the orchestrator.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
User-approved strategy for the 3-platform live arb bot, finalized 12/05/2026.

**Sizing** — already in V5/V6/V7:
- BASE = $1.20 on the smaller-price leg
- shares = round(1.20 / min_price, 2)
- If shares × max_price > $7 → SKIP the opportunity entirely
- Goal: minimize worst-case loss to ~$8 per unhedged trade

**Buying order — depends on liquidity:**

1. **Parallel fire** when BOTH sides have liquidity ≥ 4× target shares (≥ 4 × planned_shares × respective_price in USD depth):
   - Fire FAK BUY on Polymarket + FAK BUY on second platform simultaneously via concurrent async tasks
   - Both expected to fill in full given the depth headroom
   - Fastest path; preferred when conditions allow

2. **Sequential fire** otherwise:
   - Identify the side with SMALLER depth → buy that side FIRST with FAK
   - Read actual filled_shares from the FAK response
   - Size the SECOND side to match that filled amount → FAK BUY
   - Avoids over-buying on the deeper side when the thin side might not fill

**Top-up from 3rd platform** when shortfall remains:
- Compute shortfall = larger_filled - smaller_filled
- If shortfall > 0 → try same-outcome FAK BUY on the 3rd platform
- Even another round across all 3 platforms is acceptable if liquidity allows ("עוד סיבוב השלמות")

**Emergency sell** — only when shortfall after top-up exceeds 10% of larger side:
- If excess_shares / larger_filled > 0.10 → sell the excess via FAK SELL on the side that over-filled
- If excess ≤ 10% → leave unhedged, accept the small imbalance
- Example: 10 vs 9 shares → don't sell. 100 vs 85 → sell 15

**Stop conditions** (inherited from V5/V6/V7):
- Max trades per window: 1 for 5-min, 2 for 15min/1h
- Stop-on-loss: exit immediately if any window closes with PnL < 0 and at least 1 trade happened
- Window-block-after-unhedge: skip remaining trades in the window if last trade ended unhedged
- Skip below min notional: if any side would be under $1.20 due to fee deduction, skip

**Platforms covered:** Polymarket (Polygon, Chainlink) + Predict.fun (BNB, Binance) + Limitless (Base, Chainlink). Cross-oracle pairs (Poly/Lim vs Predict) AND same-oracle pairs (Poly vs Lim) both eligible — 6 candidate pairs per market window.
