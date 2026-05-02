# -*- coding: utf-8 -*-
"""diag_geoblock.py v3 — look up Polymarket Safe (proxy) address for the
EOA, then sign orders with funder=<safe_address>. This is the missing
piece that caused order_version_mismatch — for signature_type=2 the
maker MUST be the Safe address, not the EOA."""

import os, sys, time, json
from pathlib import Path
import requests

# ---------------------------------------------------------------- env
ENV_PATHS = [
    Path(__file__).parent / ".env",
    Path("/root/.env"),
]
loaded_priv = None
loaded_eoa = None
for p in ENV_PATHS:
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line: continue
                k, v = line.strip().split("=", 1)
                if k in ("PRIVATE_KEY", "MY_PRIVATE_KEY", "WALLET_PRIVATE_KEY"):
                    loaded_priv = v
                if k in ("MY_ADDRESS", "WALLET_ADDRESS", "EOA_ADDRESS"):
                    loaded_eoa = v
        if loaded_priv: break

if not loaded_priv:
    print("FAIL: no private key"); sys.exit(1)
if not loaded_eoa:
    print("FAIL: no EOA address (MY_ADDRESS) in .env"); sys.exit(1)

print(f"EOA address: {loaded_eoa}")
print(f"Private key loaded ({len(loaded_priv)} chars)")

# ---------------------------------------------------------------- look up the Safe
def lookup_safe_address(eoa: str) -> str | None:
    """Try several Polymarket public endpoints to find the Safe (proxy) address."""
    eoa_lc = eoa.lower()
    candidates = [
        # data-api.polymarket.com positions: returns objects with `proxyWallet`
        ("data-api positions", f"https://data-api.polymarket.com/positions?user={eoa_lc}"),
        # data-api activity: also includes proxyWallet
        ("data-api activity",  f"https://data-api.polymarket.com/activity?user={eoa_lc}&limit=1"),
        # data-api value: account value, includes user/proxy info
        ("data-api value",     f"https://data-api.polymarket.com/value?user={eoa_lc}"),
        # gamma users
        ("gamma users",        f"https://gamma-api.polymarket.com/users?address={eoa_lc}"),
    ]
    for label, url in candidates:
        try:
            r = requests.get(url, timeout=10)
            print(f"  [{label}] HTTP {r.status_code}, body[:200]={r.text[:200]!r}")
            if r.status_code != 200:
                continue
            data = r.json()
            # walk the structure looking for a 0x-address that isn't the EOA itself
            def find_addr(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k.lower() in ("proxywallet", "proxy_wallet", "proxy", "safe", "wallet", "address") and isinstance(v, str) and v.startswith("0x") and len(v) == 42 and v.lower() != eoa_lc:
                            return v
                        r = find_addr(v)
                        if r: return r
                elif isinstance(obj, list):
                    for item in obj:
                        r = find_addr(item)
                        if r: return r
                return None
            found = find_addr(data)
            if found:
                print(f"  >>> SAFE found via {label}: {found}")
                return found
        except Exception as e:
            print(f"  [{label}] error: {e}")
    return None

print("\nLooking up Safe (proxy) address from public Polymarket APIs...")
safe_addr = lookup_safe_address(loaded_eoa)

if not safe_addr:
    # allow override via env or CLI arg
    safe_addr = os.environ.get("SAFE_ADDRESS") or (sys.argv[1] if len(sys.argv) > 1 else None)

if not safe_addr:
    print("\n>>> Could not auto-detect Safe address.")
    print(">>> Open https://polymarket.com, log in with your wallet, click your")
    print(">>> profile icon in the top right — the 0x... address shown there is")
    print(">>> your Safe address. Re-run this script with that address:")
    print(">>>   py diag_geoblock.py 0xYourSafeAddressHere")
    sys.exit(1)

print(f"\nUsing Safe address as funder: {safe_addr}")

# ---------------------------------------------------------------- clob client
import py_clob_client
print(f"py-clob-client version: {getattr(py_clob_client, '__version__', 'unknown')}")

# === MONKEY-PATCH: override stale exchange addresses in py-clob-client 0.34.6 ===
# Polymarket migrated their exchange contracts but the library still has old ones.
# Old: 0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E (CTF) / 0xC5d563A36AE78145C45a50134d48A1215220f80a (NegRisk)
# New: 0xE111180000d2663C0091e4f400237545B87B996B (CTF) / 0xe2222d279d744050d28e00520010520000310F59 (NegRisk)
import py_clob_client.config as _cfg
from py_clob_client.clob_types import ContractConfig

_NEW_EXCHANGE_CTF     = "0xE111180000d2663C0091e4f400237545B87B996B"
_NEW_EXCHANGE_NEGRISK = "0xe2222d279d744050d28e00520010520000310F59"
_COLLATERAL = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
_CTF        = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

def _patched_get_contract_config(chainID: int, neg_risk: bool = False) -> ContractConfig:
    if chainID != 137:
        # for non-Polygon, use the original
        return _orig_get_contract_config(chainID, neg_risk)
    return ContractConfig(
        exchange=_NEW_EXCHANGE_NEGRISK if neg_risk else _NEW_EXCHANGE_CTF,
        collateral=_COLLATERAL,
        conditional_tokens=_CTF,
    )

_orig_get_contract_config = _cfg.get_contract_config
_cfg.get_contract_config = _patched_get_contract_config
# also patch the symbol imported into the order builder module
import py_clob_client.order_builder.builder as _ob_builder
_ob_builder.get_contract_config = _patched_get_contract_config
print("Patched py-clob-client to use NEW Polymarket exchange addresses.")

# Also patch the EIP-712 domain version used to sign orders.
# py-order-utils hardcodes version="1" but V2 contracts may expect "2".
import py_order_utils.builders.base_builder as _bb
from poly_eip712_structs import make_domain as _make_domain
_DOMAIN_VERSION = os.environ.get("CLOB_DOMAIN_VERSION", "2")
_DOMAIN_NAME    = os.environ.get("CLOB_DOMAIN_NAME", "Polymarket CTF Exchange")
def _patched_get_domain_separator(self, chain_id, verifying_contract):
    return _make_domain(
        name=_DOMAIN_NAME,
        version=_DOMAIN_VERSION,
        chainId=str(chain_id),
        verifyingContract=verifying_contract,
    )
_bb.BaseBuilder._get_domain_separator = _patched_get_domain_separator
print(f"Patched EIP-712 domain to version={_DOMAIN_VERSION}, name={_DOMAIN_NAME!r}")
# === END PATCH ===

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType, OrderType, PartialCreateOrderOptions

c = ClobClient(
    "https://clob.polymarket.com",
    key=loaded_priv,
    chain_id=POLYGON,
    signature_type=2,
    funder=safe_addr,
)
creds = c.create_or_derive_api_creds()
c.set_api_creds(creds)

# verify balance to confirm we're talking to the right account
try:
    bp = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
    bal = c.get_balance_allowance(bp)
    raw = bal.get("balance") if isinstance(bal, dict) else None
    if raw is not None:
        print(f"USDC balance on this Safe: ${int(raw)/1_000_000:.2f}")
    else:
        print(f"balance response: {bal}")
except Exception as e:
    print(f"balance check failed: {e}")

# ---------------------------------------------------------------- pick a market
ep = (int(time.time()) // 300) * 300
slug = f"btc-updown-5m-{ep}"
print(f"\nMarket slug: {slug}")
r = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug}, timeout=15)
data = r.json()
markets = data if isinstance(data, list) else data.get("markets", [])
if not markets:
    for i in range(1, 6):
        ep2 = ep - i * 300
        slug2 = f"btc-updown-5m-{ep2}"
        r2 = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug2}, timeout=15)
        d2 = r2.json()
        m2 = d2 if isinstance(d2, list) else d2.get("markets", [])
        if m2: slug, markets = slug2, m2; break
m0 = markets[0]
neg_risk = m0.get("negRisk", False)
print(f"  negRisk (gamma): {neg_risk}")
raw_ids = m0.get("clobTokenIds")
if isinstance(raw_ids, str): raw_ids = json.loads(raw_ids)
up_token = str(raw_ids[0])
print(f"  up_token: {up_token[:20]}...")

# also check what get_neg_risk says — that's what the library actually uses
try:
    api_neg_risk = c.get_neg_risk(up_token)
    print(f"  negRisk (CLOB API): {api_neg_risk}")
except Exception as e:
    print(f"  get_neg_risk failed: {e}")

# ---------------------------------------------------------------- attempt
print("\n--- Attempt: limit BUY @ 0.10, size=5 (won't fill, easy to cancel) ---")
try:
    args = OrderArgs(price=0.10, size=5.0, side="BUY", token_id=up_token)
    signed = c.create_order(args)
    print(f"  order built. maker={signed.order['maker']}, signer={signed.order['signer']}, sigType={signed.order['signatureType']}")
    resp = c.post_order(signed, orderType=OrderType.GTC)
    print(f"  RESPONSE: {resp}")
    if isinstance(resp, dict):
        oid = resp.get("orderID") or resp.get("orderId")
        if oid:
            print(f"\n>>> SUCCESS! Order ID: {oid}")
            print(f"  Cancelling...")
            try:
                c.cancel(order_id=oid)
                print(f"  cancelled OK")
            except Exception as e:
                print(f"  cancel failed: {e}")
            print("\n>>> WORKING. The bot needs funder=<safe_addr> in ClobClient(...).")
            sys.exit(0)
        else:
            print(f"\n>>> response had no orderID — {resp}")
except Exception as e:
    msg = str(e)
    if len(msg) > 400: msg = msg[:400] + "..."
    print(f"  FAILED: {type(e).__name__}: {msg}")
    sys.exit(2)
