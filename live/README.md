# live/

Live trading bots — one folder per coin. Each bot is a **complete, standalone Python file** with its own parameters. No shared code between coins by design — copy-paste-modify, don't import.

## Structure

```
live/
├── btc_5m/          first live bot (Bitcoin, 5-minute markets)
├── eth_5m/          (future) Ethereum, 5-minute markets
└── ...              one folder per coin/timeframe
```

## To add a new coin (e.g. ETH)

1. Copy `live/btc_5m/LIVE_BTC_5M_V1.py` → `live/eth_5m/LIVE_ETH_5M_V1.py`.
2. Update inside the file: Binance ticker (BTCUSDT → ETHUSDT), Polymarket slug pattern, numeric parameters (price thresholds, distance threshold).
3. Copy the `.env` if the same wallet is used, or create a new `.env` with a separate wallet.
4. Test thoroughly in dry-run mode before going live.

## Safety

- Each `.env` is gitignored. Never commit real keys.
- Wallet should hold only the trading capital you can afford to lose (e.g. $50–$100 to start).
- Always start a new bot in dry-run mode for at least one full hour before enabling real orders.
