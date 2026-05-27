---
name: project-consensus-v1-bot
description: "CONSENSUS_BTC_V1 dry-run bot running on Helsinki — Poly+Predict consensus strategy with Limitless side-recording, awaiting 24h dry-run validation before live"
metadata: 
  node_type: memory
  type: project
  originSessionId: cb455403-2a0a-4be9-b453-c1f5e84301bc
---

CONSENSUS_BTC_V1 bot, deployed 27/05/2026 11:26 UTC on Helsinki.

**Strategy.** At sec=90 of each BTC 5-min window, take median Poly UP/DOWN ask and median Predict YES/NO ask over [80,100]. If both Poly and Predict price the same side at >= 0.60, buy that side on whichever platform is cheaper. Hold to expiry, each platform resolves on its own oracle.

**Backtest.** Run on 4 days of BTC data: best cell sec=90, thr=0.60 → +6.0% ROI, 79% win rate, 499 trades. Poly is cheaper in 80-95% of consensus windows (Chainlink lag — Poly's price hasn't caught up to Binance-based consensus yet). Backtest scripts in `/root/reports/backtest_btc_cuts.py`, `backtest_btc_agreement.py`, `backtest_outcomes_per_oracle.py`, `backtest_poly_predict_arb.py`.

**Bot location.** `/root/live/consensus_v1/CONSENSUS_BTC_V1.py` on Helsinki. Running under screen `consensus_v1`. Logs to `consensus_v1.log` + 3 CSVs (decisions / trades / limitless snapshots).

**Mode.** DRY-RUN only — `--live` is intentionally not wired yet. Will read recorder files (does NOT touch them) and log decisions. Bot will refuse if `--live` is passed.

**Why:** validate the 79% win rate on live market data before risking real money. Backtest is only 4 days. Want at least 12-24h of live decisions to confirm signal stability.

**How to apply:** check the bot's `consensus_v1_decisions.csv` after 12-24h to see hit rate vs backtest's 79%. If aligned, next step is to add live order placement via py_clob_client_v2 (Poly) and PredictTrader (Predict.fun) — both are already imported in `arb_v5_3way_live.py` we can crib from. Limitless is record-only for now; expansion to use Limitless as a 3rd consensus signal or as a trading venue depends on what `consensus_v1_limitless.csv` shows.

Related: [[reference-helsinki-server]], [[project-predict-live-setup-complete]], [[project-limitless-trader-built]], [[project-arb-3way-live-v1]].
