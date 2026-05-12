---
name: arb_3way_live.py v1 deployed 12/05
description: First live 3-platform arbitrage bot running on Helsinki. Replaces V5 LIVE on the 15-min market. Uses ThreadPoolExecutor for parallel-if-4x-depth, sequential thin-first otherwise, top-up from 3rd platform, emergency sell only if excess >10%.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
Deployed 12/05 21:13 Israel under screen `arb_3way_live` on Helsinki.

**What v1 supports:**
- 4 of 6 candidate pairs from arb_v5_3way: A_POLY, B_POLY, A_LIM, LimUP_PolyDN
- Excluded: B_LIM and PolyUP_LimDN — both require Limitless DOWN orderbook ask, which the current LIMITLESS_RECORDER does NOT capture. ~57% of virtual PnL came from B_LIM, so adding the DOWN orderbook is the highest-value next step.
- Sizing: BASE $1.20 on min-price leg, CAP $7 on max-price leg, SKIP if shares*max > $7
- Liquidity gate: if BOTH legs depth >= 4x shares*price -> ThreadPoolExecutor parallel fire; else sequential thin-side-first with second leg sized to actual first fill
- Top-up: if shortfall after both legs, try the third (unused) platform with a same-outcome FAK BUY
- Emergency sell: only if remaining shortfall/larger > 10%; else accept the small imbalance
- Stop-on-loss: exits if any window with at least one trade closes with PnL < 0
- Window-block: after any emergency-sell, skip remaining trades in that 15-min window
- Wealth snapshot now includes Limitless USDC on Base alongside Poly USDC, Poly positions currentValue, Predict positions, and BNB USDT

**Live processes after deployment (Helsinki):**
- arb_3way_live (15-min, replaces V5 LIVE)
- arb_v7_live (5-min, unchanged)
- rec_limitless_15m + 7 multi-coin recorders + Predict 15m recorder

V5 LIVE killed cleanly. V6 LIVE on Hetzner unaffected (1h markets, separate bot).
Watchdog `check_live_bots.sh helsinki` updated to monitor arb_3way_live instead of arb_v5_live.

**Outstanding for v2:**
1. Extend LIMITLESS_RECORDER to capture both YES and NO orderbooks (or query Lim API on demand from inside the bot). This unlocks B_LIM and PolyUP_LimDN which together carry the majority of the virtual PnL.
2. Verify a real $1.20 FAK BUY clears Limitless min_size (still empirically unconfirmed; current ConditionalTokens approval also needed before any SELL leg works).
3. Watch for false WEALTH-delta stop-on-loss after the first trade settles — shared wallet between arb_3way and V7 LIVE means cross-bot wealth bleed is still a risk; mitigated by `window_pnl = 0 if trades_this_window == 0`.

**Critical bug guards baked in:**
- python3 -u so log flushes per line (today's silent 38-min stall lesson)
- Watchdog catches both process-death AND log-staleness >10min, auto-restart with same args
- Cancel-before-fallback in unwind to prevent orphan resting orders (V5 LIVE retrofit, preserved)
