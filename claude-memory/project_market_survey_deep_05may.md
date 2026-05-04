---
name: Deep market survey 05/05/2026 — comprehensive prediction market platform analysis
description: Overnight market research. 13 platforms compared, existing arb bots identified, key insights for our strategy. Top finding: 0x8dxd wallet made $550K via Polymarket+Binance latency arb.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
Comprehensive overnight market research for the user. Focus: prediction-market platforms with crypto BTC binary markets, existing arbitrage tooling, and Israel access.

## TIER 1 — Top tradeable platforms

### 1. **Polymarket** (already trading)
- **Volume:** $9.7B in 30-day, $385M/24hr (March 2026)
- **Markets:** 5min, 15min, 1h, 4h, 1d for BTC/ETH/SOL/XRP/DOGE/BNB/HYPE
- **API:** Full WebSocket + REST (we use it daily)
- **Min:** $1 nominal value (after commission, effectively $1.05+)
- **Israel:** Works
- **Verdict:** Our primary platform, best liquidity for short timeframes.

### 2. **Kalshi** (account in progress)
- **Volume:** $6B in 30-day, 52.6% market share (Jan 2026)
- **Markets:** 15min binaries (BTC/ETH/SOL/XRP/DOGE/BNB/HYPE), 1h directional, plus daily/weekly/monthly
- **API:** REST + WebSocket, RSA-key auth
- **Min:** Standard $1 cents
- **Israel:** Yes (you registered)
- **Tools:** `pykalshi` Python SDK, free, open-source
- **Verdict:** Critical for arbitrage with Polymarket. Tighter spreads than Polymarket (~0.85¢ vs 0.95¢).

### 3. ⭐ **OKX Event Contracts** (BIG NEW FIND)
- **Launched:** April 20, 2026 (very fresh)
- **Markets:** 15min, hourly, daily for BTC/ETH
- **API:** Full WebSocket + REST integrated with main OKX API
- **Min:** **$0.01** — game-changer (no $1 trap!)
- **Israel:** Not in restricted list. OKX serves 100+ countries; Israel almost certainly fine.
- **Volume:** Unknown (too new)
- **Verdict:** HUGE opportunity. Major exchange = deep liquidity expected. 1¢ minimum opens lottery strategies that Polymarket blocks.

### 4. **Hyperliquid HIP-4**
- **Volume:** $6.05M day-1, growing fast. (Underlying perp DEX: $219B/mo total)
- **Markets:** Currently only daily BTC binary. Roadmap: more daily on BTC/ETH/HYPE, then permissionless 5min/15min
- **API:** REST + WebSocket public (api.hyperliquid.xyz)
- **Min:** Unknown specific
- **Fees:** **Zero** on entry (only on exit)
- **Israel:** Verified accessible
- **Verdict:** Watch for short-timeframe expansion (likely Q3 2026). Could become 4th leg.

### 5. **Limitless Exchange**
- **Volume:** $270M total, $200M Jan 2026 alone
- **Platform:** Base L2, CLOB
- **Markets:** Hourly + daily crypto, also stocks/sports/politics
- **API:** Production-ready API, Java/Python supported
- **Israel:** Likely (Base L2, no KYC for some flows)
- **Verdict:** Worth investigating as 3rd-4th platform but smaller than top 3.

## TIER 2 — Available but limited utility

### 6. **XO Market**
- $150M volume, user-generated markets, hard to predict liquidity per market
- Sovereign rollup on Celestia
- Not focused on time-windowed crypto bets

### 7. **Predict.fun (Binance Wallet)**
- $1.8B cumulative volume, 130K users
- BNB Chain, integrated into Binance Wallet
- BTC events but mostly long-term ("BTC hit $80k first?")
- Not short-timeframe focused

### 8. **Crypto.com OG**
- Launched Feb 2026, US-only, CFTC-licensed
- Mostly sports/politics/economics — minimal crypto BTC
- Not useful for our strategy

### 9. **Robinhood Prediction Markets**
- Has BTC Up/Down 15-min markets
- US-only via Robinhood Derivatives
- Not accessible from Israel directly

### 10. **Interactive Brokers ForecastEx**
- "Best broker for prediction markets in Israel in 2026" per BrokerChooser
- BTC event contracts via CME Group + ForecastEx
- $0.01 increments, $1 payouts
- IBKR Israel account works
- **Worth investigating for Israel-friendly access**

## TIER 3 — Skip

### Manifold Markets
- PLAY MONEY only. Cannot withdraw. Useful only for research/probability discovery.

### Drift BET (Solana)
- Real-world events focus, not crypto BTC short-timeframe.

### Hyperliquid HYPE token markets
- Too speculative, not proper binary BTC.

## ⭐ THE BIG STORY: Wallet 0x8dxd

Single bot wallet on Polymarket.
- **$300 → $550,000+ all-time** (verified)
- **Strategy:** Latency arbitrage between Polymarket and Binance/Coinbase
- **Markets:** 15-min BTC/ETH/SOL/XRP binaries
- **Edge:** Speed of execution, NOT prediction quality
- **Profile:** polymarket.com/@0x8dxd

**Industry-wide:**
- 14 of top 20 Polymarket profitable wallets are bots
- 30% of wallet activity = AI agents
- $40M extracted from Polymarket via arb in 12 months (April 2024 - April 2025)
- 73% of arb profits go to sub-100ms execution bots

## ⭐ EXISTING OPEN-SOURCE BOTS WE CAN STUDY

### CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot (MIT)
- Python (FastAPI) + TypeScript (Next.js dashboard)
- Monitors Bitcoin 1-Hour Price markets between Polymarket and Kalshi
- Detects when (cost of Poly side + cost of Kalshi side) < $1
- Has visual dashboard
- **Direct precedent for our planned bot** — start here

### ImMike/polymarket-arbitrage (Python)
- Watches 10,000+ markets
- Both intra- and cross-platform arb

### akhan280/event-contract-arbitrage
- Polymarket + Kalshi + Robinhood

### OctoBot-Prediction-Market (GPL-3.0)
- Open-source Python framework
- Currently Polymarket only, Kalshi planned

## ⭐ DATA INFRASTRUCTURE WE CAN BUY (vs build)

### Oddpool ($30-100/mo)
- Unified data Kalshi + Polymarket
- WebSocket feeds, orderbooks, arbitrage API
- Historical orderbooks back to March 2026
- $100/mo Premium = unlimited WebSocket + arb API
- **Could replace ALL our recorder infrastructure for $100/mo**

### Dome
- Unified API for Polymarket, Kalshi, Limitless
- Free tier available

### PolyRouter
- Normalized data from multiple platforms via single API key

## ⭐ WALLET-TRACKING TOOLS

To identify and copy successful bots:
- **Polyguana** — free leaderboards, market stats
- **FirePolymarket** — free smart-money classification
- **Polysights** — paid, 30+ metrics, AI insights
- **Stand** — copy trading platform
- **Polymarket Bros** — free, monitors trades >$4K

## Volume + Time-Window Coverage Matrix (BTC focus)

```
Platform        5min   15min   1h     4h     1d     Notes
Polymarket      ✓      ✓       ✓      ✓      ✓     primary
Kalshi          —      ✓       ✓      —      ✓     no 5min
OKX             —      ✓       ✓      —      ✓     1¢ min!
Hyperliquid     —      —       —      —      ✓     daily only currently
Limitless       —      —       ✓      —      ✓     Base L2
Robinhood       —      ✓       —      —      ✓     US only
IBKR            —      —       ✓      —      ✓     IB account
Crypto.com OG   —      —       —      —      —     no crypto windows
```

## Strategic Recommendations for OUR Bot Roadmap

### Immediate (after Poly bot stable, ~1 week)
1. **Use Eventarb (free)** to monitor opportunities while we build
2. **Open Kalshi account fully** + get API keys (in progress)
3. **Open OKX account** for the 1¢ trades

### Short-term (2-4 weeks)
4. **Start with CarlosIbCu's bot as base** — fork the MIT-licensed code, adapt to our infrastructure
5. **2-leg arbitrage**: Polymarket DOWN + Kalshi YES (validated 45% profitable, 10% avg)
6. **Test with $50/leg** initially

### Medium-term (1-3 months)
7. **3-leg arbitrage**: add OKX once API explored. Triangular arb opportunities much more frequent.
8. **Latency optimization**: co-locate bot near each platform's servers (Polymarket on Polygon, Kalshi/OKX exchange-specific). Sub-100ms execution = catch the 73% of arb profit.
9. **Add Limitless** as 4th leg if liquidity is sufficient

### Long-term (3-6 months)
10. **Add Hyperliquid HIP-4** when 15min markets launch
11. **Wallet-mirror strategy**: identify 0x8dxd-style bots, monitor their trades, copy or fade

## Key Risks to Consider

1. **Latency competition**: 73% of arb profits captured by sub-100ms bots. Our home bot can't compete. Need server co-located near each platform.
2. **Capital fragmentation**: trading $X on Polymarket + Kalshi + OKX means dividing capital 3 ways. Each leg ties up funds while waiting for resolution.
3. **Regulatory shifts**: any of these platforms could restrict accounts. Diversification mitigates.
4. **Smart contract risk** (DEX platforms): Polymarket, Hyperliquid, Limitless, XO Market all on-chain — exploit/bug risk.
5. **Withdrawal friction**: Polymarket = USDC on Polygon, Kalshi = USD bank, OKX = crypto exchange. Moving money between platforms takes hours-days.

## Bottom line

The space is **mature, profitable, and bot-dominated**. We are not entering an empty market — we are entering one where 30% of activity is bots and arb extracted $40M in 12 months. The opportunity is real but the competition is real too.

For us to win:
- Be **faster** than existing arb bots (sub-100ms execution)
- Or trade **markets they ignore** (smaller coins, niche timeframes)
- Or **multi-platform** with new entrants (OKX, Hyperliquid 5min when available)

**Highest-EV path forward:** Build the Polymarket+Kalshi 2-leg arb first using CarlosIbCu's MIT code as base. Add OKX as 3rd leg ASAP. Don't try to compete with 0x8dxd on pure Poly latency — they're already there.
