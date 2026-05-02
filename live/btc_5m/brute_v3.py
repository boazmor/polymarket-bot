# -*- coding: utf-8 -*-
"""brute_v3.py — try sending the V2 order with browser-like headers
(User-Agent, Origin, Referer) to test if Polymarket's API blocks
non-browser User-Agents."""

import os, sys, time, json, secrets, base64, hmac, hashlib
from pathlib import Path
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address

ENV_PATHS = [Path(__file__).parent / ".env"]
priv = None
eoa = None
for p in ENV_PATHS:
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if "=" not in line: continue
            k, v = line.strip().split("=", 1)
            if k in ("MY_PRIVATE_KEY","PRIVATE_KEY","WALLET_PRIVATE_KEY"):
                priv = v
            if k in ("MY_ADDRESS","WALLET_ADDRESS","EOA_ADDRESS"):
                eoa = v

SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"
EXCHANGE_CTF_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
EOA_CS = to_checksum_address(eoa)
SAFE_CS = to_checksum_address(SAFE)

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
c = ClobClient("https://clob.polymarket.com", key=priv, chain_id=POLYGON,
               signature_type=2, funder=SAFE_CS)
creds = c.create_or_derive_api_creds()
c.set_api_creds(creds)

def hmac_sig(secret, ts, method, path, body):
    s = base64.urlsafe_b64decode(secret)
    msg = str(ts) + method + path + body
    h = hmac.new(s, msg.encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(h.digest()).decode("utf-8")

ep = (int(time.time()) // 300) * 300
slug = f"btc-updown-5m-{ep}"
r = requests.get("https://gamma-api.polymarket.com/markets",
                 params={"slug": slug}, timeout=10).json()
mkts = r if isinstance(r, list) else r.get("markets", [])
if not mkts:
    for i in range(1, 5):
        r2 = requests.get("https://gamma-api.polymarket.com/markets",
                          params={"slug": f"btc-updown-5m-{ep - i*300}"},
                          timeout=10).json()
        m2 = r2 if isinstance(r2, list) else r2.get("markets", [])
        if m2: mkts = m2; break
m = mkts[0]
ids = m.get("clobTokenIds")
if isinstance(ids, str): ids = json.loads(ids)
up_token_id = int(ids[0])

# build a single signed body
salt = secrets.randbits(256)
ts_ms = int(time.time()*1000)
maker_amt = 5_000_000
taker_amt = 50_000_000
ZERO_B32 = b"\x00"*32

domain = {"name":"Polymarket CTF Exchange","version":"2","chainId":137,
          "verifyingContract":EXCHANGE_CTF_V2.lower()}
types = {"Order":[
    {"name":"salt","type":"uint256"},{"name":"maker","type":"address"},
    {"name":"signer","type":"address"},{"name":"tokenId","type":"uint256"},
    {"name":"makerAmount","type":"uint256"},{"name":"takerAmount","type":"uint256"},
    {"name":"side","type":"uint8"},{"name":"signatureType","type":"uint8"},
    {"name":"timestamp","type":"uint256"},{"name":"metadata","type":"bytes32"},
    {"name":"builder","type":"bytes32"},
]}
msg = {
    "salt":salt,"maker":SAFE_CS.lower(),"signer":EOA_CS.lower(),
    "tokenId":up_token_id,"makerAmount":maker_amt,"takerAmount":taker_amt,
    "side":0,"signatureType":2,"timestamp":ts_ms,
    "metadata":ZERO_B32,"builder":ZERO_B32,
}
enc = encode_typed_data(domain_data=domain,message_types=types,message_data=msg)
sig = Account.sign_message(enc, private_key=priv).signature
sig_hex = sig.hex()
if not sig_hex.startswith("0x"): sig_hex = "0x" + sig_hex

body = {
    "order": {
        "maker": SAFE_CS.lower(), "signer": EOA_CS.lower(),
        "tokenId": str(up_token_id),
        "makerAmount": str(maker_amt), "takerAmount": str(taker_amt),
        "side": "BUY", "expiration": "0", "timestamp": str(ts_ms),
        "metadata": "", "builder": "0x"+"00"*32,
        "signature": sig_hex, "salt": str(salt), "signatureType": 2,
    },
    "owner": creds.api_key, "orderType": "GTC", "deferExec": False,
}
serialized = json.dumps(body, separators=(",",":"))

def try_post(label, extra_headers):
    ts = str(int(time.time()))
    sig = hmac_sig(creds.api_secret, ts, "POST", "/order", serialized)
    headers = {
        "POLY_ADDRESS": EOA_CS, "POLY_SIGNATURE": sig, "POLY_TIMESTAMP": ts,
        "POLY_API_KEY": creds.api_key, "POLY_PASSPHRASE": creds.api_passphrase,
        "Content-Type": "application/json",
        **extra_headers,
    }
    r = requests.post("https://clob.polymarket.com/order",
                      headers=headers, data=serialized, timeout=15)
    short = r.text[:160].replace("\n"," ")
    status = "✓ SUCCESS" if r.status_code == 200 else f"✗ {r.status_code}"
    print(f"  {status}  {label}: {short}")

print("=== testing browser-fingerprint headers ===\n")

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

variations = [
    ("H1 baseline (no UA)", {}),
    ("H2 just Chrome UA", {"User-Agent": UA_CHROME}),
    ("H3 Chrome UA + Origin", {"User-Agent": UA_CHROME,
        "Origin": "https://polymarket.com"}),
    ("H4 Chrome UA + Origin + Referer", {"User-Agent": UA_CHROME,
        "Origin": "https://polymarket.com",
        "Referer": "https://polymarket.com/"}),
    ("H5 Chrome UA + Origin + Referer + Accept", {"User-Agent": UA_CHROME,
        "Origin": "https://polymarket.com",
        "Referer": "https://polymarket.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="126", "Not_A Brand";v="24"',
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        }),
]

for label, eh in variations:
    try_post(label, eh)
    time.sleep(0.4)

# also test alternative endpoints
print("\n=== testing alternative endpoints ===\n")

def try_endpoint(label, path):
    ts = str(int(time.time()))
    sig = hmac_sig(creds.api_secret, ts, "POST", path, serialized)
    headers = {
        "POLY_ADDRESS": EOA_CS, "POLY_SIGNATURE": sig, "POLY_TIMESTAMP": ts,
        "POLY_API_KEY": creds.api_key, "POLY_PASSPHRASE": creds.api_passphrase,
        "Content-Type": "application/json",
        "User-Agent": UA_CHROME,
        "Origin": "https://polymarket.com",
        "Referer": "https://polymarket.com/",
    }
    r = requests.post(f"https://clob.polymarket.com{path}",
                      headers=headers, data=serialized, timeout=15)
    short = r.text[:160].replace("\n"," ")
    status = "✓ SUCCESS" if r.status_code == 200 else f"✗ {r.status_code}"
    print(f"  {status}  {label} ({path}): {short}")

for label, path in [
    ("E1 /order", "/order"),
    ("E2 /v2/order", "/v2/order"),
    ("E3 /api/order", "/api/order"),
    ("E4 /orders", "/orders"),
]:
    try_endpoint(label, path)
    time.sleep(0.4)

print("\n=== done ===")
