#!/usr/bin/env python3
"""PnL monitoring loop — prints to /root/pnl_status.txt every 30 seconds.

Tracks:
- Predict.fun: on-chain USDT + open positions value
- Polymarket: USDC balance + open positions value
- Cumulative PnL vs starting baseline
- Hourly delta (last 60min)
- Daily delta (last 24h)

History saved to /root/pnl_history.csv (one row every 30s).
"""

import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root")

# Baselines from session start
BASELINE_PREDICT_USDT = 99.99       # the user's initial Predict deposit
BASELINE_POLY_USDC = 221.87         # what we saw early in session

HISTORY = "/root/pnl_history.csv"
STATUS = "/root/pnl_status.txt"
WORK_LOG = "/root/pnl_loop.log"

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
os.environ.update(env)

# Lazy init clients
from predict_trader import PredictTrader
from web3 import Web3
from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI

w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
bnb_addresses = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
usdt_contract = w3.eth.contract(
    address=Web3.to_checksum_address(bnb_addresses.USDT),
    abi=ERC20_ABI,
)
my_eoa = "0x73a6dC847cE7B672F98d14e9F239d97a2C9FdF46"


def get_predict_state(trader):
    """Return dict with predict balances and positions."""
    try:
        positions = trader.get_positions()
    except Exception as e:
        positions = []

    open_pos_value = 0
    open_pos_count = 0
    closed_pnl = 0
    for p in positions:
        st = p.get("outcome", {}).get("status")
        val = float(p.get("valueUsd") or 0)
        pnl = float(p.get("pnlUsd") or 0)
        if st in (None, "OPEN") and val > 0.05:
            open_pos_value += val
            open_pos_count += 1
        else:
            closed_pnl += pnl

    on_chain_usdt = usdt_contract.functions.balanceOf(my_eoa).call() / 1e18
    on_chain_bnb = w3.eth.get_balance(my_eoa) / 1e18

    return {
        "usdt_onchain": on_chain_usdt,
        "bnb_onchain": on_chain_bnb,
        "open_positions_value": open_pos_value,
        "open_positions_count": open_pos_count,
        "closed_pnl": closed_pnl,
    }


def get_poly_state(client):
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
        resp = client.get_balance_allowance(params)
        usdc = int(resp["balance"]) / 1e6
        return {"usdc": usdc}
    except Exception as e:
        return {"usdc": None, "error": str(e)[:80]}


def render(p, py, hist):
    """Render the status box."""
    now = datetime.now(timezone.utc)
    total_now = p["usdt_onchain"] + p["open_positions_value"] + (py["usdc"] or 0)
    total_baseline = BASELINE_PREDICT_USDT + BASELINE_POLY_USDC
    cum_pnl = total_now - total_baseline

    hr_delta = None
    day_delta = None
    if hist:
        hr_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        for row in hist:
            ts = datetime.fromisoformat(row["ts"])
            t = float(row["total_now"])
            if hr_delta is None and ts >= hr_ago:
                hr_delta = total_now - t
            if day_delta is None and ts >= day_ago:
                day_delta = total_now - t

    out = []
    out.append("=" * 64)
    out.append(f"PnL Status — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    out.append("=" * 64)
    out.append("")
    out.append("Predict.fun (BNB Chain)")
    out.append(f"  USDT on-chain          ${p['usdt_onchain']:>8.2f}")
    out.append(f"  BNB on-chain (gas)      {p['bnb_onchain']:>8.4f}  (~${p['bnb_onchain']*650:.2f})")
    out.append(f"  Open positions value   ${p['open_positions_value']:>8.2f}  ({p['open_positions_count']} pos)")
    sub_p = p['usdt_onchain'] + p['open_positions_value']
    out.append(f"  Predict total          ${sub_p:>8.2f}  (vs baseline ${BASELINE_PREDICT_USDT:.2f} -> {sub_p-BASELINE_PREDICT_USDT:+.2f})")
    out.append("")
    out.append("Polymarket (Polygon)")
    out.append(f"  USDC balance           ${(py['usdc'] or 0):>8.2f}  (vs baseline ${BASELINE_POLY_USDC:.2f} -> {(py['usdc'] or 0)-BASELINE_POLY_USDC:+.2f})")
    out.append("")
    out.append(f"COMBINED TOTAL           ${total_now:>8.2f}")
    out.append(f"  cumulative PnL          {cum_pnl:>+8.2f}")
    out.append(f"  last hour delta         " + (f"{hr_delta:+.2f}" if hr_delta is not None else "n/a"))
    out.append(f"  last 24h delta          " + (f"{day_delta:+.2f}" if day_delta is not None else "n/a"))
    out.append("")
    return "\n".join(out)


def main():
    print(f"[{datetime.now().isoformat()}] starting pnl_loop", file=open(WORK_LOG, "a"))
    trader = PredictTrader(env["PREDICT_API_KEY"], env["MY_PRIVATE_KEY"])
    from py_clob_client_v2.client import ClobClient
    poly_client = ClobClient(
        host="https://clob.polymarket.com",
        key=env["MY_PRIVATE_KEY"],
        chain_id=137,
        signature_type=2,
        funder="0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B",
    )
    poly_client.set_api_creds(poly_client.create_or_derive_api_key())

    history = []
    if os.path.exists(HISTORY):
        with open(HISTORY) as f:
            history = list(csv.DictReader(f))

    while True:
        try:
            p = get_predict_state(trader)
            py = get_poly_state(poly_client)
            txt = render(p, py, history)
            with open(STATUS, "w") as f:
                f.write(txt)
            # Also append to history
            now = datetime.now(timezone.utc).isoformat()
            total = p["usdt_onchain"] + p["open_positions_value"] + (py["usdc"] or 0)
            row = {
                "ts": now,
                "predict_usdt": f"{p['usdt_onchain']:.4f}",
                "predict_positions": f"{p['open_positions_value']:.4f}",
                "poly_usdc": f"{py['usdc'] or 0:.4f}",
                "total_now": f"{total:.4f}",
            }
            new = not os.path.exists(HISTORY)
            with open(HISTORY, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=row.keys())
                if new:
                    w.writeheader()
                w.writerow(row)
            history.append(row)
        except Exception as e:
            with open(WORK_LOG, "a") as f:
                f.write(f"{datetime.now().isoformat()} ERROR: {type(e).__name__}: {e}\n")
        time.sleep(30)


if __name__ == "__main__":
    main()
