---
name: Polymarket wallet setup details (CRITICAL for live trading)
description: How the user's Polymarket wallet is structured — Gnosis Safe proxy, signature type, balance/allowance state
type: reference
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
Discovered 2026-05-01 morning during CLOB integration setup.

## Wallet structure

The user's Polymarket account uses a **Gnosis Safe proxy wallet** (Polymarket's standard for users who connect external wallets like Rabby).

- **EOA address** (the externally-owned account, signs transactions): `0x73a6dC847cE7B672F98d14e9F239d97a2C9FdF46`
  - This is what shows in Rabby
  - Has 0 USDC directly (funds aren't held here)
  - Used for SIGNING orders via py_clob_client (`signer` field in order)
- **Safe address (proxy/maker)**: `0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B`
  - CORRECTED 2026-05-02 — earlier guess `0x4cd00e387622c35bddb9b4c962c136462338bc31` was WRONG (it was just a Polymarket deposit relayer the user transferred USDC to, not his actual Safe)
  - The real Safe address was discovered by querying `/data/trades` with his API key — `maker_address` field shows the actual Safe used in completed trades
  - Holds the actual $315.40 (was $15.40 in May 1, user topped up)
  - This MUST be passed as `funder=` to `ClobClient(...)` AND used as `maker` in the order struct — NOT the EOA, NOT the deposit address
  - Pre-approved (unlimited allowance) to V2 contracts: `0xE111180000d2663C0091e4f400237545B87B996B` (CTF V2), `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` (NegRisk Adapter), `0xe2222d279d744050d28e00520010520000310F59` (NegRisk Exchange V2)
  - Polymarket auto-converts USDC to pUSD (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`) for V2 collateral

## How to connect via py-clob-client

```python
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# CRITICAL: signature_type=2 means POLY_GNOSIS_SAFE
c = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=POLYGON,
    signature_type=2,
)
api_creds = c.create_or_derive_api_creds()
c.set_api_creds(api_creds)
```

The other signature types we tested:
- `signature_type=0` (EOA): returned 0 balance
- `signature_type=1` (POLY_PROXY): returned 0 balance — this is for old-style Polymarket proxy wallets
- `signature_type=2` (POLY_GNOSIS_SAFE): returned **$15.40 balance** ✓ — this is the one to use

## Approvals already in place

The Safe wallet has UNLIMITED (`MAX_UINT256`) allowance set for all 3 Polymarket contracts:
- `0xE111180000d2663C0091e4f400237545B87B996B` (Polymarket Conditional Tokens / CTF Exchange)
- `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` (Negrisk adapter or similar)
- `0xe2222d279d744050d28e00520010520000310F59` (Negrisk Exchange)

This means **no extra approval transactions needed before trading** — bot can place orders immediately once connected.

## Current trading capacity

- **$15.40 USDC** available for trading (as of 2026-05-01 morning)
- **0 open orders** (clean slate)
- User said he'd transfer **$400-500** when going live with the MAKER strategy ($60×3=$180 reserved at any time + buffer)

## Implications for the bot

1. The `Wallet` class in `LIVE_BTC_5M_V1.py` MUST initialize with `signature_type=2`.
2. Balance queries MUST use `BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)`.
3. No need to add approval logic to the bot — already done.
4. Order placement goes through the Safe — bot just signs with EOA key.
