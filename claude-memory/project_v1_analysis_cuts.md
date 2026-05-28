---
name: project-v1-analysis-cuts
description: Co-developed list of 16 analytical cuts for CONSENSUS_BTC_V1 — to run on historical recordings AND new live data
metadata: 
  node_type: memory
  type: project
  originSessionId: cb455403-2a0a-4be9-b453-c1f5e84301bc
---

Cuts list co-developed with user 27/05 — to run against CONSENSUS_BTC_V1 historical data (Poly+Predict+Limitless: 3 days, Gemini+Kalshi: from 27/05 onward) and against live bot data as it accumulates.

**Core constraint (user, 27/05):** we count DOLLARS, not trades. 12 windows per hour, 5 trades per hour is fine. Aggressive cuts that narrow trade count but raise hit rate are PREFERRED. Goal is profit, not volume.

**Direction rule (user, 27/05):** do NOT cut by UP vs DOWN. Strategy must win in both. Don't bias toward whichever direction is currently winning — that's a macro-regime trap.

**The 16 cuts:**

User-proposed:
1. **# agreements — find the outlier.** For each platform, hit rate when it voted ALONE against majority. If right < 50% when contrarian → noise. If right > 60% → value-adding contrarian.
2. **Silent majority.** Bucket by # silent platforms (3+ silent, 4+ silent). Hypothesis: high-silence windows are noise; should skip.
3. **# seconds in window.** Compare snapshot at sec 30/60/90/120/180/240. Combine with reversal-rate observation.
4. **Target itself.** Use the target value as a feature.
5. **Target agreement between platforms.** When all 5 targets cluster tight = consensus on reference price; when split = oracle divergence.
6. **Target × time.** As window progresses, reversal potential drops. Late entries on the same agreement should be safer.
7. **Reversal-after-N-agree, by distance.** Count how many windows reverse direction during the 5 min after 3+ or 4+ agreement formed. Correlate with distance between target and BTC price at the buy moment.
8. **# agreements × # seconds.** Joint matrix.

Claude-proposed:
9. **Distance from target.** Bucket by |distance| (0-20, 20-50, 50-100, 100+). Sweet spot likely 40-100 USD.
10. **Cross-oracle gap (Chainlink ↔ Binance).** When poly_target and predict_target diverge >$50, Chainlink is lagging — bet THE BINANCE LEAN.
11. **Price bucket.** Cheap ≤ 0.50 / mid 0.50-0.75 / expensive > 0.75. Find the sweet spot per variant.
12. **Pre-trade volatility (30 sec before).** Binance movement <20 / 20-50 / 50+ USD. Calm = stronger signal? Volatile = trap?
13. **NYC time-of-day.** Refresh past finding (Q4 quiet hours profitable, NYC 06:00 losing, NYC 19:00 winning) with V1 live data.
14. **Kalshi sub_pos (0/1/2).** Kalshi 15-min covers 3 Poly 5-min windows. Likely sub_pos=2 is most predictive (closest to Kalshi resolution).
15. **Pairwise agreement.** For each pair (poly+pred, poly+lim, poly+gem, ...) how predictive is JUST that pair agreeing? Identify the dominant pair.
16. **Single-platform predictive power.** Per platform alone, if it voted UP, what's actual UP rate? Knowledge score per source.

**How to apply:** when user asks for "analysis", "insights on V1", or "cuts", walk this list, run each cut on best-available data, report which ones gave actionable findings. Mark DONE in next memory update so we don't repeat.

**Data sources for backtest:**
- POLY: `/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv` + `market_outcomes.csv` (Chainlink resolution)
- PREDICT: `/root/data_predict_btc_5m/combined_per_second.csv` (Binance resolution = last binance_now vs strike)
- LIMITLESS: `/root/data_limitless_btc_5m/combined_per_second.csv` + `markets.csv` (Binance resolution vs target_price)
- GEMINI: `/root/data_gemini_btc_5m/combined_per_second.csv` (since 27/05 12:00 UTC)
- KALSHI: `/root/data_kalshi_btc_15m/combined_per_second.csv` (since 27/05 12:00 UTC)

Related: [[project-consensus-v1-bot]], [[reference-gemini-kalshi-recorders]], [[project-chainlink-latency-finding]], [[project-analysis-cuts-todo]].
