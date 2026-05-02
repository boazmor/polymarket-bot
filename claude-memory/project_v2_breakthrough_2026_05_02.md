---
name: V2 breakthrough — first live order placed and bot live-running (2026-05-02)
description: End-of-session milestone — solved the V2 migration block, integrated new library into LIVE bot, verified live trading from Israel
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
## Achievement summary

After ~5 hours of investigation across multiple obstacles, on 2026-05-02 we:

1. **Diagnosed the V2 migration in detail** — Polymarket migrated from CTF Exchange V1 to V2 around end of April 2026. Old library `py-clob-client` v0.34.6 cannot place orders (returns `order_version_mismatch`). Both py-clob-client and TS @polymarket/clob-client are stuck on V1 schema.

2. **Identified the user's real Safe (proxy) address** — `0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B`. Earlier guess `0x4cd00e387622c35bddb9b4c962c136462338bc31` was a deposit-relayer, not the actual Safe. Found by querying `/data/trades` after a $1 manual UI order.

3. **Found the fix via cross-AI consultation** — Two AIs (Perplexity-style + ChatGPT) independently pointed to a separate package: **`py-clob-client-v2`** (version 1.0.0). Installed via `pip install py-clob-client-v2`. The new package contains both V1 and V2 schema handling, EIP-1271 Safe signature support, and graceful retry on the version-mismatch error.

4. **Placed and cancelled the first live order via API** — `v2_test_with_lib.py`: BUY 5 shares @ 0.10 = $0.50 → returned `success: True, status: live, orderID: 0x80782be4...` → cancelled via `cancel_orders([id])` → `canceled: [...]`. Order was real, on chain, and immediately reversible.

5. **Ported `LIVE_BTC_5M_V1_TEST5.py` Wallet class to V2** — Surgical changes only:
   - Imports: `from py_clob_client_v2.client import ClobClient`, `from py_clob_client_v2.clob_types import OrderArgsV2, OrderType, BalanceAllowanceParams, AssetType`
   - `ClobClient(...)` now takes `funder=SAFE_ADDRESS` and `chain_id=137` (literal int, no POLYGON constant in v2)
   - `create_or_derive_api_creds()` → `create_or_derive_api_key()`
   - `place_buy()` uses `OrderArgsV2(...)` and `client.create_and_post_order(args, order_type=OrderType.GTC)` (single call with built-in retry)
   - `cancel()` uses `client.cancel_orders([id])` returning `{canceled: [...], not_canceled: {}}`
   - `fetch_open_orders()` uses `client.get_open_orders()` returning `{data: [...]}`
   - Hardcoded `SAFE_ADDRESS = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"` as class constant

6. **Smoke test passed** — `smoke_test_v2.py` confirmed `connect=True` and `balance=$314.40` on the user's Safe. The Cloudflare 403 noise on the first `/auth/api-key` call is benign — it means the user already has an API key, library falls through to derive it.

7. **Bot running live from user's home in Israel** — As of end of session, `LIVE_BTC_5M_V1_TEST5.py --live` is running and showing live BTC/target/distance updates with both BOT40 and BOT120 in IDLE state, waiting for signal. Test config: $5/trade max, $15 daily loss cap, $500 wallet ceiling.

## Critical context for the next session

- **Geoblock unchanged**: Hetzner Germany IP is still blocked by Polymarket. Israel home IP works. To run 24/7 we need to spin up a Hetzner Helsinki server (or any non-blocked region — US, Singapore). User confirmed he wants this.
- **The user's wallet is currently in Israel residence**: $314.40 in the Safe, ~$1.00 spent today (one $1 manual UI order to find the Safe address; a $0.50 test order via API was cancelled cleanly).
- **Cloudflare 403 on `/auth/api-key` is normal**: don't treat it as an error. It only means the API key already exists. The library handles it.
- **DO NOT use `py-clob-client` (V1) anywhere in the LIVE bot**. The old package is still installed but only `py-clob-client-v2` should be imported in trading code. Old paths in MULTI_COIN_RECORDER.py and other research scripts can stay on V1 since they don't place orders.

## Files added/changed this session

- `live/btc_5m/LIVE_BTC_5M_V1_TEST5.py` — Wallet class ported to V2 (the only LIVE bot file modified)
- `live/btc_5m/v2_test_with_lib.py` — first standalone V2 order test (place + cancel)
- `live/btc_5m/v2_order_test.py` — earlier manual V2 builder (kept for reference; failed because we lacked Safe-correct sig packing)
- `live/btc_5m/diag_geoblock.py` — initial diagnostic that revealed V1 schema error
- `live/btc_5m/find_safe.py` — finds Safe via Alchemy ERC-20 transfer scan
- `live/btc_5m/probe_domain.py` — queries on-chain `eip712Domain()` of V2 contracts
- `live/btc_5m/fetch_my_order.py` — pulls user's recent orders/trades to see V2 wire format
- `live/btc_5m/fetch_order_by_id.py` — fetches single order by hash
- `live/btc_5m/brute_v2.py` / `brute_v3.py` — automated body-format brute-forcers (rendered moot once we found the real package)
- `live/btc_5m/cancel_my_order.py` — cancels a specific test order
- `live/btc_5m/smoke_test_v2.py` — verifies the LIVE bot's Wallet class connects + reads balance
- `claude-memory/project_polymarket_v2_migration_block.md` — updated to RESOLVED status

## Next session priorities

1. Check on the live bot's session — did it find any signals overnight? Any orders placed? PnL?
2. If clean — provision Hetzner Helsinki, deploy bot there, transition to 24/7
3. Consider `chain_id=137` constant cleanup — drop literal in favor of an import if v2 exposes one
4. Check that BOT40 maker engine (3 simultaneous limit orders at 0.28/0.29/0.30) works correctly in live mode — the multi-order placement path hasn't been live-tested yet, only single orders via the test scripts
5. Tighter logging for V2-specific responses (the `take_amount` / `make_amount` fields in successful responses — currently not parsed)
