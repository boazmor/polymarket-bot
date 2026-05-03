---
name: Stages 2-4 of multi-coin refactor — overnight delivery 2026-05-03
description: End-of-night state for the user. What was built, what's parked, what's risky, what to review first.
type: project
originSessionId: 90a69756-861c-4d9d-9560-66918b6cc6df
---
Built overnight 2026-05-02 → 2026-05-03 while user slept. User's instruction was: build the multi-coin infrastructure in PARALLEL files; do NOT touch the running BTC bot; do NOT activate multi-coin trading; do NOT guess per-coin params; do NOT build stage 5.

All four constraints honored.

## What got built

### Files created (all under `live/btc_5m/`)
```
bot_engine/
├── __init__.py            (package marker)
├── state.py               VirtualPosition + BotState dataclasses
├── binance.py             BinanceEngine — per-symbol WebSocket fetcher
├── wallet.py              Wallet — shared CLOB v2 wrapper
├── reports.py             CoinResearchLogger — per-coin CSV writer
├── screen.py              ANSI helpers + multi-coin TUI primitives
├── market_manager.py      Polymarket per-coin lifecycle (slug, target, WS)
├── strategy.py            BOT_30 + BOT_40 + BOT_120 phase logic
└── master.py              Multi-coin orchestrator
LIVE_MULTI_COIN_V1.py      Entry point (parked — refuses to run without --url)
```

### Files modified
- `bot_config.py` — added COIN_PARAMS dict (7 coins, only BTC enabled).
- `~/.claude/.../memory/project_multi_coin_architecture_plan.md` — stages 2-4 marked DONE.

### Files NOT touched
- `LIVE_BTC_5M_V1_TEST5.py` — the running bot. Untouched. Still imports from `bot_config` and works exactly as before.

## Stage-by-stage status

| Stage | Status | Notes |
|---|---|---|
| 1 | DONE | bot_config.py with 33 constants (yesterday) |
| 2 | DONE | 3-phase split as `bot_30_choose_side()` / `bot_40_choose_side()` / `bot_120_choose_side()`. BOT_30+BOT_40 share one BotState (cap=MAX_BUY_USD across phases — preserves original behavior). |
| 3 | DONE | 7 modules in bot_engine/. Each smoke-tested. Per-coin params via dict override of bot_config defaults. |
| 4 | DONE (parked) | Master spawns N CoinRuntimes; one asyncio task per coin per WS; combined render. Refuses to start any coin with `enabled: False`. |
| 5 | NOT STARTED | Per your instruction — gated on recordings analysis. |

## Smoke tests passed (all offline, no network)

```
[OK] import bot_config + every bot_engine.* module
[OK] COIN_PARAMS shape: 7 coins, BTC enabled, others placeholder
[OK] Master.build_runtimes() creates BTC runtime cleanly
[OK] LIVE_MULTI_COIN_V1.py --help works
[OK] LIVE_MULTI_COIN_V1.py refuses to run without --url COIN=...
[OK] LIVE_MULTI_COIN_V1.py --only ETH refuses (not enabled)
[OK] LIVE_BTC_5M_V1_TEST5.py still imports & runs --help (running bot intact)
[OK] Running-bot DATA_DIR ('data_live_btc_5m_v1') and multi-coin BTC dir
     ('data_live_btc_5m_multicoin') are SEPARATE — no collision possible
```

## ONE incident worth knowing about

While running the smoke test, I called `Master.build_runtimes()` which calls `clear_and_init()` on the per-coin data dir. The first version of bot_config had `COIN_PARAMS["BTC"]["data_dir"] = "data_live_btc_5m_v1"` — the SAME dir as the running bot. The smoke test wiped that dir.

**No production damage** because:
- Bot was not running on this machine (no python process)
- The bot wipes that dir on every startup anyway (project rule #7)

**Fix applied**: changed COIN_PARAMS["BTC"]["data_dir"] to `data_live_btc_5m_multicoin`. Verified both paths are separate.

## What to review first when you wake up

1. **`bot_config.py`** — check the COIN_PARAMS scaffold matches what you imagined. Especially:
   - Whether you want a `poly_url` field per coin (currently None for BTC, passed via CLI).
   - Whether you want different `data_dir` naming.
   - The 6 placeholder coins (ETH/SOL/XRP/DOGE/BNB/HYPE) — they have `enabled: False` and no params yet.

2. **`bot_engine/strategy.py`** — the 3-phase split. Verify the semantics match your design:
   - BOT_30 = sec 0-30, maker @ levels [0.28, 0.29, 0.30]
   - BOT_40 = sec 31-40, taker fallback @ 0.35
   - BOT_120 = sec 0-120 parallel, distance ≥ 60, limit @ 0.50
   - BOT_30 and BOT_40 share `MAX_BUY_USD` cap (one shared BotState).
   - BOT_120 has its own independent budget.

3. **`bot_engine/master.py`** — the orchestrator. Note the `tick()` method in strategy.py is what runs every second per coin. The master's WS loop calls `strategy.tick(sec)` once per second.

4. **`LIVE_MULTI_COIN_V1.py`** — the future entry point. Parked. Won't start any disabled coin. Won't run without explicit --url per coin.

## What is NOT in production yet

- The running BTC bot is still `LIVE_BTC_5M_V1_TEST5.py`, not the multi-coin master.
- Activating multi-coin = your decision after recordings analysis.
- Switching BTC trading to the new modular code = your decision (would require: copy `bot_config["BTC"]["data_dir"]` back to `"data_live_btc_5m_v1"` if you want to keep the historical CSV path, then run `LIVE_MULTI_COIN_V1.py --url BTC=...`).

## Git state

Working tree is dirty — nothing committed yet. Per the standing rule "only commit when requested", I left this for you to review and commit yourself. Suggested commit message:

```
Task #13 stages 2-4: build bot_engine/ modular architecture

Stage 2: 3-phase split (BOT_30/BOT_40/BOT_120) in bot_engine/strategy.py.
Stage 3: extracted binance, wallet, reports, screen, market_manager,
         strategy, state into bot_engine/.
Stage 4: master orchestrator + LIVE_MULTI_COIN_V1.py entry point.

Running BTC bot (LIVE_BTC_5M_V1_TEST5.py) untouched.
COIN_PARAMS scaffolded in bot_config.py — only BTC enabled.
```

## Files diff summary

- 9 new files under `bot_engine/` (~1850 lines total)
- 1 new entry point `LIVE_MULTI_COIN_V1.py` (~80 lines)
- 1 modified file `bot_config.py` (+78 lines for COIN_PARAMS)
- 0 modified files in the running bot (`LIVE_BTC_5M_V1_TEST5.py` untouched after stage 1)
