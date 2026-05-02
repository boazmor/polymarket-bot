# -*- coding: utf-8 -*-
"""fetch_my_order.py — pull the user's open orders from Polymarket so we can
see the exact field format the V2 API uses. The user places ONE manual order
through the UI; this script queries it back and prints the raw JSON.
"""

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
if not priv:
    print("FAIL: no private key in .env"); sys.exit(1)

safe_addr = sys.argv[1] if len(sys.argv) > 1 else None
if not safe_addr:
    print("Usage: py fetch_my_order.py <safe_address>"); sys.exit(1)

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.headers.headers import create_level_2_headers
from py_clob_client.clob_types import RequestArgs

c = ClobClient("https://clob.polymarket.com", key=priv, chain_id=POLYGON,
               signature_type=2, funder=safe_addr)
creds = c.create_or_derive_api_creds()
c.set_api_creds(creds)
print(f"API key: {creds.api_key}\n")

# Hit /data/orders with L2 auth — should return the user's open orders
endpoints_to_try = [
    "/data/orders",
    "/orders",
    "/data/order",
]

for path in endpoints_to_try:
    print(f"=== GET {path} ===")
    req = RequestArgs(method="GET", request_path=path)
    headers = create_level_2_headers(c.signer, c.creds, req)
    r = requests.get(f"https://clob.polymarket.com{path}", headers=headers, timeout=20)
    print(f"  HTTP {r.status_code}")
    print(f"  body: {r.text[:2000]}")
    print()

# Also try trades history
for path in ["/data/trades", "/trades"]:
    print(f"=== GET {path} ===")
    req = RequestArgs(method="GET", request_path=path)
    headers = create_level_2_headers(c.signer, c.creds, req)
    r = requests.get(f"https://clob.polymarket.com{path}", headers=headers, timeout=20)
    print(f"  HTTP {r.status_code}")
    print(f"  body: {r.text[:2000]}")
    print()
