---
name: Gemini Predictions — major arb find 05/05/2026
description: Gemini Predictions has BTC 5min + 15min binary markets with public no-auth API. Verified working from Hetzner Germany. Adds 3rd reference price (Kaiko) to enable triangular arb with Polymarket (Chainlink) + Kalshi.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Searched for additional binary BTC platforms 05/05/2026 evening per user request. The big find:

## Gemini Predictions

**Why it matters:**
- Has both **5-minute (BTC05M)** and **15-minute (BTC15M)** BTC up/down binaries — matches our existing timeframes exactly.
- Reference price = **Kaiko index (GRR-KAIKO_BTCUSD_60S)** — different from Polymarket (Chainlink) and Kalshi (their oracle). 3 different references = more mispricing windows = more arb opportunities.
- Public REST API, **no auth needed for read-only**. Authenticated trading available too.
- Verified working from Hetzner Germany (server `hetzner` already used for Polymarket/Kalshi). Helsinki should also work.
- US main exchange supports Israel for crypto, but predictions product geo-restriction unclear — moot, we'll use Germany/Helsinki anyway.

## Active markets right now (verified live)

| Series | Count | Notes |
|---|---|---|
| BTC05M | 14 | 5-min BTC binary (one every 5 min) |
| BTC15M | 1 | 15-min BTC binary |
| BTC1H, ETH1H, SOL1H, XRP1H, ZEC1H | 3 each | hourly per coin |
| ETH15M | 1 | 15-min ETH binary |
| BTC, ETH, SOL, XRP, ZEC daily | 1 each | end of month |

Coins covered: **BTC, ETH, SOL, XRP, ZEC** (no DOGE/BNB/HYPE).

## API endpoints (public, no key)

```
# List active crypto events
GET https://api.gemini.com/v1/prediction-markets/events?status=active&category=crypto&limit=200

# List categories
GET https://api.gemini.com/v1/prediction-markets/categories

# Order book with depth (uses regular Gemini /v1/book endpoint)
GET https://api.gemini.com/v1/book/{instrumentSymbol}
# e.g. /v1/book/GEMI-BTC05M2605051555-UP
# returns {"bids":[...], "asks":[{"price","amount","timestamp"},...]}
```

The `instrumentSymbol` format: `GEMI-{TICKER}-{UP|DOWN}` (e.g. `GEMI-BTC05M2605051555-UP`).
The `ticker` format embeds expiry: `BTC05M2605051555` = BTC, 5-min, 2026-05-05, 15:55 UTC expiry.

## Each contract returns

```
prices.bestBid / bestAsk / lastTradePrice
strike.value (the reference price for resolution)
expiryDate / effectiveDate
status / marketState
```

## Strategic implications

**New arbitrage pairs unlocked:**
1. **Polymarket 5min ↔ Gemini 5min** — first 5-min cross-platform arb available (Kalshi has no 5min)
2. **Polymarket 15min ↔ Gemini 15min** — second 15min pair (we have only Poly↔Kalshi today)
3. **Triangular: Polymarket ↔ Kalshi ↔ Gemini (15min)** — three-way arb is much more frequent than two-way

**Reference price diversity:**
- Polymarket: Chainlink RTDS (lags ~1.2s per our prior measurement)
- Kalshi: their internal oracle
- Gemini: Kaiko index (60s aggregate)

Three different latencies + three different aggregation methods = structural mispricings that can be captured.

## Action items (after V2 baseline confirms)

1. Build Gemini recorder mirror to existing Kalshi recorder (same per-second CSV format)
2. Deploy to Germany (verified) and Helsinki
3. Once data accumulates, re-run arb analysis: Poly↔Gemini (5m + 15m) and triangular Poly↔Kalshi↔Gemini (15m)
4. Eventually add Gemini as 3rd leg in arb_virtual_bot

## Other platforms surveyed today (lower priority)

- **DraftKings Predictions** — only daily BTC/ETH, US-only
- **Robinhood Event Contracts** — US-only, growing fast (320% revenue surge Q1 26)
- **OG.com** — sports-focused, minimal crypto
- **XO Market** — user-generated markets, $150M vol but quality varies
- **Insight Prediction** — exists but no 15min crypto BTC
- **SX Bet** — sports primarily
- **Thales/Overtime** — crypto BTC up/down via Chainlink oracle, but smaller volume than top tier
- **Tytanid** — DeFi binary options DEX, low liquidity
- **Bybit/Bitget/HTX/KuCoin** — perp futures only, no event contracts found

The list to keep watching: Hyperliquid HIP-4 (when 15min launches), OKX Event Contracts (when volume scales — scheduled checks 5/6 and 5/7).
