# -*- coding: utf-8 -*-
"""find_safe.py — locate the Polymarket Safe (proxy) address for the EOA
by scanning USDC Transfer logs from the EOA via Polygon RPC.

Polymarket's Safe holds the user's USDC. To deposit, the user transferred
USDC FROM their EOA TO the Safe. So the recipient of any past USDC
Transfer originating from the EOA is the Safe address.
"""

import os, sys, json
from pathlib import Path
import requests

ENV_PATHS = [Path(__file__).parent / ".env", Path("/root/.env")]
eoa = None
rpc_url = None
for p in ENV_PATHS:
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line: continue
                k, v = line.strip().split("=", 1)
                if k in ("MY_ADDRESS", "WALLET_ADDRESS", "EOA_ADDRESS"):
                    eoa = v
                if k == "POLYGON_RPC_URL":
                    rpc_url = v

if not eoa or not rpc_url:
    print("FAIL: missing MY_ADDRESS or POLYGON_RPC_URL in .env"); sys.exit(1)

USDC_NATIVE   = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"  # USDC native
USDC_BRIDGED  = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"  # USDC.e

print(f"EOA: {eoa}")
print(f"Asking Alchemy for all ERC-20 transfers OUT of your EOA...")
print(f"(method: alchemy_getAssetTransfers — works on free tier)")

# OUTGOING (EOA -> ?)
payload_out = {
    "jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers",
    "params": [{
        "fromBlock": "0x0",
        "toBlock":   "latest",
        "fromAddress": eoa,
        "category": ["erc20"],
        "withMetadata": False,
        "excludeZeroValue": True,
        "maxCount": "0x3e8",  # 1000
    }]
}
# INCOMING (? -> EOA)  — fallback: maybe the Safe sent something back
payload_in = dict(payload_out)
payload_in["id"] = 2
payload_in["params"] = [dict(payload_out["params"][0])]
payload_in["params"][0].pop("fromAddress")
payload_in["params"][0]["toAddress"] = eoa

def query(payload, label):
    try:
        r = requests.post(rpc_url, json=payload, timeout=30)
        data = r.json()
        if "error" in data:
            print(f"  [{label}] RPC error: {data['error']}")
            return []
        return data.get("result", {}).get("transfers", []) or []
    except Exception as e:
        print(f"  [{label}] failed: {e}")
        return []

out_transfers = query(payload_out, "outgoing")
in_transfers  = query(payload_in,  "incoming")

print(f"  outgoing: {len(out_transfers)} transfers")
print(f"  incoming: {len(in_transfers)} transfers")

# Build candidate set: any USDC counterparty
usdc_set = {USDC_NATIVE.lower(), USDC_BRIDGED.lower()}
counterparties = {}

for t in out_transfers:
    asset_addr = (t.get("rawContract", {}).get("address") or "").lower()
    if asset_addr not in usdc_set: continue
    to_addr = (t.get("to") or "").lower()
    if not to_addr: continue
    val = float(t.get("value") or 0)
    counterparties.setdefault(to_addr, {"sent": 0, "recv": 0, "first_block": t.get("blockNum")})
    counterparties[to_addr]["sent"] += val

for t in in_transfers:
    asset_addr = (t.get("rawContract", {}).get("address") or "").lower()
    if asset_addr not in usdc_set: continue
    from_addr = (t.get("from") or "").lower()
    if not from_addr: continue
    val = float(t.get("value") or 0)
    counterparties.setdefault(from_addr, {"sent": 0, "recv": 0, "first_block": t.get("blockNum")})
    counterparties[from_addr]["recv"] += val

if not counterparties:
    print()
    print(">>> No USDC transfers between your EOA and any other address.")
    print(">>> You must have deposited USDC straight to the Safe (bridge / CEX → Safe).")
    print(">>> Open polymarket.com, log in, click profile icon top-right — copy the address.")
    print(">>> Then run:  py diag_geoblock.py <safe_address>")
    sys.exit(3)

print()
print(f"USDC counterparties of your EOA:")
for addr, info in sorted(counterparties.items(), key=lambda x: -(x[1]['sent']+x[1]['recv'])):
    print(f"  {addr}   sent ${info['sent']:.2f}  recv ${info['recv']:.2f}  (first seen block {info['first_block']})")

# pick the biggest counterparty as the likely Safe
best = max(counterparties.items(), key=lambda x: x[1]['sent'] + x[1]['recv'])
print()
print(f">>> Most likely Safe: {best[0]}")
print(f">>> Run:  py diag_geoblock.py {best[0]}")
