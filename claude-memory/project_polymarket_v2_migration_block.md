---
name: Polymarket V2 migration broke all third-party order libraries (2026-05-02)
description: Live trading is blocked by a Polymarket exchange contract migration that the official Python and TypeScript clients have NOT been updated to support
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
## TL;DR — RESOLVED 2026-05-02
**The fix was to install a DIFFERENT package: `py-clob-client-v2` (not `py-clob-client`).** Polymarket released a separate v2 package and stopped updating the original. Verified: a real $0.50 limit order was placed and successfully cancelled via the new library on 2026-05-02.

```bash
py -m pip install py-clob-client-v2  # version 1.0.0 — has both V1 and V2 schemas
```

The new library handles everything automatically: V2 typehash, EIP-1271 Safe signatures, the new Order struct fields (timestamp/metadata/builder), and graceful retry on the version-mismatch error.

## What happened
End of March 2026: Polymarket deployed a new "CTF Exchange V2" contract at `0xE111180000d2663C0091e4f400237545B87B996B` (and Negrisk V2 at `0xe2222d279d744050d28e00520010520000310F59`).

End of April 2026: server-side switched to require V2 signatures. From that day, every order placed via py-clob-client v0.34.6 and @polymarket/clob-client v5.8.2 is rejected with `order_version_mismatch`.

Polymarket has NOT yet released an update to their public Python or TypeScript clients (status as of 2026-05-02). 4 open GitHub issues, none resolved.

## What's different in V2 (verified from contract source on Polygonscan)
- **Exchange addresses (NEW)**: CTF=`0xE111180000d2663C0091e4f400237545B87B996B`, NegRisk=`0xe2222d279d744050d28e00520010520000310F59`
- **EIP-712 domain**: name="Polymarket CTF Exchange", **version="2"** (was "1"), chainId=137, verifyingContract=<exchange>
- **Order struct fields** (different from V1):
  - REMOVED: `taker`, `expiration`, `nonce`, `feeRateBps`
  - ADDED: `timestamp` (uint256 ms), `metadata` (bytes32), `builder` (bytes32)
  - Final fields: salt, maker, signer, tokenId, makerAmount, takerAmount, side(uint8), signatureType(uint8), timestamp, metadata, builder
- **ORDER_TYPEHASH**: `0xbb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589`
- **Collateral migrated** from USDC.e (`0x2791...`) to pUSD (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`). The user's $315.40 in the Safe is wrapped automatically.

## What works in our code
We wrote `live/btc_5m/v2_order_test.py` — bypasses py-clob-client for order signing but uses it for API key derivation and balance queries. Verified:
- typehash computed in Python matches contract's expected value (sanity check at startup)
- domain values match the contract's `eip712Domain()` view function (queried via Alchemy RPC)
- signature recovers locally to the EOA address (eth_account verification)
- balance check returns $315.40 — proves L2 HMAC headers are valid

## What still doesn't work
POST `/order` returns HTTP 400 `{"error":"Invalid order payload"}`. We tried these body variants — all rejected:
- minimal V2 schema (12 required fields, salt as string)
- with `metadata: ""` (empty)
- with `metadata: "0x" + "00"*32` (bytes32 hex)
- with V1 backward-compat fields added (taker, nonce, feeRateBps)
- without V1 fields, exact OpenAPI property order

Polymarket's API gives no specific error — just generic "Invalid order payload". Without server logs or maintainer help we can't pinpoint which field they're rejecting.

## User's wallet status
- Safe address: `0x4cd00e387622c35bddb9b4c962c136462338bc31`
- Balance: $315.40 (auto-wrapped to pUSD by Polymarket)
- API key: `e65fbe14-f6ad-19b6-a04c-780d6c0d37a5`
- Pre-approvals to V2 contracts: ALREADY SET (unlimited allowance)
- The wallet is ready — only the client library is the blocker

## Files in repo for V2 work
- `live/btc_5m/v2_order_test.py` — manual V2 order builder (does NOT work yet, server rejects)
- `live/btc_5m/diag_geoblock.py` — earlier diagnostic with monkey-patched exchange addresses (also rejected)
- `live/btc_5m/find_safe.py` — finds Safe address by scanning USDC transfers via Alchemy RPC
- `live/btc_5m/probe_domain.py` — queries on-chain `eip712Domain()` view function on V2 contracts

## How to resume
1. ✅ DONE — installed `py-clob-client-v2`
2. ✅ DONE — verified order place + cancel works via `live/btc_5m/v2_test_with_lib.py`
3. NEXT — port LIVE_BTC_5M_V1_TEST5.py's `Wallet` class to use `py_clob_client_v2.client.ClobClient` instead of `py_clob_client.client.ClobClient`. API differences:
   - Method renamed: `create_or_derive_api_creds()` → `create_or_derive_api_key()`
   - Order arg type: `OrderArgs` → `OrderArgsV2`
   - Cancel: `cancel(order_id=...)` → `cancel_orders([order_id])` (returns dict with `canceled`/`not_canceled` keys)
   - Posting: prefer `create_and_post_order(args, order_type=OrderType.GTC)` — single call with built-in retry on version mismatch
   - Imports: from `py_clob_client_v2.client` and `py_clob_client_v2.clob_types`
   - Same `funder=safe_address`, same `signature_type=2` for Gnosis Safe
4. Cloudflare may return a transient 403 on `/auth/api-key` — the library handles it (logs warning, falls through to derive). Don't treat that 403 as fatal in the bot.

## Why not write our own V2 client from scratch
We DID write one (v2_order_test.py). It's 200 lines. The math (typehash, domain, signing) is all correct. But the API rejects every body variant we try without telling us why. There's a server-side validation we don't have visibility into — likely something in how `metadata`/`builder` should be encoded, or possibly an undocumented header. Burning more attempts costs little but gains less and less. Better to wait for an official fix or community workaround.

## Meanwhile: simulation continues
LIVE_BTC_5M_V1_TEST5 in dry-run mode keeps running on the Hetzner server. Strategy data, hourly bucket findings, NYC 05-06 worst-hours, and recorder for 7 coins all unaffected.
