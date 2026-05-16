#!/usr/bin/env python3
"""Snapshot all 3 platform balances + list open positions. Read-only."""

import os
import sys
import json
import urllib.request

sys.path.insert(0, "/root")

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"


def predict_check():
    print("\n=== PREDICT.FUN (BNB chain) ===")
    try:
        from predict_trader import PredictTrader
        from web3 import Web3
        from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI
        pk = os.environ["MY_PRIVATE_KEY"]
        api_key = os.environ["PREDICT_API_KEY"]
        pt = PredictTrader(api_key, pk, log_path="/tmp/wallet_check.log")
        addrs = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
        w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
        usdt = w3.eth.contract(address=Web3.to_checksum_address(addrs.USDT), abi=ERC20_ABI)
        eoa = Web3.to_checksum_address(pt.address)
        usdt_balance = usdt.functions.balanceOf(eoa).call() / 1e18
        print(f"  EOA address: {pt.address}")
        print(f"  USDT (free): ${usdt_balance:.4f}")
        positions = pt.get_positions()
        pos_value = sum(float(p.get("valueUsd") or 0) for p in positions)
        print(f"  Open positions: {len(positions)}, value ${pos_value:.4f}")
        for p in positions[:20]:
            print(f"    market={p.get('marketId')} outcome={p.get('outcome')} "
                  f"shares={p.get('size')} valueUsd={p.get('valueUsd')}")
        print(f"  TOTAL Predict: ${usdt_balance + pos_value:.4f}")
        return usdt_balance + pos_value
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}")
        return None


def poly_check():
    print("\n=== POLYMARKET (Polygon chain) ===")
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        pk = os.environ["MY_PRIVATE_KEY"]
        c = ClobClient(host="https://clob.polymarket.com",
                       key=pk, chain_id=137,
                       signature_type=2, funder=POLY_SAFE)
        c.set_api_creds(c.create_or_derive_api_key())
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
        resp = c.get_balance_allowance(params)
        usdc = int(resp["balance"]) / 1e6
        print(f"  Safe address: {POLY_SAFE}")
        print(f"  USDC (free): ${usdc:.4f}")
        url = f"https://data-api.polymarket.com/positions?user={POLY_SAFE}&limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        pos_value = 0.0
        if isinstance(data, list):
            print(f"  Open positions: {len(data)}")
            for p in data[:30]:
                cv = p.get("currentValue") or p.get("current_value") or 0
                try:
                    pos_value += float(cv)
                except Exception:
                    pass
                title = p.get("title") or p.get("event") or p.get("market") or ""
                print(f"    {title[:60]:<60}  "
                      f"shares={p.get('size','?')}  "
                      f"avgPrice={p.get('avgPrice','?')}  "
                      f"currentValue=${cv}")
        print(f"  Positions total value: ${pos_value:.4f}")
        print(f"  TOTAL Polymarket: ${usdc + pos_value:.4f}")
        return usdc + pos_value
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}")
        return None


def limitless_check():
    print("\n=== LIMITLESS (Base chain) ===")
    try:
        from limitless_trader import LimitlessTrader
        from web3 import Web3
        lim_key = os.environ["LIMITLESS_API_KEY"]
        lim_sec = os.environ["LIMITLESS_API_SECRET"]
        pk = os.environ["MY_PRIVATE_KEY"]
        lt = LimitlessTrader(lim_key, lim_sec, pk, log_path="/tmp/wallet_check_lim.log")
        rpc = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
        w3 = Web3(Web3.HTTPProvider(rpc))
        USDC = w3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
        OWNER = w3.to_checksum_address(lt.address)
        abi = [{"constant": True, "inputs": [{"name": "a", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view", "type": "function"}]
        c = w3.eth.contract(address=USDC, abi=abi)
        usdc = c.functions.balanceOf(OWNER).call() / 1e6
        print(f"  EOA address: {lt.address}")
        print(f"  USDC (free): ${usdc:.4f}")
        try:
            positions = lt.get_positions() if hasattr(lt, "get_positions") else []
            if positions:
                print(f"  Open positions: {len(positions)}")
                for p in positions[:20]:
                    print(f"    {p}")
            else:
                print(f"  Open positions: 0 (or get_positions unsupported)")
        except Exception as e2:
            print(f"  positions check failed: {type(e2).__name__}: {e2}")
        print(f"  TOTAL Limitless: ${usdc:.4f}")
        return usdc
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    print("=" * 70)
    print("WALLET SNAPSHOT — 3 PLATFORMS")
    print("=" * 70)
    a = predict_check()
    b = poly_check()
    c = limitless_check()
    print("\n" + "=" * 70)
    if a is not None and b is not None and c is not None:
        print(f"GRAND TOTAL: ${a + b + c:.4f}")
    print("=" * 70)
