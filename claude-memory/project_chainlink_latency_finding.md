---
name: Chainlink BTC feed latency — likely root cause of "Polymarket distance loses" mystery
description: Empirical finding from 2026-04-29 analysis showing Chainlink BTC price feed lags by ~1.2s, explaining why Polymarket-based distance underperforms Binance-based distance
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
On 2026-04-29 we downloaded the Chainlink RTDS tick recording (`/root/data_ws_chainlink_research/rtds_ticks.csv`, 37,663 ticks from 24/04) and measured the `latency_ms` field — the gap between when each price tick was created and when it arrived at the Hetzner server.

**Latency distribution:**
- p50 (median): **1,200 ms (1.2 seconds)**
- p90: 1,565 ms
- p99: 1,930 ms
- max: 3,401 ms

**For comparison, Binance's public WS feed delivers ticks in single-digit milliseconds — effectively real-time.**

**Hypothesis (strong, awaiting cross-source confirmation):**
This explains the mystery of "Polymarket-based distance loses, Binance-based distance profits." When the bot uses Polymarket's Chainlink BTC price for distance:
- Every decision is based on a BTC price that is already 1.2+ seconds stale.
- In a 5-minute trading window where the strategically important action happens in the first 0–30 seconds, a 1.2s lag means the bot reacts AFTER the market has already moved.
- The bot consequently buys at bad fills, often on the wrong side, and bleeds money.

**To validate this empirically:** run both recorders simultaneously (`WS_BINANCE_POLY_RESEARCH_RECORDER` + `WS_CHAINLINK_RESEARCH_RECORDER`) and compare Binance BTC vs Chainlink BTC at the same wall-clock seconds. Expected: Chainlink should consistently report a price that Binance had ~1.2s earlier.

**Secondary finding from same analysis (38h of recording, 25-26/04):**
- 465 markets recorded, 100% target capture
- Of 464 resolved markets: DOWN won 318 (68.5%), UP won 146 (31.5%) — strongly down-trending period
- distance_signed (Binance BTC - target): mean -$15, stdev $39, range -$580 to +$556 — most targets fall very close to the moment-of-creation BTC price; large deviations (|distance|>$200) are rare. This suggests the previously-used distance threshold of 68 is restrictive in normal market conditions.
