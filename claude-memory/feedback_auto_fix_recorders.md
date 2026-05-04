---
name: Auto-fix recorders without asking
description: When the twice-daily recorder health check finds a broken recorder, restart it immediately — don't ask permission.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
The recorder health check at /root/check_recorders.sh runs twice daily on both servers (8am + 8pm Israel). When it detects a stale or no-data recorder, it must AUTO-RESTART it without asking.

**Why:** The user will continuously refine BRM parameters throughout the bot's lifetime. Quality recordings must be available on demand at any moment. They don't want to discover broken recorders only when going to pull data — by then the gap exists and can't be recovered. This is a permanent ongoing rule, not a one-time setup.

**How to apply:**
- The check_recorders.sh script already has restart_helsinki / restart_germany / restart_kalshi functions and calls them automatically when a problem is detected.
- If the user asks me to verify recorder health, I should also fix anything broken on the spot, not just report.
- This applies to recorder restarts only — NOT to live trading bot restarts (those still need user confirmation per the standing rule on real-money decisions).
