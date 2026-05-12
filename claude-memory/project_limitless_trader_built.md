---
name: limitless_trader.py built and USDC approved on Base — 12/05
description: limitless_trader.py module shipped to /root on Helsinki, HMAC auth confirmed, USDC approval tx done on Base. Foundation ready for arb_3way_live.py integration.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
End of session 12/05 18:40 UTC.

**Built:**
- `/root/limitless_trader.py` on Helsinki. Sync wrapper around async `limitless-sdk` (v1.0.9). Reuses one persistent event loop. Methods: `place_fak_buy`, `place_fak_sell`, `cancel`, `cancel_all`, `get_market`, `close`. HMAC auth via `HMACCredentials(tokenId, secret)`. Smoke-tested — fetched live market `btc-up-or-down-15-min-...`, venue.exchange resolved to `0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5`, tokens.yes/tokens.no token IDs visible in `market.tokens` (object, not list).
- `/root/approve_limitless_usdc.py` on Helsinki — one-shot script for USDC allowance on Base. Now committed to repo too.

**On-chain state:**
- Base USDC allowance from `0x73a6dC...dF46` to venue `0x05c748E2...` is now **unlimited** (max_uint256)
- Approval tx: `0x6f90ceec1658537b15d7ab65675e41ed33abd97fa9d53167b7dbf338a50e2ee9`, block 45910858, gas_used 55785
- Base wallet balance after tx: $49.86 USDC, 0.000531 ETH (gas remaining)

**Empirical findings on Limitless market schema:**
- `market.tokens` is an object, NOT a list: `market.tokens.yes` (token ID string), `market.tokens.no` (token ID string)
- `market.collateral_token.address = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` (USDC on Base, 6 decimals)
- `market.settings.min_size = '100000000'` — meaning UNCONFIRMED. Could be 100 USDC ($100 min, way above our $1.20 base) OR 100 shares ($50 at $0.50/share). Must be empirically tested with a real FAK BUY before scaling up the bot.
- `metadata.is_poly_arbitrage: true` — Limitless flags markets that mirror Polymarket markets (useful for same-oracle pair detection)
- `venue.exchange` is dynamic per market — always cache via `market_fetcher.get_market(slug)` BEFORE `create_order` for performance and signing correctness

**Outstanding work (next session):**
1. Test a single $1.20 FAK BUY on a current 5-min or 15-min Limitless market to confirm:
   - Whether `min_size=100000000` blocks at $100 or $50 or $0.10
   - Whether the SignedOrder.maker_amount/taker_amount values are scaled by 1e6 (USDC decimals) or differently for FAK
   - Whether shares received match `matched_size * price` accounting
2. Build `arb_3way_live.py` per `project_arb_3way_strategy_spec.md` — parallel-fire if both sides ≥ 4x liquidity, sequential else, top-up from 3rd platform on shortfall, emergency sell only if >10% imbalance
3. ConditionalTokens approval (`setApprovalForAll` on the CTF contract) needed before SELL works — defer until selling is required for emergency unwind path
