---
name: Hyperliquid HIP-4 outcome markets
description: Verified candidate prediction-market platform. API endpoints, encoding, current limitations, and Israel access status.
type: reference
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
HIP-4 launched mainnet 2026-05-02. Decentralized binary outcome contracts on HyperCore CLOB. No KYC, zero entry fees.

## Access
- API base: `https://api.hyperliquid.xyz/info` (POST, JSON body)
- Verified working from Israel (home PC) AND Germany server (02:30 UTC May 3)
- Israel NOT in restricted jurisdictions (US, Canada-Ontario, Russia, Cuba, Iran, Syria, North Korea are)

## Endpoints

```bash
# Outcome meta (list of all binary markets)
curl -X POST https://api.hyperliquid.xyz/info \
  -H 'Content-Type: application/json' \
  -d '{"type":"outcomeMeta"}'

# All mid prices (includes outcome contracts as #N)
curl -X POST https://api.hyperliquid.xyz/info \
  -H 'Content-Type: application/json' \
  -d '{"type":"allMids"}'

# L2 orderbook for a specific outcome contract
curl -X POST https://api.hyperliquid.xyz/info \
  -H 'Content-Type: application/json' \
  -d '{"type":"l2Book","coin":"#0"}'
```

## Encoding
`asset_encoding = 10 * outcome_index + side` where side 0=Yes, 1=No.
- Coin string: `#<encoding>` (e.g., `#0` = Yes side of outcome 0)
- Token name: `+<encoding>`
- Asset ID: `100000000 + encoding`

## Current state (as of 2026-05-03)
Only ONE market live: BTC daily binary
- Description format: `class:priceBinary|underlying:BTC|expiry:20260503-0600|targetPrice:78213|period:1d`
- Sides: Yes (BTC > target at expiry), No
- Mid prices when checked: Yes 0.50105, No 0.49895

Only daily for now. Plan to add 5m/15m/1h in phased rollout (per launch announcement). Until then, NOT a direct alternative to the bot's 5m/15m Polymarket strategy.

## Why this matters
- Future arbitrage candidate vs Polymarket BTC daily
- Zero entry fees vs Polymarket 2% on winning side — significantly better economics
- CLOB matching same as perps (200k orders/sec)
- When 5m/15m comes online, drop-in replacement for the existing bot
