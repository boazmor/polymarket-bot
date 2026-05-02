# -*- coding: utf-8 -*-
"""fetch_order_by_id.py — fetch an order's full details by order ID,
to see EXACTLY which fields/format Polymarket UI uses for V2 orders."""

import os, sys, json
from pathlib import Path
import requests

ENV_PATHS = [Path(__file__).parent / ".env", Path("/root/.env")]
priv = None
for p in ENV_PATHS:
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if "=" not in line: continue
            k, v = line.strip().split("=", 1)
            if k in ("MY_PRIVATE_KEY", "PRIVATE_KEY", "WALLET_PRIVATE_KEY"):
                priv = v

# the user's most recent successful order from /data/trades
ORDER_ID = "0xe586e05be2719f4f5d2382b1a1c763273179c64d44d5e25a04dbf444a5de5858"
SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.headers.headers import create_level_2_headers
from py_clob_client.clob_types import RequestArgs

c = ClobClient("https://clob.polymarket.com", key=priv, chain_id=POLYGON,
               signature_type=2, funder=SAFE)
creds = c.create_or_derive_api_creds()
c.set_api_creds(creds)

# try multiple known endpoints
paths = [
    f"/data/order/{ORDER_ID}",
    f"/data/orders/{ORDER_ID}",
    f"/orders/{ORDER_ID}",
    f"/data/order?order_id={ORDER_ID}",
]
for path in paths:
    print(f"\n=== GET {path} ===")
    req = RequestArgs(method="GET", request_path=path)
    headers = create_level_2_headers(c.signer, c.creds, req)
    r = requests.get(f"https://clob.polymarket.com{path}", headers=headers, timeout=20)
    print(f"  HTTP {r.status_code}")
    print(f"  body: {r.text[:3000]}")

# also fetch all trades with full detail
print("\n=== GET /data/trades (full first trade) ===")
req = RequestArgs(method="GET", request_path="/data/trades")
headers = create_level_2_headers(c.signer, c.creds, req)
r = requests.get("https://clob.polymarket.com/data/trades", headers=headers, timeout=20)
if r.status_code == 200:
    data = r.json().get("data", [])
    if data:
        print(json.dumps(data[0], indent=2))
