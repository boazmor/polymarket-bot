"""Shared in-memory orderbook state for the 3 platforms.

Replaces the file-polling architecture (latest.json -> tail_last_row) with a
thread-safe dict updated directly by each platform's WebSocket client.

The main bot reads via SharedState.get(platform) which returns a copy to
prevent torn reads. is_fresh(platform) gates trading on connection health
and quote age.
"""

import copy
import threading
import time
from dataclasses import dataclass


@dataclass
class PlatformBook:
    """Top-of-book snapshot for one platform's BTC market."""
    best_bid: float = 0.0          # price someone is willing to BUY UP at
    best_ask: float = 0.0          # price someone is willing to SELL UP at
    bid_depth_usd: float = 0.0     # USD depth at best bid level
    ask_depth_usd: float = 0.0     # USD depth at best ask level
    no_best_ask: float = 0.0       # implied NO/DOWN ask = 1 - best_bid
    no_ask_depth_usd: float = 0.0  # USD depth at NO ask (= bid_depth_usd)
    market_id: str = ""            # platform-specific market identifier
    slug: str = ""                 # human-readable slug
    ts_ms: int = 0                 # exchange-provided timestamp if available
    last_update_ms: int = 0        # local receive time (for zombie detection)
    connected: bool = False        # True only when WS is live AND data flowing
    error_count: int = 0           # cumulative WS errors (for diagnostics)


class SharedState:
    """Thread-safe container for all platforms' orderbook state."""

    def __init__(self):
        self._data = {
            "poly": PlatformBook(),
            "predict": PlatformBook(),
            "lim": PlatformBook(),
        }
        self._lock = threading.RLock()

    def update(self, platform: str, **kwargs):
        """Bulk-update fields. Called by WS client threads."""
        with self._lock:
            book = self._data[platform]
            for k, v in kwargs.items():
                if hasattr(book, k):
                    setattr(book, k, v)
            # last_update_ms is always set on every update so zombie detection
            # works regardless of which fields the caller passes.
            book.last_update_ms = int(time.time() * 1000)

    def get(self, platform: str) -> PlatformBook:
        """Return a COPY so the caller's reads aren't disturbed by a
        concurrent WS update mid-iteration."""
        with self._lock:
            return copy.copy(self._data[platform])

    def mark_disconnected(self, platform: str):
        """Mark stale during reconnect so the bot stops trading on that
        platform until data flows again. Zero out prices to avoid `ghost`
        liquidity reads."""
        with self._lock:
            self._data[platform].connected = False
            self._data[platform].best_bid = 0.0
            self._data[platform].best_ask = 0.0
            self._data[platform].bid_depth_usd = 0.0
            self._data[platform].ask_depth_usd = 0.0
            self._data[platform].error_count += 1

    def is_fresh(self, platform: str, max_age_ms: int = 80) -> bool:
        """Returns True only if WS is connected AND last update <= max_age_ms.

        Use this BEFORE every trading decision to reject stale opportunities.
        """
        with self._lock:
            book = self._data[platform]
            if not book.connected:
                return False
            now_ms = int(time.time() * 1000)
            return (now_ms - book.last_update_ms) <= max_age_ms

    def all_connected(self) -> bool:
        """True iff all three platforms have live WS connections."""
        with self._lock:
            return all(b.connected for b in self._data.values())

    def snapshot(self) -> dict:
        """Returns a flat dict of all platform states, for logging/debugging."""
        with self._lock:
            now_ms = int(time.time() * 1000)
            return {
                plat: {
                    "best_bid": b.best_bid,
                    "best_ask": b.best_ask,
                    "bid_depth_usd": b.bid_depth_usd,
                    "ask_depth_usd": b.ask_depth_usd,
                    "age_ms": (now_ms - b.last_update_ms) if b.last_update_ms else -1,
                    "connected": b.connected,
                    "error_count": b.error_count,
                }
                for plat, b in self._data.items()
            }


# Module-level singleton for the running bot
STATE = SharedState()
