---
name: רקורדס חייבים להיות מלאים, שליפים בשנייה, אמינים
description: User mandate — recorders must capture complete per-second snapshots with all relevant fields. Past analyses were misleading because data was incomplete; that caused BOT 40-120 to fail.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User's exact words 08/05: "תרשום בזכרון שהרקורדס צריכים להיות מלאים. שליפים בשניה, מלאים אינורמציה כדי שנוכל לנתח נכון. עד כעכשיו הניתוחים שלך הטעו אותנו ולכן גם בוט 40-120 נכשל. תרשום דוחות מלאים, אמינם ושליפים."

**Why:** Past recorders missed fields, causing analyses based on them to be wrong. The user's BOT 40-120 strategy failed in part because the recorder data we used to validate it didn't include critical fields. He's holding me accountable for incomplete recorders leading to bad analysis recommendations.

**How to apply:**

For every recorder (Polymarket, Predict, Kalshi, Gemini, Pyth, Binance, future ones):
1. **Per-second snapshot is the minimum** — every second a row, with the full state of the market at that moment. No skipping.
2. **All relevant fields** — bid, ask, sizes (in shares AND in USD), depth at multiple price levels, last trade price, volumes, age of feed, oracle price (Binance + Chainlink + Pyth + Kaiko per relevance), strike, sec_from_open, market epoch, market id, status flag, settlement flag.
3. **Outcome capture mandatory** — when a market ends, write a row to `market_outcomes.csv` with: ticker, strike, settlement price, winner side, source. This was the gap that broke the bot settlement loop on 08/05.
4. **Identify oracle source explicitly** — for each strike captured, record which oracle/source provided it (Chainlink RTDS, Binance, Kaiko, Pyth).
5. **Reliability** — recorder must NOT silently drop seconds. If WebSocket disconnects, log it and reconnect. Each row written should be timestamped and complete.
6. **Don't trust last_price snap as a winner signal** — many platforms have low liquidity and last_price doesn't snap. Use strike-comparison method (this market's strike vs next market's strike) which works for all platforms.
7. **Before writing analysis** based on a recorder's data, verify the recorder captured the fields the analysis depends on. If not, fix the recorder FIRST then run the analysis.

**Audit each recorder periodically** for: missing fields, gaps in time, fields filled with zeros instead of real data, market rollovers not captured, outcomes not written.
