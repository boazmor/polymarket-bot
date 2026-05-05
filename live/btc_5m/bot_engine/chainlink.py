# -*- coding: utf-8 -*-
"""
chainlink.py — Polymarket Chainlink RTDS WebSocket price subscriber.

Polymarket uses the Chainlink RTDS feed to determine the priceToBeat (target)
of each new 5-minute market. The price at the moment the market opens IS the
target. Subscribing here gives BRM authoritative target capture without
needing Playwright or Polymarket page scraping.

Key facts (verified from MULTI_COIN_RECORDER on the server):
  * Endpoint: wss://ws-live-data.polymarket.com
  * Topic:    crypto_prices_chainlink
  * Filter:   symbol={btc,eth,sol,xrp,doge,bnb,hype}/usd
  * Latency:  ~1.2s p50 (per project_chainlink_latency_finding.md). Acceptable
              for target capture (one-shot per market) but NOT for distance
              calculation (where Binance still wins with sub-100ms latency).

The BinanceEngine continues to drive distance/decision making. ChainlinkClient
exists ONLY to fix target_price for each new market.
"""
import asyncio
import json
import ssl
import time
from typing import Optional

import websockets


_CHAINLINK_WS = "wss://ws-live-data.polymarket.com"

COIN_TO_CHAINLINK_SYMBOL = {
    "BTC":  "btc/usd",
    "ETH":  "eth/usd",
    "SOL":  "sol/usd",
    "XRP":  "xrp/usd",
    "DOGE": "doge/usd",
    "BNB":  "bnb/usd",
    "HYPE": "hype/usd",
}


class ChainlinkClient:
    """One WebSocket subscription per coin to Polymarket's Chainlink price feed.

    Public state:
        price          — latest tick value from Chainlink (USD)
        updated_at     — unix-time of latest tick
        status         — "starting" / "connecting" / "live" / "reconnecting"
        ticks_total    — total ticks received since process start
    """

    def __init__(self, coin: str) -> None:
        coin_upper = coin.upper().strip()
        symbol = COIN_TO_CHAINLINK_SYMBOL.get(coin_upper)
        if symbol is None:
            raise ValueError(f"no chainlink symbol mapping for coin={coin_upper}")
        self.coin = coin_upper
        self.symbol = symbol
        self.price: Optional[float] = None
        self.updated_at: float = 0.0
        self.status: str = "starting"
        self.ticks_total: int = 0
        self.reconnects: int = 0
        self.last_error: str = ""
        self._stop = False

    def snapshot(self) -> dict:
        return {
            "coin": self.coin,
            "symbol": self.symbol,
            "price": self.price,
            "updated_at": self.updated_at,
            "status": self.status,
            "ticks_total": self.ticks_total,
            "reconnects": self.reconnects,
        }

    async def run(self) -> None:
        ssl_ctx = ssl.create_default_context()
        sub_msg = {
            "action": "subscribe",
            "subscriptions": [{
                "topic": "crypto_prices_chainlink",
                "type": "*",
                "filters": '{"symbol":"' + self.symbol + '"}',
            }],
        }
        while not self._stop:
            try:
                self.status = "connecting"
                async with websockets.connect(
                    _CHAINLINK_WS,
                    ssl=ssl_ctx,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                    close_timeout=5,
                ) as ws:
                    await ws.send(json.dumps(sub_msg))
                    self.status = "live"
                    async for raw in ws:
                        if self._stop:
                            return
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(msg, dict) or msg.get("topic") != "crypto_prices_chainlink":
                            continue
                        payload = msg.get("payload") or {}
                        if not isinstance(payload, dict):
                            continue
                        if payload.get("symbol") != self.symbol:
                            continue
                        try:
                            val = float(payload.get("value"))
                        except (TypeError, ValueError):
                            continue
                        self.price = val
                        self.updated_at = time.time()
                        self.ticks_total += 1
            except asyncio.TimeoutError:
                self.status = "timeout"
                self.reconnects += 1
                await asyncio.sleep(2)
            except Exception as e:
                self.status = "reconnecting"
                self.last_error = f"{type(e).__name__}:{e}"
                self.reconnects += 1
                await asyncio.sleep(2)

    def stop(self) -> None:
        self._stop = True
