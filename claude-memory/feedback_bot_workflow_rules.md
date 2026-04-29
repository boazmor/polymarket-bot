---
name: Strict working rules for the Polymarket bot project
description: Hard rules the user has set for how to develop, deploy, and structure the bot — non-negotiable conventions
type: feedback
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user has built up a strict set of working rules over months of iteration on this bot. These are non-negotiable conventions — violating them has caused real problems in the past.

**Code delivery rules:**
1. **Never deliver partial bots.** Always ship a complete, runnable single file. No "here's a snippet, paste it in" — that has caused breakage.
2. **Never silently change tactics or strategy.** If a parameter, threshold, or logic branch is being changed, explain it explicitly and get approval before applying.
3. **Don't use `nano`.** When editing files on the server, use a different approach (likely `vim` or sending complete files via scp/upload). The user dislikes nano specifically.

**Deployment / instruction format:**
4. **Always structure instructions in two sections labeled "מחשב" (computer) and "שרת" (server).** That's how the user mentally separates local vs remote actions. Even short instructions should respect this layout.
5. **Always upload to the server first, then run there.** Never run locally as a substitute for testing on the server. Local runs miss latency-dependent behavior.

**Bot architecture rules:**
6. **Every bot must include a research unit** that produces CSV reports of what happened. No "trading-only" bots — research is part of the deliverable.
7. **Every run must clean old report files first** so reports from different runs don't get mixed and skew analysis. Failure to do this has corrupted past analyses.

**Data the bot must always log per trade:**
- שעה (timestamp)
- מחיר קניה (buy price)
- דיסטנס פולימרקט (Polymarket distance)
- דיסטנס ביננס (Binance distance)
- צד קניה (buy side — YES/NO)
- נזילות (liquidity)
- WIN/LOSS
- PnL בדולרים (dollar P&L)

**Optimization metric (very important):**
8. **The goal is NOT win rate. The goal is which rule earns the most dollars total.** A rule with 30% win rate that wins 3x can beat a 70% win rate rule. Always evaluate by dollar PnL across the sweep, never by hit rate.

**Why these rules exist:** Each one came from a real incident — partial bots that broke, silent tactic changes that lost money, mixed-report runs that produced wrong conclusions, win-rate-chasing that lost dollars. Respect them all.
