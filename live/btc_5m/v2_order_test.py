# -*- coding: utf-8 -*-
"""v2_order_test.py — manually build & post a Polymarket V2 order.
Bypasses py-clob-client's order builder (still on V1 schema).

V2 Order struct (from CTFExchange V2 contract source on Polygonscan):
  salt, maker, signer, tokenId, makerAmount, takerAmount,
  side(uint8), signatureType(uint8), timestamp(uint256 ms),
  metadata(bytes32), builder(bytes32)

ORDER_TYPEHASH (from contract):
  0xbb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589

Domain:
  name="Polymarket CTF Exchange", version="2", chainId=137,
  verifyingContract=<exchange address>
"""

import os, sys, time, json, secrets, base64, hmac, hashlib
from pathlib import Path
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

# ---------------------------------------------------------------- env
ENV_PATHS = [Path(__file__).parent / ".env", Path("/root/.env")]
priv = None
eoa = None
for p in ENV_PATHS:
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if "=" not in line: continue
            k, v = line.strip().split("=", 1)
            if k in ("MY_PRIVATE_KEY", "PRIVATE_KEY", "WALLET_PRIVATE_KEY"):
                priv = v
            if k in ("MY_ADDRESS", "WALLET_ADDRESS", "EOA_ADDRESS"):
                eoa = v

if not priv or not eoa:
    print("FAIL: missing private key or EOA in .env"); sys.exit(1)

safe_addr = sys.argv[1] if len(sys.argv) > 1 else None
if not safe_addr:
    print("Usage: py v2_order_test.py <safe_address>")
    sys.exit(1)
# keep checksummed case for API; lowercase only for signing where EIP-712 normalizes
from eth_utils import to_checksum_address
safe_addr_cs = to_checksum_address(safe_addr)
eoa_cs = to_checksum_address(eoa)
safe_addr = safe_addr_cs
eoa_lc = eoa_cs

print(f"EOA:  {eoa_lc}")
print(f"Safe: {safe_addr}")

# ---------------------------------------------------------------- contracts
EXCHANGE_CTF_V2     = "0xE111180000d2663C0091e4f400237545B87B996B"
EXCHANGE_NEGRISK_V2 = "0xe2222d279d744050d28e00520010520000310F59"

# ---------------------------------------------------------------- API creds (still works via py-clob-client)
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

c = ClobClient("https://clob.polymarket.com", key=priv, chain_id=POLYGON,
               signature_type=2, funder=safe_addr)
creds = c.create_or_derive_api_creds()
c.set_api_creds(creds)
print(f"API key: {creds.api_key}")

# verify balance
try:
    bp = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
    bal = c.get_balance_allowance(bp)
    raw = bal.get("balance") if isinstance(bal, dict) else None
    if raw is not None:
        print(f"Safe balance: ${int(raw)/1_000_000:.2f}")
except Exception as e:
    print(f"balance check failed: {e}")

# ---------------------------------------------------------------- pick a market
ep = (int(time.time()) // 300) * 300
slug = f"btc-updown-5m-{ep}"
r = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug}, timeout=10).json()
mkts = r if isinstance(r, list) else r.get("markets", [])
if not mkts:
    for i in range(1, 5):
        slug2 = f"btc-updown-5m-{ep - i*300}"
        r2 = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug2}, timeout=10).json()
        m2 = r2 if isinstance(r2, list) else r2.get("markets", [])
        if m2: slug, mkts = slug2, m2; break
m = mkts[0]
neg_risk = m.get("negRisk", False)
ids = m.get("clobTokenIds")
if isinstance(ids, str): ids = json.loads(ids)
up_token_id = int(ids[0])
verifying_contract = (EXCHANGE_NEGRISK_V2 if neg_risk else EXCHANGE_CTF_V2).lower()
print(f"\nMarket: {slug}")
print(f"  negRisk: {neg_risk}")
print(f"  exchange: {verifying_contract}")
print(f"  up_token_id: {ids[0][:24]}...")

# ---------------------------------------------------------------- typehash sanity
type_str = ("Order(uint256 salt,address maker,address signer,uint256 tokenId,"
            "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
            "uint256 timestamp,bytes32 metadata,bytes32 builder)")
computed_hash = "0x" + keccak(text=type_str).hex()
expected_hash = "0xbb86318a2138f5fa8ae32fbe8e659f8fcf13cc6ae4014a707893055433818589"
print(f"\nTYPEHASH:")
print(f"  computed: {computed_hash}")
print(f"  expected: {expected_hash}")
print(f"  match:    {computed_hash == expected_hash}")

# ---------------------------------------------------------------- build V2 order
PRICE       = 0.10           # cheap, won't fill
SIZE_SHARES = 50.0           # = $5 order (5 shares was below min)
maker_amount = int(round(SIZE_SHARES * PRICE * 1_000_000))   # USDC 6 dec
taker_amount = int(round(SIZE_SHARES * 1_000_000))           # CTF 6 dec
salt        = secrets.randbits(256)
timestamp_ms = int(time.time() * 1000)
ZERO_BYTES32 = b"\x00" * 32

order_struct = {
    "salt":          salt,
    "maker":         safe_addr,
    "signer":        eoa_lc,
    "tokenId":       up_token_id,
    "makerAmount":   maker_amount,
    "takerAmount":   taker_amount,
    "side":          0,   # BUY
    "signatureType": 2,   # POLY_GNOSIS_SAFE
    "timestamp":     timestamp_ms,
    "metadata":      ZERO_BYTES32,
    "builder":       ZERO_BYTES32,
}

domain = {
    "name":              "Polymarket CTF Exchange",
    "version":           "2",
    "chainId":           137,
    "verifyingContract": verifying_contract,
}
types = {
    "Order": [
        {"name": "salt",          "type": "uint256"},
        {"name": "maker",         "type": "address"},
        {"name": "signer",        "type": "address"},
        {"name": "tokenId",       "type": "uint256"},
        {"name": "makerAmount",   "type": "uint256"},
        {"name": "takerAmount",   "type": "uint256"},
        {"name": "side",          "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
        {"name": "timestamp",     "type": "uint256"},
        {"name": "metadata",      "type": "bytes32"},
        {"name": "builder",       "type": "bytes32"},
    ]
}

encodable = encode_typed_data(domain_data=domain, message_types=types, message_data=order_struct)
signed = Account.sign_message(encodable, private_key=priv)
signature_hex = signed.signature.hex()
if not signature_hex.startswith("0x"):
    signature_hex = "0x" + signature_hex
print(f"\nSigned. sig[:30]={signature_hex[:30]}...")

# Local verification: recover address from signature, must equal EOA
recovered = Account.recover_message(encodable, signature=signed.signature)
print(f"  recovered address: {recovered}")
print(f"  expected EOA:      {eoa}")
print(f"  match:             {recovered.lower() == eoa.lower()}")

# ---------------------------------------------------------------- POST body
# Per Polymarket docs (api-reference/trade/post-a-new-order.md):
#  - salt is INTEGER (not string)
#  - expiration is in the body but NOT signed (kept for GTD; "0" for GTC)
#  - metadata is empty string "" (not bytes32 hex)
#  - builder is bytes32 hex
#  - signatureType is INTEGER
#  - deferExec is at top level (default false)
body = {
    "order": {
        "maker":         safe_addr,
        "signer":        eoa_lc,
        "tokenId":       str(up_token_id),
        "makerAmount":   str(maker_amount),
        "takerAmount":   str(taker_amount),
        "side":          "BUY",
        "expiration":    "0",
        "timestamp":     str(timestamp_ms),
        "builder":       "0x" + "00" * 32,
        "signature":     signature_hex,
        "salt":          str(salt),
        "signatureType": 2,
    },
    "owner":     creds.api_key,
    "orderType": "FAK",
    "deferExec": False,
}
serialized = json.dumps(body, separators=(",", ":"))

# ---------------------------------------------------------------- HMAC L2 headers
def build_hmac(secret, ts, method, path, body_str):
    s = base64.urlsafe_b64decode(secret)
    msg = str(ts) + method + path + body_str
    h = hmac.new(s, msg.encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(h.digest()).decode("utf-8")

ts_now = str(int(time.time()))
sig = build_hmac(creds.api_secret, ts_now, "POST", "/order", serialized)
headers = {
    "POLY_ADDRESS":    eoa,
    "POLY_SIGNATURE":  sig,
    "POLY_TIMESTAMP":  ts_now,
    "POLY_API_KEY":    creds.api_key,
    "POLY_PASSPHRASE": creds.api_passphrase,
    "Content-Type":    "application/json",
}

# ---------------------------------------------------------------- POST
print(f"\nPOSTing to /order ...")
print(f"  FULL body: {serialized}")
try:
    resp = requests.post("https://clob.polymarket.com/order",
                         headers=headers, data=serialized, timeout=30)
    print(f"\n  HTTP {resp.status_code}")
    print(f"  resp headers: {dict(resp.headers)}")
    print(f"  resp body: {resp.text[:800]}")

    if resp.status_code == 200:
        try:
            d = resp.json()
            oid = d.get("orderID") or d.get("orderId")
            if oid:
                print(f"\n>>> SUCCESS! order_id = {oid}")
                # cancel
                cancel_body = json.dumps({"orderID": oid}, separators=(",", ":"))
                ts2 = str(int(time.time()))
                sig2 = build_hmac(creds.api_secret, ts2, "DELETE", "/order", cancel_body)
                ch = {**headers, "POLY_SIGNATURE": sig2, "POLY_TIMESTAMP": ts2}
                cr = requests.delete("https://clob.polymarket.com/order",
                                     headers=ch, data=cancel_body, timeout=30)
                print(f">>> cancel: HTTP {cr.status_code} {cr.text[:200]}")
        except Exception as e:
            print(f"  json parse error: {e}")
except Exception as e:
    print(f"  POST error: {type(e).__name__}: {e}")
