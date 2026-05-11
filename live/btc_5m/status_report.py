#!/usr/bin/env python3
"""One-shot status report — exactly what you have right now across both platforms."""
import sys, json
sys.path.insert(0, "/root")
from predict_trader import PredictTrader

def load_env(path):
    out = {}
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, _, v = ln.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out

env = load_env("/root/live/btc_5m/.env")
t = PredictTrader(env["PREDICT_API_KEY"], env["MY_PRIVATE_KEY"])

print("=" * 70)
print("PREDICT.FUN POSITIONS")
print("=" * 70)
pos_list = t.get_positions()
total_value = 0
total_pnl = 0
for p in pos_list:
    mid = p["market"]["id"]
    slug = p["market"]["categorySlug"]
    amount = int(p["amount"]) / 1e18
    avg = p.get("averageBuyPriceUsd", "?")
    pnl = float(p.get("pnlUsd") or 0)
    val = float(p.get("valueUsd") or 0)
    outcome = p["outcome"]["name"]
    status = p["outcome"].get("status") or "OPEN"
    total_value += val
    total_pnl += pnl
    print(f"  m{mid}  outcome={outcome}  status={status}")
    print(f"    slug:        {slug}")
    print(f"    shares:      {amount:.4f}")
    print(f"    avg buy:     ${avg}")
    print(f"    value now:   ${val:.4f}")
    print(f"    PnL:         ${pnl:+.4f}")
    print()

print(f"  TOTAL value: ${total_value:.4f}")
print(f"  TOTAL pnl:   ${total_pnl:+.4f}")

print("\n" + "=" * 70)
print("PREDICT.FUN OPEN ORDERS")
print("=" * 70)
open_orders = t.get_orders()
active = [o for o in open_orders if (o.get("amountFilled") or "0") != o.get("amount")]
for o in active[:10]:
    print(f"  id={o.get('id')}  market={o.get('marketId')}  "
          f"amount={int(o.get('amount','0'))/1e18:.2f}  "
          f"filled={int(o.get('amountFilled','0'))/1e18:.2f}")

print("\n" + "=" * 70)
print("ON-CHAIN MY_EOA on BNB CHAIN")
print("=" * 70)
from web3 import Web3
from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI
w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
a = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
usdt = w3.eth.contract(address=Web3.to_checksum_address(a.USDT), abi=ERC20_ABI)
addr = "0x73a6dC847cE7B672F98d14e9F239d97a2C9FdF46"
print(f"  address: {addr}")
print(f"  BNB:     {w3.eth.get_balance(addr)/1e18:.6f}")
print(f"  USDT:    {usdt.functions.balanceOf(addr).call()/1e18:.4f}")

print("\n" + "=" * 70)
print("POLYMARKET (Polygon)")
print("=" * 70)
import os
from dotenv import load_dotenv
load_dotenv("/root/live/btc_5m/.env", override=True)
try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    cli = ClobClient(
        host="https://clob.polymarket.com",
        key=os.environ["MY_PRIVATE_KEY"],
        chain_id=137,
        signature_type=2,
        funder="0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B",
    )
    cli.set_api_creds(cli.create_or_derive_api_key())
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
    resp = cli.get_balance_allowance(params)
    print(f"  Safe address: 0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B")
    print(f"  USDC balance: ${int(resp['balance'])/1e6:.4f}")
except Exception as e:
    print(f"  poly query failed: {e}")
