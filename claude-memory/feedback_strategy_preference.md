---
name: Strategy preference — few big wins over many small wins
description: User prefers low-volume high-payout strategies. Avoid scalping. Lottery-ticket and high-payout-distance strategies preferred.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User explicitly stated 04/05/2026: "אני נגד הרבה עסקאות רווח קטן. עדיף הפוך"
(against many small-profit trades, prefers the opposite)

**Why:** Aligns with the existing strategy philosophy: low win rate but high payout per win = profitable in dollars. The bot's BOT40 already does this (buy at 0.30, get 3.3x if wins). Scalping at 0.90+ for 4-10% profit per trade conflicts with this philosophy.

**How to apply:**
- When proposing strategies, prefer: fewer trades, bigger payout per win.
- Example preferences:
  - Buy at 0.01-0.05 in last 30 sec → 20-100x per win, even if win rate only 30% (lottery ticket)
  - Buy at 0.30-0.50 with distance edge → 2-3x per win, 50%+ win rate
- Avoid suggesting:
  - Scalping at 0.90+ for tiny profits
  - High-frequency strategies that require many trades to be profitable
- The user wants the bot to feel "asymmetric" — most trades lose, but the wins are dramatic.
