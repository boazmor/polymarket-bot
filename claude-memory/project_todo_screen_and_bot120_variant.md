---
name: TODOs from 2026-05-01 — screen flicker fix + BOT120 variant
description: Two improvements requested by user during the first live test session
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User asked these two during the first live trading session on 2026-05-01.

## TODO 1 — Fix screen flicker

**Current behavior:** the bot calls `clear_screen()` (ANSI `\033[2J\033[H`) every second and reprints the entire status block. This causes visible flicker on Windows PowerShell, especially when running over SSH or with slow rendering.

**Goal:** update only the values that change, not the entire screen. Keep static elements (URL, slug, headers, decoration lines) drawn once; only refresh the dynamic numbers (BTC price, target, distance, order book bids/asks, bot decisions, P&L).

**Implementation approach:** use ANSI cursor-positioning escape codes:
- `\033[<row>;<col>H` to move cursor to specific position
- `\033[K` to clear from cursor to end of line
- Track which row each dynamic value lives on
- On each refresh, just overwrite those rows in place

**Risk:** more complex code than V3's clear-and-reprint. If we get cursor positions wrong, display gets garbled. Plan: add a small flag like `USE_INPLACE_UPDATE = True` and let user toggle.

**Lower-risk alternative:** reduce `SCREEN_REFRESH_EVERY_SEC` from 1 to 2 or 3 — less flicker, slightly older info on screen. Quick win.

## TODO 2 — BOT120 variant: dist 60 + limit 0.50

User suggested: in addition to the current BOT120 logic (dist≥68, cap 0.80), test a more aggressive variant:

- **Trigger at distance 60** (lower than current 68)
- **Place a limit order at 0.50** (much lower than the current cap of 0.80)
- "See if there are opportunities" — i.e., does this find profitable cheap fills?

**Interpretation:** this is a MAKER-style entry — instead of taking the best ask, sit at 0.50 and wait. Like the BOT40 maker idea but for BOT120.

**Why interesting:**
- Lower distance (60 vs 68) = more frequent triggers
- Lower limit (0.50 vs 0.80) = much cheaper entries when filled
- Trade-off: at distance 60-68, often the direction-side ask is well above 0.50, so limit at 0.50 won't fill immediately. Have to wait for the market to come to us.

**Implementation:** add as a SECOND BOT120 instance (e.g. `BOT120B`) running alongside the current one, with its own state and logging. Don't replace the existing BOT120 — track both, compare which earns more in the same hours.

**Constants to add:**
- `MIN_DIST_BOT120B = 60.0`
- `BOT120B_MAKER_PRICE = 0.50`

**Pending:** waiting for the live test to first confirm the basic infrastructure works. After confirmation, this becomes V2 priority alongside the time filter (NYC 05-06).

## Priority order for next coding session

1. **Verify first live trade succeeds** (current task — user is watching)
2. **Time filter for NYC 05-06** (user already approved this, supports +24% boost in sim)
3. **Screen flicker fix** (lower priority — annoyance not blocker)
4. **BOT120 dist-60 / limit-0.50 variant** (research idea — run side-by-side with existing)
