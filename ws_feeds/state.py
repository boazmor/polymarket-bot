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
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    no_best_ask: float = 0.0
    no_ask_depth_usd: float = 0.0
    market_id: str = ""
    slug: str = ""
    ts_ms: int = 0                 # alias for last_update_ms, kept for compat
    last_update_ms: int = 0        # local receive time of last message
    server_ts_ms: int = 0          # platform-stamped emit time of last message
    last_transit_ms: int = 0       # last_update_ms - server_ts_ms
    connected: bool = False
    error_count: int = 0


class SharedState:
    """Thread-safe container for all platforms' orderbook state."""

    def __init__(self):
        self._data = {
            "poly": PlatformBook(),
            "predict": PlatformBook(),
            "lim": PlatformBook(),
        }
        self._lock = threading.RLock()
        # Event-driven wake-up: WS threads call self._wake_event.set() on
        # every economically-meaningful top-of-book change. Main bot waits
        # on the same event so it evaluates within microseconds of arrival
        # instead of waiting for the next polling tick.
        self._wake_event = threading.Event()

    @property
    def wake_event(self):
        """Threading.Event the main bot waits on. set() is idempotent so
        many WS updates in flight just collapse into one wake; the bot
        clears it after each evaluation pass."""
        return self._wake_event

    def update(self, platform: str, **kwargs):
        """Bulk-update fields. Called by WS client threads.
        Detects economically-meaningful changes (best price or top-of-book
        depth) and signals the wake event so the main bot wakes up
        immediately for evaluation."""
        meaningful_change = False
        with self._lock:
            book = self._data[platform]
            old_ask = book.best_ask
            old_bid = book.best_bid
            old_ask_depth = book.ask_depth_usd
            old_no_ask = book.no_best_ask

            for k, v in kwargs.items():
                if hasattr(book, k):
                    setattr(book, k, v)
            now_ms = int(time.time() * 1000)
            book.last_update_ms = now_ms
            book.ts_ms = now_ms
            if 0 < book.server_ts_ms and (now_ms - book.server_ts_ms) < 10000:
                book.last_transit_ms = now_ms - book.server_ts_ms

            if (book.best_ask != old_ask or
                book.best_bid != old_bid or
                book.ask_depth_usd != old_ask_depth or
                book.no_best_ask != old_no_ask):
                meaningful_change = True

        if meaningful_change:
            self._wake_event.set()

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

    def is_fresh(self, platform: str,
                 heartbeat_max_ms: int = 60000,
                 transit_max_ms: int = 300) -> bool:
        """Returns True iff: WS connected AND a recent heartbeat exists AND
        the most recent message's transit time (server-stamped to local) was
        within `transit_max_ms`.

        Quiet markets are TRUSTED: a silent period just means the orderbook
        did not change, NOT that data is stale. The heartbeat window only
        catches outright disconnects.
        """
        with self._lock:
            book = self._data[platform]
            if not book.connected:
                return False
            now_ms = int(time.time() * 1000)
            if now_ms - book.last_update_ms > heartbeat_max_ms:
                return False
            if book.last_transit_ms > transit_max_ms:
                return False
            return True

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
