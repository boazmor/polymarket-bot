---
name: project-v1-historical-findings-2026-05-27
description: 7 key findings from backtesting V1 strategy on 3 days of Poly+Predict+Limitless data (738 windows) — actionable rules for bot tuning
metadata: 
  node_type: memory
  type: project
  originSessionId: cb455403-2a0a-4be9-b453-c1f5e84301bc
---

Backtested CONSENSUS_BTC_V1 strategy on 738 windows of historical Poly+Predict+Limitless 5-min BTC data (24-27 May). Gemini+Kalshi excluded (only 5h of data). Script: `/root/reports/backtest_v1_historical.py`. Cuts 1, 2, 3, 6, 8, 9, 10, 11, 13, 15, 16 from [[project-v1-analysis-cuts]] executed.

**Baseline (sec=90, THR=0.60):**
- Loose 2+: 75.6% win, +$44.86 over 602 fires (3 days)
- Loose 3+: 79.2% win, +$59.85 over 453 fires ← BEST
- Strict 2+: 75.7%, +$21.05
- Strict 3+: 79.1%, +$46.18

**7 actionable findings:**

1. **Limitless is noise, not signal.** Knowledge score: 55% correct on UP votes, 54.6% on DOWN. When Lim votes ALONE against majority (287 windows = 39% of sample), it's right only 47% — worse than coin flip. Pred+Lim agreement WITHOUT Poly = -$2.05 PnL. **Recommendation:** drop Limitless from decision logic, OR require Poly to always be in the agreeing set.

2. **Silent kills profit.** 0 silent platforms (all 3 voting): +$60.29 / 79.3% win. 1 silent: -$15.43 / 64.2%. **Recommendation:** require ALL platforms (within available 3) to vote — no silent permitted.

3. **Best timing: sec=90 with min_N=3.** Sec=240 has higher win rate (85.5%) but lower PnL due to expensive prices. Sec=90 + min_N=3 is the optimum.

4. **Distance 50-100 USD = danger zone.** 67.9% win, -$16.57. Other distance buckets all profitable (20-50: 89%; 100-200: 78.5%, +$51.42; 200+: 93%). **Recommendation:** SKIP if |distance_from_target| is between 50 and 100 USD.

5. **Cross-oracle gap >$100 = sweet spot.** When poly_target (Chainlink) and pred_target (Binance) differ by >$100, win rate 79.2% and +$37.22 PnL on 221 fires. The bigger the Chainlink lag, the better the opportunity. (Confirms [[project-chainlink-latency-finding]].)

6. **NYC hour matters strongly.** Worst hours (avoid): 09 (-$7.65), 14 (-$8.61), 11 (-$5.43), 03 (-$6.10), 17 (-$2.77). Best hours: 02 (+$11.36, 95%), 13 (+$11.38, 88%), 22 (+$9.33, 90%), 12 (+$8.53). Refresh [[project-findings-activity-buckets]] with this.

7. **Poly must always be in the agreeing pair.** Poly+Pred = +$59.85. Poly+Lim = +$58.86. Pred+Lim (without Poly) = -$2.05. **Recommendation:** require Poly UP/DOWN to match the consensus side; ignore signals where only non-Poly platforms agree.

**Composite rule to test next:**
- sec=90
- min_N=3 (loose)
- Poly required in agreeing set
- |distance| NOT in [50, 100]
- NYC hour NOT in {3, 9, 11, 14}
- Cross-oracle gap >$50 preferred (don't gate on this yet — too restrictive in some windows)

Expected: ~60-70% of current fires retained, win rate likely 82-85%, PnL per trade ~$0.20-0.25 average. ~$30-40 per 3 days at $2/trade scaling linearly with invest.

**Sample size caveats:** 738 windows = ~3 days. Standard error on 75% win rate with 600 fires is ~1.8pp, so findings are statistically meaningful. NYC hour cells have only ~25 fires each → ±10pp error per hour. The hour-rule should be re-verified with another week of data before hard-coding.

Related: [[project-v1-analysis-cuts]], [[project-consensus-v1-bot]], [[project-chainlink-latency-finding]], [[project-findings-activity-buckets]].
