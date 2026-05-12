"""One-time USDC approval for Limitless CTF Exchange on Base.

Sets allowance(owner=MY_ADDRESS, spender=venue.exchange) = max_uint256 so the
SDK can pull USDC when filling FAK BUY orders. ETH gas ~$0.05 at typical Base
prices.

Re-run is a no-op if allowance is already non-zero (skips and exits 0).
"""

import os
import sys
from eth_account import Account
from web3 import Web3


def load_env(path):
    out = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main():
    env = load_env("/root/live/btc_5m/.env")
    pk = env["MY_PRIVATE_KEY"]
    me = env["MY_ADDRESS"]

    rpc = env.get("BASE_RPC_URL", "https://mainnet.base.org")
    w3 = Web3(Web3.HTTPProvider(rpc))
    assert w3.is_connected() and w3.eth.chain_id == 8453, "Base RPC not reachable"

    USDC = w3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
    EXCHANGE = w3.to_checksum_address("0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5")
    OWNER = w3.to_checksum_address(me)

    abi = [
        {"constant": True, "inputs": [{"name": "owner", "type": "address"},
                                      {"name": "spender", "type": "address"}],
         "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
         "stateMutability": "view", "type": "function"},
        {"constant": False, "inputs": [{"name": "spender", "type": "address"},
                                       {"name": "value", "type": "uint256"}],
         "name": "approve", "outputs": [{"name": "", "type": "bool"}],
         "stateMutability": "nonpayable", "type": "function"},
    ]
    usdc = w3.eth.contract(address=USDC, abi=abi)

    cur = usdc.functions.allowance(OWNER, EXCHANGE).call()
    print(f"current allowance: {cur/1e6:.4f} USDC")
    if cur > 10**30:
        print("already unlimited - skipping")
        return 0

    acct = Account.from_key(pk)
    assert acct.address.lower() == OWNER.lower(), "private key does not match MY_ADDRESS"

    max_uint = 2**256 - 1
    nonce = w3.eth.get_transaction_count(OWNER)
    gas_price = w3.eth.gas_price
    print(f"nonce={nonce} gas_price={gas_price/1e9:.4f} gwei")

    tx = usdc.functions.approve(EXCHANGE, max_uint).build_transaction({
        "from": OWNER,
        "nonce": nonce,
        "chainId": 8453,
        "gasPrice": gas_price,
        "gas": 80_000,
    })
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"tx submitted: {h.hex()}")
    print("waiting receipt...")
    r = w3.eth.wait_for_transaction_receipt(h, timeout=120)
    print(f"status={r.status} block={r.blockNumber} gas_used={r.gasUsed}")
    if r.status != 1:
        print("FAILED")
        return 2

    new_allow = usdc.functions.allowance(OWNER, EXCHANGE).call()
    print(f"new allowance: {new_allow/1e6:.4f} USDC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
