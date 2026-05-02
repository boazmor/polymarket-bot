# -*- coding: utf-8 -*-
"""probe_domain.py — query the V2 exchange contract on Polygon for its
EIP-712 domain values. Uses eip712Domain() (EIP-5267 standard view fn).
Selector: 0x84b0196e.

Result tells us the exact name/version/chainId/verifyingContract that the
contract expects for order signatures."""

import os, sys, json
from pathlib import Path
import requests

ENV_PATHS = [Path(__file__).parent / ".env", Path("/root/.env")]
rpc_url = None
for p in ENV_PATHS:
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line: continue
                k, v = line.strip().split("=", 1)
                if k == "POLYGON_RPC_URL":
                    rpc_url = v
if not rpc_url:
    print("FAIL: POLYGON_RPC_URL not in .env"); sys.exit(1)

CONTRACTS = [
    ("CTF Exchange V2 (new)",    "0xE111180000d2663C0091e4f400237545B87B996B"),
    ("NegRisk Exchange V2 (new)","0xe2222d279d744050d28e00520010520000310F59"),
    ("CTF Exchange V1 (old)",    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk Exchange V1 (old)","0xC5d563A36AE78145C45a50134d48A1215220f80a"),
]

# eip712Domain() = 0x84b0196e
# Returns (bytes1 fields, string name, string version, uint256 chainId,
#          address verifyingContract, bytes32 salt, uint256[] extensions)

def call(addr, selector):
    payload = {
        "jsonrpc":"2.0","id":1,"method":"eth_call",
        "params":[{"to": addr, "data": selector}, "latest"]
    }
    r = requests.post(rpc_url, json=payload, timeout=15).json()
    return r.get("result"), r.get("error")

def decode_string(data, offset):
    # data is hex without 0x; offset is in bytes (decode the dynamic part)
    str_offset = offset * 2
    length = int(data[str_offset:str_offset+64], 16) * 2
    s = data[str_offset+64 : str_offset+64+length]
    try:
        return bytes.fromhex(s).decode("utf-8")
    except Exception:
        return f"<hex:{s}>"

for label, addr in CONTRACTS:
    print(f"\n=== {label} : {addr}")
    result, err = call(addr, "0x84b0196e")
    if err:
        print(f"  eip712Domain() error: {err}")
        # try DOMAIN_SEPARATOR() as fallback (selector 0x3644e515)
        result2, err2 = call(addr, "0x3644e515")
        if result2:
            print(f"  DOMAIN_SEPARATOR(): {result2}")
        continue
    if not result or result == "0x":
        print(f"  no eip712Domain() — contract doesn't implement EIP-5267")
        continue
    raw = result[2:]  # strip 0x
    # decode: 7 head fields, each 32 bytes
    # field 0: bytes1 fields (padded)
    fields_byte = raw[0:64]
    # field 1: offset to name
    name_off = int(raw[64:128], 16)
    # field 2: offset to version
    ver_off = int(raw[128:192], 16)
    # field 3: chainId
    chain_id = int(raw[192:256], 16)
    # field 4: verifyingContract
    vc = "0x" + raw[256+24:320]
    # field 5: salt
    salt = "0x" + raw[320:384]
    # field 6: offset to extensions
    ext_off = int(raw[384:448], 16)

    name = decode_string(raw, name_off)
    version = decode_string(raw, ver_off)

    print(f"  name             : {name!r}")
    print(f"  version          : {version!r}")
    print(f"  chainId          : {chain_id}")
    print(f"  verifyingContract: {vc}")
    print(f"  salt             : {salt}")
