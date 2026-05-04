---
name: Add seller identity tracking to recorder + analysis tools
description: User wants to identify counterparty wallet addresses for every trade — detect market makers, smart money, bots that consistently sell early.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User requested 04/05/2026: enhance the recording infrastructure to capture identity of every counterparty wallet that fills our orders.

**Goals:**
- Detect if same wallet keeps selling to us at start (likely market maker)
- Identify smart-money wallets that consistently win on the side they take
- Distinguish bot-driven flow from retail noise

**To implement:**

1. **Recorder enhancement (MULTI_COIN_RECORDER.py):**
   - When `last_trade_price` event arrives, also capture the buyer/seller wallet from event payload (Polymarket WS includes this).
   - Add columns to `poly_book_ticks.csv`: `maker_address`, `taker_address`, `trade_size_usd`.

2. **Bot enhancement (LIVE_BTC_5M_V1_TEST5.py and bot_engine):**
   - When our buy order fills, query Polymarket trades API for the fill details and log counterparty.
   - Store per-trade: order_id, fill_price, fill_size, counterparty_address.

3. **Analysis tool (separate script):**
   - For our wallet's trade history, group by counterparty and compute: # of times sold to us, avg price they sold at, market timing (early/mid/late), eventual market outcome (did we win or lose?).
   - Flag wallets with > N transactions to us (likely a market maker or bot).

4. **Polymarket API for historical fills:**
   - Endpoint: `https://data-api.polymarket.com/trades?user=<our_safe_address>&limit=...`
   - Returns: maker, taker, price, size, timestamp, market.
   - Can backfill all our past trades to find counterparty patterns.

**How to apply:** When user asks about counterparties, sellers, bots, market makers, or "who sold to us", consult this memory and either run the analysis tool or build it if not yet exists.

## Follow-up project (04/05/2026): identify and reverse-engineer profitable bots on Polymarket

User wants to find bots that consistently profit on Polymarket BTC 5m markets and learn their strategy.

**Approach:**
1. For each market our bot traded in (we have ~150 trades), query Polymarket for ALL trades on that market — gets all wallets that participated.
2. Aggregate: which wallets traded most often across our markets? (Likely market makers / bots)
3. For each suspected bot wallet, query its trade history (`/trades?user=<addr>`) and compute:
   - Total volume
   - Win/loss ratio
   - Avg buy price
   - Time-of-trade distribution (early/mid/late in market)
   - Net P&L
4. Top 5-10 most profitable wallets → their trade patterns reveal strategy.

**Tooling needed:** Python script that reads our trade slugs, fetches per-market trades, builds wallet stats. ~2-3 hours of work to build well.

**Note:** The /trades?user=<addr> endpoint returns only THAT wallet's perspective (proxyWallet field shows the queried wallet). To find counterparties, use per-market trade query OR transactionHash → Polygon blockchain decode.
