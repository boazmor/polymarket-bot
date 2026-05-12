---
name: פרדיק משתמש בביננס +1, לא בפית
description: Predict.fun strike = Binance BTCUSDT price at second +1 from market open. Verified on 4 consecutive 15-min markets 08/05.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
**Discovery:** Predict.fun's binary up/down market strike comes from Binance BTCUSDT price at second +1 from market open epoch. NOT Pyth, NOT Chainlink.

**Why:** Verified by user-provided strikes from Predict.fun screen for 4 consecutive markets on 08/05:

  שוק 1778214600 (04:30 UTC) — Predict 79,729 — Binance +1: 79,729.77 — diff 0.77
  שוק 1778215500 (04:45 UTC) — Predict 79,567.33 — Binance +1: 79,567.34 — diff 0.01
  שוק 1778216400 (05:00 UTC) — Predict 79,655 — Binance +1: 79,655.23 — diff 0.23
  שוק 1778217300 (05:15 UTC) — Predict 79,639.68 — Binance +1: 79,639.69 — diff 0.01

Pyth was off by up to $14 on the same markets. Binance is essentially exact (sub-dollar always).

**How to apply:**
- In all bots that estimate Predict.fun strike (arb_v4_4way, arb_v5_amiti, arb_v5_poly_predict_4seasons, arb_virtual_bot_v5):
  - Replace `lookup_pyth_strike(market_open_epoch)` with `lookup_binance_strike(market_open_epoch)`
  - Read from existing `/root/data_btc_15m_research/combined_per_second.csv`
  - Take row where `market_epoch == market_open_epoch` AND `sec_from_start == 1` (column 5)
  - Return `binance_price` (column 6)
- The Pyth recorder is no longer needed for Predict's strike. Can keep running for other research, but bots don't need it.
- The previous BOTH_LOST_DANGER trades from V5 are explained by this gap. Many of them would have been correctly identified as dangerous (large strike spread) if Binance was used instead of Pyth.
