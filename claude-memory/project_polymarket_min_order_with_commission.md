---
name: Polymarket $1 minimum is NET of commission
description: Why MAX_BUY_USD must be > $1 — the buy commission gets deducted before the minimum check, so $1.00 always fails.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Polymarket's $1 minimum order size is enforced AFTER the buy commission is deducted. If you set MAX_BUY_USD = $1.00, the broker deducts commission from that and the remaining notional drops below $1 → order rejected with "below Polymarket $1 minimum".

**Why:** confirmed by user 03/05/26 morning after live diagnosis showed home bot rejected 8 consecutive buy opportunities at down_ask 0.27–0.30 (same opportunities V3 dry-run on Germany filled). Bot computed `order_notional = 0.99999...` and the `< 1.0` check failed every time.

**How to apply:**
- Never set `MAX_BUY_USD` to exactly 1.0 (or `BOT40_MAKER_SIZE_USD`). Minimum safe value is **2.0** (gives a ~5% commission buffer).
- Same rule applies to any future per-coin sizing: each coin's per-trade USD floor must be ≥ 2.0.
- Floating-point makes the symptom worse but the underlying issue is the commission. Even `MAX_BUY_USD = 1.05` would be marginal.
- Fixed in bot_config.py 03/05/26: MAX_BUY_USD 1.0 → 2.0, BOT40_MAKER_SIZE_USD 1.0 → 2.0.
