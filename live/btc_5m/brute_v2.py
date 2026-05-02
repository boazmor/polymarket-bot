# -*- coding: utf-8 -*-
"""brute_v2.py — try many variations of the V2 order body and record which
one succeeds (or what specific error each gives back).

The signing/maker/typehash math is verified correct (matches contract).
The mystery is which fields the V2 /order endpoint requires/forbids.
We try permutations and let the server tell us, one by one."""

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

# bootstrap api creds via py-clob-client
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

# pick a current market
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
print(f"Market token: {ids[0][:20]}...\n")

def sign_v2(maker, signer, token_id, maker_amt, taker_amt, side_int,
            sigtype, ts_ms, salt, metadata_b32, builder_b32):
    domain = {"name":"Polymarket CTF Exchange","version":"2","chainId":137,
              "verifyingContract":EXCHANGE_CTF_V2.lower()}
    types = {"Order":[
        {"name":"salt","type":"uint256"},
        {"name":"maker","type":"address"},
        {"name":"signer","type":"address"},
        {"name":"tokenId","type":"uint256"},
        {"name":"makerAmount","type":"uint256"},
        {"name":"takerAmount","type":"uint256"},
        {"name":"side","type":"uint8"},
        {"name":"signatureType","type":"uint8"},
        {"name":"timestamp","type":"uint256"},
        {"name":"metadata","type":"bytes32"},
        {"name":"builder","type":"bytes32"},
    ]}
    msg = {
        "salt":salt,"maker":maker.lower(),"signer":signer.lower(),
        "tokenId":token_id,"makerAmount":maker_amt,"takerAmount":taker_amt,
        "side":side_int,"signatureType":sigtype,"timestamp":ts_ms,
        "metadata":metadata_b32,"builder":builder_b32,
    }
    enc = encode_typed_data(domain_data=domain,message_types=types,message_data=msg)
    sig = Account.sign_message(enc, private_key=priv).signature
    sig_hex = sig.hex()
    if not sig_hex.startswith("0x"): sig_hex = "0x" + sig_hex
    return sig_hex

def try_post(label, body):
    serialized = json.dumps(body, separators=(",",":"))
    ts = str(int(time.time()))
    sig = hmac_sig(creds.api_secret, ts, "POST", "/order", serialized)
    headers = {
        "POLY_ADDRESS": EOA_CS, "POLY_SIGNATURE": sig, "POLY_TIMESTAMP": ts,
        "POLY_API_KEY": creds.api_key, "POLY_PASSPHRASE": creds.api_passphrase,
        "Content-Type": "application/json",
    }
    r = requests.post("https://clob.polymarket.com/order",
                      headers=headers, data=serialized, timeout=15)
    short = r.text[:120].replace("\n"," ")
    status = "✓ SUCCESS" if r.status_code == 200 else f"✗ {r.status_code}"
    print(f"  {status}  {label}: {short}")
    return r.status_code == 200

# common values
PRICE = 0.10
SIZE = 50.0
maker_amt = int(round(SIZE * PRICE * 1_000_000))
taker_amt = int(round(SIZE * 1_000_000))
ZERO_B32 = b"\x00"*32

attempts = []

# Variation A: minimum required fields, FAK
def make_body(orderType="FAK", incl_metadata=True, metadata_val="",
              incl_builder=True, builder_val="0x"+"00"*32,
              salt_str=True, sigtype_int=True,
              addr_lower=True, deferExec=False,
              side_str="BUY", expiration="0",
              maker_amt_=maker_amt, taker_amt_=taker_amt,
              extra_fields=None):
    salt = secrets.randbits(256)
    ts_ms = int(time.time()*1000)
    sig_hex = sign_v2(SAFE_CS, EOA_CS, up_token_id, maker_amt_, taker_amt_,
                      0 if side_str=="BUY" else 1, 2, ts_ms, salt,
                      ZERO_B32, ZERO_B32)
    maker = SAFE_CS.lower() if addr_lower else SAFE_CS
    signer = EOA_CS.lower() if addr_lower else EOA_CS
    o = {
        "maker": maker, "signer": signer,
        "tokenId": str(up_token_id),
        "makerAmount": str(maker_amt_), "takerAmount": str(taker_amt_),
        "side": side_str, "expiration": expiration,
        "timestamp": str(ts_ms),
        "builder": builder_val if incl_builder else None,
        "signature": sig_hex,
        "salt": str(salt) if salt_str else salt,
        "signatureType": 2 if sigtype_int else "POLY_GNOSIS_SAFE",
    }
    if incl_metadata: o["metadata"] = metadata_val
    if extra_fields: o.update(extra_fields)
    o = {k:v for k,v in o.items() if v is not None}
    return {"order": o, "owner": creds.api_key, "orderType": orderType, "deferExec": deferExec}

print("=== running 20 variations ===\n")

variations = [
    ("V01 default GTC", make_body(orderType="GTC")),
    ("V02 default FAK", make_body(orderType="FAK")),
    ("V03 default FOK", make_body(orderType="FOK")),
    ("V04 GTC no metadata", make_body(orderType="GTC", incl_metadata=False)),
    ("V05 GTC metadata=0x00*32", make_body(orderType="GTC", metadata_val="0x"+"00"*32)),
    ("V06 GTC checksummed addrs", make_body(orderType="GTC", addr_lower=False)),
    ("V07 GTC salt as int", make_body(orderType="GTC", salt_str=False)),
    ("V08 GTC sigtype as string", make_body(orderType="GTC", sigtype_int=False)),
    ("V09 FAK + side=0 int", make_body(orderType="FAK", side_str=0)),
    ("V10 GTC no builder", make_body(orderType="GTC", incl_builder=False)),
    ("V11 GTC builder=empty", make_body(orderType="GTC", builder_val="")),
    ("V12 GTC + extra: nonce/feeRateBps/taker",
       make_body(orderType="GTC", extra_fields={"nonce":"0","feeRateBps":"0","taker":"0x"+"00"*20})),
    ("V13 GTC no expiration",
       make_body(orderType="GTC", expiration=None)),
    ("V14 GTC deferExec=True", make_body(orderType="GTC", deferExec=True)),
    ("V15 FAK $1 size", make_body(orderType="FAK",
        maker_amt_=1_000_000, taker_amt_=10_000_000)),
    ("V16 FOK $1 size", make_body(orderType="FOK",
        maker_amt_=1_000_000, taker_amt_=10_000_000)),
    ("V17 GTC tokenId as int", None),  # special — handled separately below
    ("V18 GTC + makerAmount as int", None),  # special
    ("V19 GTC small order ($0.10)",
       make_body(orderType="GTC", maker_amt_=50_000, taker_amt_=500_000)),
    ("V20 GTC tickSize field", make_body(orderType="GTC",
        extra_fields={"tickSize":"0.01"})),
]

for label, body in variations:
    if body is None:
        # special variants
        if label.endswith("tokenId as int"):
            b = make_body(orderType="GTC")
            b["order"]["tokenId"] = up_token_id
            body = b
        elif label.endswith("makerAmount as int"):
            b = make_body(orderType="GTC")
            b["order"]["makerAmount"] = maker_amt
            b["order"]["takerAmount"] = taker_amt
            body = b
        else:
            continue
    try_post(label, body)
    time.sleep(0.3)

print("\n=== done ===")
