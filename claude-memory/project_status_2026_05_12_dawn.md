---
name: End of overnight 11/05→12/05 — 3 live bots running with $1.20/$7 sizing
description: V5/V6/V7 LIVE all running with new sizing rules (BASE $1.20 on smaller leg, CAP $7 on larger leg), 2/2/1 trades per window respectively, wealth-snapshot now includes Polymarket position currentValue from /data-api so unsettled positions no longer falsely trigger stop-on-loss.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
End of session 12/05 02:43 UTC. User going to sleep.

**Live bots running:**
- V5 LIVE on Helsinki — 15min markets, --max-trades-per-window 2 --invest 7.0
- V6 LIVE on Hetzner — 1h markets, --max-trades-per-window 2 --invest 7.0
- V7 LIVE on Helsinki — 5min markets, --max-trades-per-window 1 --invest 7.0

**Tonight's session highlights:**

1. **Sizing logic rewritten** to user's spec: smaller-leg gets BASE $1.20 (so predict $1 minimum is never violated), larger-leg capped at $7. shares = 1.20 / min_price. If shares × max_price > 7, skip the trade entirely. This minimizes worst-case loss to ~$8 per unhedged trade vs the older ~$50.

2. **Wealth snapshot bug discovered + fixed.** Earlier the bot would buy on Polymarket, USDC drops, but the new shares we own are NOT in the wealth calc, so wealth shows artificial drop and stop-on-loss fires falsely. Fix: snapshot_wealth now fetches /data-api/positions for the Safe address and sums currentValue. Wealth now reflects unsettled positions correctly.

3. **Limitless integration started:** $50 USDC bridged to Base (have $49.86). $1 ETH-equivalent bridged for gas (got 0.000685 ETH). API token generated with Trading scope only (Account Creation, Delegated Signing, Withdrawal locked behind partner access). `limitless-sdk` v1.0.9 exists on PyPI. Next step is the one-time USDC approval transaction on Base, then writing `limitless_trader.py`. NOT done yet.

4. **3-WAY virtual bots running** (arb_v5_3way, arb_v6_3way) — they monitor Polymarket + Predict.fun + Limitless and pick the cheapest of 6 candidate arb pairs (4 cross-oracle Poly/Lim vs Predict, 2 same-oracle Poly vs Lim). V5_3WAY made 84 virtual trades in 5h showing Limitless contributes meaningfully when Poly depth is thin.

5. **V7 virtual built** as a clone of V5 BASIC but on 5min data with the same filter set as V5 LIVE.

**Cumulative loss tonight (live bots):**
- Started session ~$330
- Ended session ~$255
- Net loss ~$75 from a series of trades that hit:
  - $0.92 Predict order rejected (below $1 min) → unhedged poly position
  - Polymarket retry filling at $0.79 while market moved → orphan poly
  - Unwind GTC limit not filling → market kept moving against position
- All fixed by tonight's sizing + cancel-before-fallback + window-block-after-unhedge patches.

**Outstanding work:**
1. Build `limitless_trader.py` and integrate Limitless live trading into V5/V6/V7. Foundation laid, USDC/ETH on Base ready, API key in .env. ~3 hours.
2. Verify the wealth snapshot fix actually prevents the false-loss issue (need a trade to trigger and observe).
3. Decide whether to run V5 BASIC virtual baseline vs V5_3WAY virtual side-by-side for a full day to A/B Limitless contribution.
