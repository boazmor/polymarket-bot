# -*- coding: utf-8 -*-
"""v2_test_with_lib.py — test the new py-clob-client-v2 library with a tiny
limit order at a low price (won't fill, easy to cancel)."""

import os, sys, time, json
from pathlib import Path
import requests

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
print(f"EOA:  {eoa}")
print(f"Safe: {SAFE}")

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgsV2, OrderType, BalanceAllowanceParams, AssetType

POLYGON = 137
c = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON,
               key=priv, signature_type=2, funder=SAFE)
creds = c.create_or_derive_api_key()
c.set_api_creds(creds)
print(f"API key: {creds.api_key}")

# pick a current market via gamma
ep = (int(time.time()) // 300) * 300
slug = f"btc-updown-5m-{ep}"
r = requests.get("https://gamma-api.polymarket.com/markets",
                 params={"slug": slug}, timeout=10).json()
mkts = r if isinstance(r, list) else r.get("markets", [])
if not mkts:
    for i in range(1, 5):
        r2 = requests.get("https://gamma-api.polymarket.com/markets",
                          params={"slug": f"btc-updown-5m-{ep - i*300}"}, timeout=10).json()
        m2 = r2 if isinstance(r2, list) else r2.get("markets", [])
        if m2: mkts = m2; break

m = mkts[0]
ids = m.get("clobTokenIds")
if isinstance(ids, str): ids = json.loads(ids)
up_token_id = ids[0]
print(f"\nMarket slug: {slug}")
print(f"  up_token_id: {up_token_id[:24]}...")

# build a tiny test order: BUY 5 shares @ 0.10 = $0.50
print(f"\nBuilding V2 order: BUY 5 shares @ 0.10 (= $0.50)")
args = OrderArgsV2(
    token_id=up_token_id,
    price=0.10,
    size=5.0,
    side="BUY",
)

try:
    print("Calling create_and_post_order...")
    resp = c.create_and_post_order(args, order_type=OrderType.GTC)
    print(f"\nRESPONSE: {resp}")
    if isinstance(resp, dict):
        oid = resp.get("orderID") or resp.get("orderId")
        if oid:
            print(f"\n>>> SUCCESS! order_id = {oid}")
            print(">>> Cancelling immediately...")
            try:
                cr = c.cancel(order_id=oid)
                print(f">>> cancel result: {cr}")
            except Exception as e:
                print(f">>> cancel error: {e}")
        else:
            print(f">>> response had no order id: {resp}")
except Exception as e:
    print(f"\nFAILED: {type(e).__name__}: {e}")
