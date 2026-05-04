---
name: BRM late-market strategies — 2 new modules to add
description: Plan for adding BOT_FOLLOW (Popular-Insurrection mimicry) and BOT_LATE_LOTTERY (small-distance reversal) to BRM after data accumulates.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Discussed 04/05/2026 evening. Two new strategies identified by analyzing top profitable Polymarket bots, validated against limited recording data.

## Module A — BOT_FOLLOW (mimics Popular-Insurrection)

Wallet 0xeebde7a0e019a63e6b476eb425505b7b3e6eba30 has $619K total PnL using this pattern. Verified against our 5-market overlap.

**Trigger conditions (ALL must hold):**
- sec_from_start: 100-180
- distance from open: ≥ +50 (in the direction of the side we buy)
- ask price ≤ 0.70 on that side

**Action:** Buy $2 worth at the ask.

**Expected performance (limited sample):**
- 67% win rate at distance +50 to +100, avg price 0.62
- ~+8% return per trade

**Why stop at sec 180?** Hypothesis: after 180s, market less stable — momentum bot risk increases. To verify: extend to 280s in backtest once more data accumulates.

## Module B — BOT_LATE_LOTTERY (reversal bet)

User's idea: bet on price reversal in last seconds when distance is small (BTC hasn't really decided).

**Trigger conditions (ALL must hold):**
- sec_from_start: 240-290 (last minute)
- |distance| ≤ 30 (market still uncertain)
- ask ≤ 0.10 on at least one side

**Action:** Place $1 limit at the cheap side. Could place on both sides simultaneously for safety.

**Expected performance (TINY sample — 7 markets, 2 wins):**
- 29% win rate
- +186% expected return per trade (high variance — payout ~10x per win)

**Confidence:** Low. Need 100+ markets of data before fully trusting. Implement and let it run on small size to gather data.

## Implementation notes

- Add as separate modules in `bot_engine/strategy.py`
- Each module gets its own panel in BRM display (user wants visual separation)
- Each module tracks its own positions/PnL separately (don't mix with BOT_120)
- Per-coin params dict needs new keys: `bot_follow_enabled`, `bot_lottery_enabled`, plus thresholds
- Start enabled for BTC only, others disabled until we see results

## Why this matches user's preference

User explicitly prefers low-volume high-payout strategies (saved in feedback_strategy_preference.md):
- BOT_FOLLOW: ~5-10 trades per day expected, +8% per trade — not lottery but reliable
- BOT_LATE_LOTTERY: ~1-2 trades per day, +186% per trade in small sample — true asymmetric payoff
