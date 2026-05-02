# -*- coding: utf-8 -*-
"""cancel_my_order.py — cancel the test order from v2_test_with_lib.py."""

import sys
from pathlib import Path

ENV_PATHS = [Path(__file__).parent / ".env"]
priv = None
for p in ENV_PATHS:
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if "=" not in line: continue
            k, v = line.strip().split("=", 1)
            if k in ("MY_PRIVATE_KEY","PRIVATE_KEY","WALLET_PRIVATE_KEY"):
                priv = v

SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"
ORDER_ID = "0x80782be42543da5cb8f8b3e2527b117962880515144c7e28a082b5f18f334955"

from py_clob_client_v2.client import ClobClient
c = ClobClient(host="https://clob.polymarket.com", chain_id=137,
               key=priv, signature_type=2, funder=SAFE)
creds = c.create_or_derive_api_key()
c.set_api_creds(creds)

# try cancel_orders (takes a list of order hashes)
try:
    print(f"Cancelling order {ORDER_ID[:24]}...")
    r = c.cancel_orders([ORDER_ID])
    print(f"Result: {r}")
except Exception as e:
    print(f"cancel_orders failed: {e}")
    # fallback: try cancel_order with payload
    try:
        from py_clob_client_v2.clob_types import OrderPayload
        r = c.cancel_order(OrderPayload(orderID=ORDER_ID))
        print(f"cancel_order result: {r}")
    except Exception as e2:
        print(f"cancel_order also failed: {e2}")
