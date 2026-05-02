---
name: Multi-coin bot architecture plan (approved 2026-05-02)
description: Component breakdown and rollout plan for the multi-coin bot — user-approved structure
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User proposed a modular architecture on 2026-05-02. He approved the broad structure plus my additions. This file captures the agreed plan.

## Component breakdown (8-11 modules)

| # | Component | Responsibility | User idea or mine? |
|---|---|---|---|
| 1 | Binance fetcher | One process reads ALL 7 coins' Binance prices via WebSocket | user |
| 2 | Reports (CSV writer) | All structured logs (signals, trades, settlements, research) | user |
| 3 | Screen display | TUI showing current state of all coins | user |
| 4 | Calculations | Distance, signals, decisions — pure functions, testable | user |
| 5 | Polymarket / Wallet | Order book reads + place/cancel orders via py-clob-client-v2 | user |
| 6 | Master controller | Coordinates timing, market rollovers, kill switches | user |
| 7 | **Params file** | Single JSON/Python file with per-coin tunable params — THE only file edited frequently | **user** ⭐ |
| 8 | Cross-Coin Signal Engine | "Macro signal" when N coins agree; lead-follower detection | mine |
| 9 | Capital Allocator | When multiple coins signal at once: rank by confidence, pick top N | mine |
| 10 | State Persistence | Save daily PnL + open positions + kill state to disk → survive crashes | mine |
| 11 | Alerts | Telegram/email when: kill triggered, connection lost, balance drops fast | mine |

## 3-phase strategy split (user's correction to current 2-phase)

Currently the bot has BOT40 (mashes maker+taker phases) and BOT120 (overlaps with BOT40). User clarified:
- **BOT_30**: 0-30s, MAKER pattern — limit orders at 0.28/0.29/0.30
- **BOT_40**: 30-40s, TAKER fallback — grab any side at ≤ 0.35
- **BOT_120**: 0-120s (PARALLEL to BOT_30/40, not sequential), distance-based — distance ≥ 68, limit at 0.50

The three are independent triggers running concurrently in their own time windows.

## Per-coin params (the params file design)

```python
COIN_PARAMS = {
    "BTC": {
        "bot30_maker_levels": [0.28, 0.29, 0.30],
        "bot30_size_usd": 1.0,
        "bot40_fallback_price": 0.35,
        "bot120_min_distance": 68,
        "bot120_max_price": 0.80,
        "bot120_limit_price": 0.50,    # the maker variant
        "max_buy_usd": 5.0,
        "blocked_nyc_hours": [5, 6, 7],
    },
    "ETH": { ... different params },
    ...
}
```

Each coin gets its own thresholds based on its volatility profile and historical bias. The params file is the ONLY one we'd edit during normal tuning.

## "1-bot vs 7-bots" comparative report

A new report we'll generate daily to answer: did the unified bot's cross-coin features add real value, or would 7 isolated bots have done as well?

For every trade taken: log actual PnL. Also simulate what 7 isolated bots would have decided (same params, no cross-coin signaling). Compare totals.

If unified > isolated → cross-coin engine justifies its complexity.
If unified ≤ isolated → simplify to 7 separate bots.

## Rollout plan (estimate 15-20 hours)

| Stage | Work | Hours |
|---|---|---|
| 1 | Externalize all hardcoded constants → `bot_config.py` (params file). Bot keeps working as-is, just reads from external file. | 1-2h |
| 2 | Split BOT40 into BOT_30 + BOT_40 (pure refactor, no logic change) | 2-3h |
| 3 | Extract each component into its own file under `bot_engine/` (binance.py, wallet.py, strategy.py, screen.py, reports.py, master.py) | 5-7h |
| 4 | Multi-coin: master controller orchestrates 7 strategy instances, shared wallet, shared capital allocator | 3-4h |
| 5 | (Optional, gated on report) Cross-Coin Signal Engine added between strategy and decision | 4-5h |

**User's preferred path**: stages 1-4 first to get a clean multi-coin baseline. Stage 5 (cross-coin engine) added LATER if the comparative report shows it's worth the complexity.

## Critical context

- User explicitly said: "the principle is — start with 7 isolated bots sharing one wallet + one params file, then add cross-coin only if it earns its keep"
- This is incremental — each stage is shippable on its own
- The current TEST5 bot stays running in parallel during the refactor (don't break working code while building new)
