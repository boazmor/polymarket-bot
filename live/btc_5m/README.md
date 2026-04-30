# live/btc_5m/

Live trading bot for Polymarket's Bitcoin 5-minute markets.

## Files

- `LIVE_BTC_5M_V1.py` — the bot itself (complete, standalone, runnable file).
- `.env` — secrets for THIS bot only (private key, address, RPC URL). **Gitignored. Never commit.**
- (when running) outputs in a `data/` subfolder — CSVs of trades, P&L, errors. Gitignored.

## .env shape

```
WALLET_PRIVATE_KEY=0x...        the bot's wallet private key (64 hex chars after 0x)
WALLET_ADDRESS=0x...            the bot's wallet address (40 hex chars after 0x)
POLYGON_RPC_URL=https://...     Polygon RPC (Alchemy / Infura / Quicknode)
```

Older keys from research code (e.g. `MY_PRIVATE_KEY`, `MY_ADDRESS`) are kept as-is during transition; the live bot reads whichever exists.

## Running

**Local dry-run (decisions only, no real orders):**
```
cd live\btc_5m
python LIVE_BTC_5M_V1.py --dry-run
```

**Live on the Hetzner server:**
```
scp .env LIVE_BTC_5M_V1.py root@178.104.134.228:/root/data_live_btc_5m_v1/
ssh hetzner "cd /root/data_live_btc_5m_v1 && python3 LIVE_BTC_5M_V1.py"
```

## Safety limits (V1)

- `$1` per trade
- max `$20` daily loss → bot stops automatically
- max `$100` total wallet exposure → bot refuses to trade if wallet > $100

These are enforced in code, not just in policy.
