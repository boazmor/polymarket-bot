---
name: project-v2-filtered-bot
description: "CONSENSUS_BTC_V2 — filtered bot replacing V1 27/05. Requires Poly+Predict both vote, blocks dist 50-100, blocks NYC hours 3/9/11/14. Backtest expects 82.9% win, $7.24/day at $1/trade"
metadata: 
  node_type: memory
  type: project
  originSessionId: cb455403-2a0a-4be9-b453-c1f5e84301bc
---

CONSENSUS_BTC_V2 deployed Helsinki 27/05 17:14 UTC, replacing all 3 V1 variants (early, late, strict).

**Decision rule (decide_v2 in /root/live/consensus_v2/CONSENSUS_BTC_V2.py):**
1. Sec 90 reference (median over [80,100]).
2. Require BOTH Polymarket AND Predict.fun to vote the SAME direction at >= 0.60.
3. Skip if |distance from Poly target| is in [50, 100] USD.
4. Skip if NYC hour (UTC-4) is in {3, 9, 11, 14}.
5. Buy on cheaper of Poly or Predict for the chosen side.
6. $2/trade default — Polymarket min $1 net of 2% commission requires ≥ $2 raw.

Limitless, Gemini, Kalshi are observed only (recorded in n_total agreement count for telemetry, but don't gate the decision). Found in [[project-v1-historical-findings-2026-05-27]] that Limitless is noise (55% knowledge score) and that Lim/Gem/Kal never independently override the Poly+Pred signal.

**Backtest expectation (738 windows = 3 days):**
- 245 fires after filters
- 203 wins / 42 losses = 82.9% win
- Avg price 0.768, ROI 9% per trade
- 80 trades/day, $7.24/day at $1 nominal
- At $2/trade (real Poly min): ~$14.50/day
- At $50/trade if liquidity allows: ~$362/day

**Mode.** DRY-RUN only — `--live` still refuses in code. Awaiting 24h validation that live matches backtest's 82.9% before wiring real orders via py_clob_client_v2 + PredictTrader.

**Watchdog.** Wired in `/root/check_live_bots.sh` (cron */5 min). V1 variants removed.

**Files.** `/root/live/consensus_v2/CONSENSUS_BTC_V2.py` (bot), `consensus_v2.log` (stdout), `consensus_v2_decisions.csv` / `_trades.csv` / `_outcomes.csv` / `_limitless.csv` / `_gemini.csv` / `_kalshi.csv` (per-window data).

Related: [[project-consensus-v1-bot]] (predecessor — keep for reference), [[project-v1-historical-findings-2026-05-27]] (basis for the filters), [[project-v1-analysis-cuts]], [[reference-gemini-kalshi-recorders]].
