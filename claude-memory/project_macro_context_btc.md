---
name: BTC macro context and its implications for the bot
description: User's view on Bitcoin macro momentum and why it matters for UP/DOWN strategy bias and execution speed
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User explained on 2026-04-30 evening: UP/DOWN markets reflect BTC momentum. The current macro context shapes which side has the edge.

## Current macro context (as of late April 2026)
- BTC hit ATH around **$125,000** at peak.
- Crashed to roughly **$60,000-something**.
- Now in a slow recovery — climbing back gradually.
- Late-April BTC range observed in our data: ~$76,200-78,900 (still well below ATH).

**Implication for the coming month (per user's view):** UP-side bets are likely to win more often than DOWN-side, because the underlying trend is up. BOT120's direction-only logic should naturally lean UP more often during this period (since BTC > target -> UP signal more frequent).

But the 30-hour dataset analyzed earlier showed the OPPOSITE — DOWN won 68.5% in that window, suggesting that period was downtrending. So this changes by week, not constant.

## Why the bot's speed matters
In a 5-minute window, anything can happen — news, whales, micro-moves. The macro trend is a probabilistic edge across many markets, not a guarantee per market. To capture the edge:
- The bot needs to see the move and act fast (within seconds).
- This is why we want:
  - Real-time Binance feed (sub-second updates)
  - Maker orders sitting in the book (fill instantly when seller appears)
  - Low-latency server (Hetzner Germany, near Polymarket infra)
  - No browser dependency for target capture (HTTP `__NEXT_DATA__` is faster than headless render)

## How to use this when designing the bot
- Don't second-guess BOT120's direction logic — it correctly follows the immediate momentum signal in each 5-minute window.
- BUT also be aware: in a strong macro uptrend, UP-side fills are still preferable in the cheap range. If we ever bias the strategy by side, lean UP during net-up months.
- In mixed/sideways periods, BOT40's price-cap logic (cheap on either side) is the right approach.

## Research-question connection
This connects to `project_research_questions.md` -> question #3 "Direction asymmetry". When we run that analysis, segment by BTC's macro trend during the period (UP-trending vs DOWN-trending segments) — the bot may behave differently in each.
