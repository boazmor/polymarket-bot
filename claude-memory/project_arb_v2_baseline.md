---
name: arb_virtual_bot V2 — measurement baseline 13:48 Israel time 05/05/2026
description: New dynamic-sizing formula deployed 05/05 ~13:48 Israel. Pre-13:48 trades are V1 (fixed $50/side, tiers, max 6/market). All results comparison must start AFTER 13:48.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User explicitly asked: "פעם הבאה שאשאל על התוצאות תתחיל מהשעה 1:48 עם הנוסחה החדשה."

## V2 formula (deployed commit d1271fd, 13:48 Israel 05/05/2026)

- Target $100/side (was $50)
- If smaller-side depth < $100: trade size = depth/2 per side
- Min: $5/side (skip below)
- Cap: 15 trades per 15min market (was 6)
- Cooldown: 5s between opens on same (direction, market)
- Single cost threshold ≤ 0.90 (no more T1/T2/T3 tiers)

## Filter rules unchanged from V1

- MAX_FEED_AGE_SEC = 30
- MAX_PRICE_GAP = 0.4 (skip extreme imbalanced asks)
- MAX_STRIKE_DIFF = 50 (skip when poly target vs kalshi strike differs by ≥$50)

## When pulling results

Filter `/root/arb_virtual_trades.csv` by `open_ts >= '2026-05-05 13:48:00'`
to evaluate V2 only. Pre-13:48 rows belong to V1 (different sizing/tier
logic) and would contaminate the comparison.

The CSV has 52 V1 trades closed before that time (n=52, W=34, L=15,
inv=$5200, PnL=+$981.97, ROI=+18.9%). Use those as V1 baseline only.
