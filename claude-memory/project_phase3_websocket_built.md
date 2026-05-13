---
name: Phase 3 WebSocket migration BUILT (not yet deployed live) 13/05
description: v3 of the arb bot subscribes directly to WebSockets for all 3 platforms. ws_feeds/ package built and tested. Smoke test on Helsinki confirms detection in 30-90ms freshness window. Bot ready for 24h dry-run validation before live cutover.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
13/05/2026 ~06:15 Israel. v3 build complete after user-approved 4-hour focused session while he slept.

**What was built:**
- `ws_feeds/state.py` — SharedState + PlatformBook dataclass, thread-safe via RLock
- `ws_feeds/poly_ws.py` — Polymarket WS, sub format `{"assets_ids":[...], "type":"market", "custom_feature_enabled":true}`, handles `book` + `price_change` + `best_bid_ask` events, silent-freeze detection on first message
- `ws_feeds/predict_ws.py` — Predict.fun WS, topic `predictOrderbook/{market_id}`, market rollover via callable provider, library pings for heartbeat
- `ws_feeds/limitless_ws.py` — Limitless via SDK's WebSocketClient (Socket.IO), zombie detection on 30s no-data, market rollover handled
- `ws_feeds/runner.py` — start_all_feeds() launches 3 threads each with its own asyncio loop
- `arb_v5_3way_live_v3.py` — main bot reads from STATE instead of latest.json files, parse_poly/parse_predict_latest/parse_limitless_latest rewritten to use SharedState

**Smoke test confirmed:**
- All 3 WS feeds connect within 1 second
- Freshness ages: Polymarket 30-200ms (quiet markets), Predict 232-1200ms (end of window), Limitless 7-200ms (active)
- Bot detects A_LIM opportunities at cost 0.585-0.695 (30-42% profit potential)
- Bot CORRECTLY rejects them via STALE_QUOTE_REJECT when age > 80ms threshold
- pre-cache parallel still works (~294ms)
- 3 clients connected, --dry-run mode flushes cleanly

**Known issue / tuning needed:**
The 80ms freshness threshold is conservative — it rejects ~80% of detected opportunities because Limitless updates arrive every 200-300ms in active markets. After 24h observation, user may want to raise to 120-150ms based on actual rejection rate vs opportunity capture.

**Deployment status:**
- File `/root/arb_v5_3way_live_v3.py` on Helsinki (not running)
- v1 (Phase 1 + Round 2 fixes) STILL RUNNING on Helsinki + Hetzner Germany
- Recommended next step: run v3 in dry-run mode alongside v1 live for 24 hours, compare detection rate + age distribution, then decide cutover

**Files committed (commit 10d3778):**
- ws_feeds/__init__.py, state.py, poly_ws.py, predict_ws.py, limitless_ws.py, runner.py
- arb_v5_3way_live_v3.py
- _test_poly_ws.py, _test_predict_ws.py, _test_limitless_ws.py

**Architecture diagram:**
```
Polymarket WS  ─┐
Predict.fun WS ─┼─→ STATE (RLock-protected dict) ─→ Main bot loop
Limitless WS   ─┘                                    │
                                                     ↓
                                          Order placement (sync, ThreadPool)
```

3 threads write STATE; main thread reads. No event loops shared. predict_trader and limitless_trader still sync; only data ingestion went async.

**Remaining work for future sessions:**
- 24h dry-run observation + threshold tuning
- V6_3WAY 1h variant (same diff as v2→v3, ~20 min)
- Oracle divergence filter (now possible — can read Predict's strike via WS)
- Position reconciliation cross-platform (Phase 4)
- Telegram alerts (Phase 4)
