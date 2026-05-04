---
name: BRM maturity timeline — when to deepen what
description: Two parallel tracks — recording data accumulation + BRM operational shakedown. Both need time before any deep parameter tuning is meaningful.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User's framing 04/05/2026 evening: BRM is BUILT (structure exists), but **when** do we deepen it? Two needs gate the answer.

## Track 1 — Recording data accumulation

For meaningful parameter tuning we need:
- **Per-coin distance/price values:** ≥1,000 markets per coin per window. Currently <100. **Need ~1 week of clean recording per coin.**
- **Time-of-day analysis:** profitable/losing hours need ≥2-3 cycles each = need ≥3 days of NYC daily cycle.
- **Day-of-week analysis:** weekend vs weekday differences need ≥3-4 weekends = **need ~1 month**.
- **Holidays/special events:** even longer to cross-validate.

Recording has been clean since 04/05 ~13:30 UTC (after the parser fix). So:
- 1 week = ~11/05/26 → enough for distance/price tuning per coin
- 1 month = ~04/06/26 → enough for day-of-week analysis

## Track 2 — BRM operational shakedown

Even with imperfect parameters, BRM must run live to:
- Verify all modules wire up correctly
- Catch runtime errors before they matter
- Establish a baseline for comparison
- Build operator confidence in the system

Currently BRM runs in dry-run mode on Germany. Need to:
- Let it run continuously without tweaking for at least a week
- Watch for crashes, edge cases, race conditions
- Once stable → switch one coin to $1 live, observe another week
- Then scale gradually

## When to deepen

**Don't deepen until both tracks mature:**

| Action | Earliest |
|---|---|
| Add per-coin distance/price thresholds | After 1 week of clean recording (~11/05) |
| Add BOT_FOLLOW (Stage 4) | After validation against ≥500 markets |
| Add BOT_PASSIVE_LOTTERY | After validation; sample currently 59 markets only |
| Add hour-of-day filters | After 2 weeks of data |
| Add day-of-week filters | After 1 month |

## Don't add modules speculatively

Resist the temptation to add untested modules. The data we have now is ~5 hours of fresh recordings. Any decision based on this is probably wrong.

**The right rhythm:**
1. Let BRM run as-is (current modules: BOT_30/40/120 + new dual-buy)
2. Let recordings accumulate
3. Every Sunday: run analysis on accumulated week's data
4. Decide what to deepen based on what the data actually says
5. Commit changes Monday, run a week, repeat

## What to do THIS WEEK

- BRM keeps running in dry-run on Germany — don't touch
- Live bot at home keeps running with $2/trade + dual-buy fix — don't touch
- Recorders keep running across both servers — auto-fix cron handles failures
- No new modules until ≥1 week of data accumulated
- Optionally: build `analyze_weekly.py` script to be run every Sunday (groundwork for future tuning)
