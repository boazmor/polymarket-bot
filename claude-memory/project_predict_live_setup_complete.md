---
name: Predict.fun live trading infrastructure complete (10/05 night)
description: Full live-trading path on Predict.fun was wired and verified end-to-end. EOA wallet funded with USDT on BNB Chain, allowances + token approvals set, $2 buy+sell smoke test passed, combined Poly+Predict smoke test passed.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
End of 10/05 → 11/05 overnight session: live Predict.fun trading is technically working from MY_EOA on BNB Chain. Bot wiring (`arb_v5_live.py`) built, ran one 15-min window with 0 trades due to (a) the Predict recorder not running on Helsinki and (b) no arb opportunity in that window. Recorder issue fixed, second run started 22:46 UTC.

**Why:** Several discrete sub-projects had to land in sequence — exporting funds from the Predict.fun Privy smart wallet, bridging POL→BNB for gas, setting two kinds of contract approvals (USDT allowance + CTF token approval), and figuring out the exact `POST /v1/orders` body format (camelCase + `data.order` wrapper + `pricePerShare` as wei18 string + `signer` field name in auth + 0x-prefixed signature).

**How to apply:**

Files installed on Helsinki:
- `/root/predict_trader.py` — reusable PredictTrader class (auth, get_market, get_orderbook, get_orders, get_positions, place_limit)
- `/root/arb_v5_live.py` — live trading wrapper that mirrors arb_virtual_bot_v5.py logic but places real orders on both Polymarket and Predict.fun. Caps: --max-windows, --max-trades-per-window, --invest
- `/root/pnl_loop.py` — runs continuously, writes `/root/pnl_status.txt` every 30s, history to `/root/pnl_history.csv`
- `/root/status_report.py` — one-shot reporter
- `/root/PREDICT_RECORDER_15M_V2.py` — copied from hetzner; now running under screen `rec_predict_15m`

Critical addresses/values (BNB Chain):
- MY_EOA `0x73a6dC847cE7B672F98d14e9F239d97a2C9FdF46` — same EOA as Polymarket
- Predict smart wallet (now empty after withdrawal): `0xd76a7ECc8b2a1F01120FBE73BEDa682c1ffd93E2`
- BNB Chain exchanges (allowances set, all 4): CTF, NEG_RISK_CTF, YIELD_BEARING_CTF, YIELD_BEARING_NEG_RISK_CTF
- USDT contract on BNB: `0x55d398326f99059fF775485246999027B3197955`
- For 15-min BTC markets specifically: `isNegRisk=False`, `isYieldBearing=False`, `feeRateBps=200`. Resolution source is Chainlink BTC/USDT data stream (NOT Pyth, NOT Binance).

POST /v1/orders body format (took multiple probes to discover):
```json
{
  "data": {
    "order": { ... camelCase fields, side=0/1 int, signatureType=0=EOA, signature with 0x prefix ... },
    "pricePerShare": "<int(price * 1e18) as string>",
    "strategy": "LIMIT"
  }
}
```

Auth POST /v1/auth body:
```json
{"signer": "0x...", "signature": "0x...", "message": "..."}
```
NOTE: field name is `signer` not `address` — this was a 400 trap.

Cancel orders is on-chain (CTF Exchange `cancelOrders` transaction). The API does NOT support DELETE/POST cancel. Orders auto-expire when market closes.

Approvals: use SDK's `OrderBuilder.set_approvals(is_yield_bearing=False)` AND again with `is_yield_bearing=True`. This calls 10 transactions total (CTF/NegRisk × allowance/approval × YB/non-YB). Cost ≈ $0.20 in BNB gas.

Smoke results 10/05 22:00-22:30 UTC:
- $2 round-trip on Predict.fun market 319060 — bought Up 2.37 shares at $0.84, sold 2.36 at $0.92 → +$0.17 profit (market moved in our favor)
- Combined $4 test on market 319446 — bought Up 4.88 shares on Predict at $0.41, bought Down 3.28 shares on Polymarket at $0.61. BTC settled UP → Predict paid $4.78, Polymarket paid $0 → net +$0.78
- Earlier test mistake: a "far-from-market" $0.10 limit BUY actually filled (someone sold at that price), lost $1 when Down won. **Lesson: even unrealistic limit prices can fill — don't assume safety from being far out of market.**

Open per-platform state at session pause:
- MY_EOA BNB: 0.0228 BNB ($14.83 gas), 97.16 USDT
- Polymarket Safe: 231.24 USDC (was 221.87 — gained $9.37 mid-session; source not fully reconciled, possibly maker rewards from boosted 15-min markets)
- Combined balance vs start-of-day baseline: +$6.53 cumulative

Issues still to resolve:
- Predict recorder on Helsinki was NOT running until 22:46. This caused first V5 LIVE run to see stale feed and skip all opportunities. Auto-fix recorder cron should be extended to cover Predict 15m recorder.
- WON Predict positions (settled but not redeemed) are not counted by `pnl_loop.py`. The 4.78 shares from the smoke test still show in /v1/positions with status=WON but valueUsd=$0.01 instead of $4.78 — investigate whether redeem is needed or if the UI is misleading.
- Combined smoke test bought OPPOSITE outcomes (Predict Up + Poly Down) — not a real arb, just a stress test of both APIs. Future tests should target true arb pairs where cost < 0.90.
