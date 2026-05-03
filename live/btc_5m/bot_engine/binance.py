# -*- coding: utf-8 -*-
"""
binance.py — Binance WebSocket price fetcher.

Multi-coin variant: each instance subscribes to ONE symbol (btcusdt, ethusdt,
solusdt, ...). The master controller spawns one BinanceEngine per coin.

Same boundary-close bookkeeping as the original BTC engine — used by the bot
to anchor each 5-min market's "open" price.
"""
import asyncio
import json
import ssl
import time
from typing import Dict, Optional

import websockets


_BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


class BinanceEngine:
    """One WebSocket client per Binance trading pair.

    Args:
        symbol: lowercase pair symbol e.g. "btcusdt", "ethusdt", "solusdt".
                The actual market the strategy watches is determined by the
                Polymarket slug; this engine just streams the matching Binance
                trade feed so the strategy has a real-time reference price.
    """

    def __init__(self, symbol: str = "btcusdt") -> None:
        self.symbol = symbol.lower().strip()
        self.ws_url = f"{_BINANCE_WS_BASE}/{self.symbol}@trade"
        self.price: Optional[float] = None
        self.updated_at: float = 0.0
        self.status: str = "starting"
        self.last_trade_ts_ms: Optional[int] = None
        self._current_bucket_start: Optional[int] = None
        self._last_trade_in_bucket: Optional[float] = None
        self.boundary_close_prices: Dict[int, float] = {}
        self._stop = False

    def snapshot(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "updated_at": self.updated_at,
            "status": self.status,
            "last_trade_ts_ms": self.last_trade_ts_ms,
            "current_bucket_start": self._current_bucket_start,
            "last_trade_in_bucket": self._last_trade_in_bucket,
        }

    def close_for_boundary(self, boundary_epoch: Optional[int]) -> Optional[float]:
        """Return the last trade price seen in the 5-min bucket whose epoch
        boundary is `boundary_epoch`. Used to capture each market's open price.
        """
        if boundary_epoch is None:
            return None
        try:
            return self.boundary_close_prices.get(int(boundary_epoch))
        except Exception:
            return None

    async def run(self) -> None:
        ssl_ctx = ssl.create_default_context()
        while not self._stop:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ssl=ssl_ctx,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                    close_timeout=5,
                ) as ws:
                    self.status = "live"
                    while not self._stop:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        px = msg.get("p") or msg.get("price")
                        if px is not None:
                            trade_px = float(px)
                            trade_ts_ms = int(msg.get("T") or msg.get("E") or int(time.time() * 1000))
                            bucket_start = (trade_ts_ms // 1000 // 300) * 300
                            if self._current_bucket_start is None:
                                self._current_bucket_start = bucket_start
                            elif bucket_start != self._current_bucket_start:
                                if self._last_trade_in_bucket is not None:
                                    self.boundary_close_prices[self._current_bucket_start + 300] = float(self._last_trade_in_bucket)
                                    if len(self.boundary_close_prices) > 2000:
                                        oldest = sorted(self.boundary_close_prices.keys())[:-1000]
                                        for k in oldest:
                                            self.boundary_close_prices.pop(k, None)
                                self._current_bucket_start = bucket_start
                            self._last_trade_in_bucket = trade_px
                            self.last_trade_ts_ms = trade_ts_ms
                            self.price = trade_px
                            self.updated_at = time.time()
            except asyncio.TimeoutError:
                self.status = "timeout"
            except Exception:
                self.status = "reconnecting"
                await asyncio.sleep(2)

    def stop(self) -> None:
        self._stop = True
