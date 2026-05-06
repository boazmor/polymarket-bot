#!/usr/bin/env python3
"""
PREDICT_RECORDER.py — Predict.fun BTC 15-minute market recorder.

Auto-discovers the most active BTC 15-min market by:
  1. Subscribing to predictOrderbook/* (all markets)
  2. Tracking activity per market (orderCount, last update)
  3. Selecting the market with highest activity that matches BTC pattern
  4. Auto-rolling over when current market becomes inactive

Output: data_predict_btc_15m/
  combined_per_second.csv — tick-by-tick orderbook of currently-tracked market
  events.csv              — discovery, rollover, errors
  markets.csv             — log of every market we've recorded

Run:
  python3 PREDICT_RECORDER.py
"""

import argparse
import asyncio
import csv
import json
import os
import signal
import sys
import time
import websockets
from datetime import datetime
from typing import Optional, Dict

WS_URL = "wss://ws.predict.fun/ws"
DATA_DIR = "/root/data_predict_btc_15m"   # default; can be overridden via CLI
POLL_INTERVAL_SEC = 1.0
DISCOVERY_INTERVAL_SEC = 30
INACTIVE_THRESHOLD_SEC = 120  # if no orderbook update for 2 minutes, market closed
MIN_ORDER_COUNT = 5           # minimum orders to consider a market active
RANK = 1                      # 1=pick highest marketId, 2=pick second-highest, etc.

SHOULD_STOP = False

# Per-market state from WS — keyed by marketId
MARKETS: Dict[int, dict] = {}        # marketId -> latest orderbook data
LAST_UPDATE: Dict[int, float] = {}   # marketId -> last update epoch

CURRENT_MARKET_ID: Optional[int] = None
CURRENT_MARKET_SINCE: Optional[float] = None


def now_local():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def now_epoch():
    return int(time.time())


def setup_signals():
    def handler(sig, frame):
        global SHOULD_STOP
        SHOULD_STOP = True
        print(f"[{now_local()}] received signal {sig}, stopping")
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


class CsvStore:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.combined = os.path.join(self.data_dir, "combined_per_second.csv")
        self.events = os.path.join(self.data_dir, "events.csv")
        self.markets = os.path.join(self.data_dir, "markets.csv")
        self._init_if_missing(self.combined, [
            "local_ts", "epoch_sec",
            "market_id",
            "yes_bid", "yes_ask",
            "yes_bid_size", "yes_ask_size",
            "yes_bid_usd", "yes_ask_usd",
            "no_ask_implied", "no_bid_implied",
            "no_ask_usd_buyable", "no_ask_usd_total",
            "spread", "order_count",
            "ws_age_sec",
        ])
        self._init_if_missing(self.events, ["local_ts", "event", "detail"])
        self._init_if_missing(self.markets, ["local_ts", "market_id", "first_seen_ts", "order_count_at_select"])

    @staticmethod
    def _init_if_missing(path, headers):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)

    def event(self, ev, detail=""):
        with open(self.events, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now_local(), ev, detail])

    def append_combined(self, row):
        with open(self.combined, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def market_picked(self, market_id, order_count):
        with open(self.markets, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now_local(), market_id, now_epoch(), order_count])


async def ws_listener(csvs):
    """Connect to WS and keep MARKETS dict updated for ALL active markets."""
    while not SHOULD_STOP:
        try:
            async with websockets.connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                # Subscribe to ALL orderbooks
                req = {"method": "subscribe", "requestId": 1, "params": ["predictOrderbook/*"]}
                await ws.send(json.dumps(req))
                csvs.event("WS_CONNECT", "subscribed to predictOrderbook/*")
                while not SHOULD_STOP:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    t = d.get("type")
                    if t == "M" and "Orderbook" in d.get("topic", ""):
                        data = d.get("data", {})
                        mid = data.get("marketId")
                        if mid:
                            MARKETS[mid] = data
                            LAST_UPDATE[mid] = time.time()
        except Exception as e:
            csvs.event("WS_DISCONNECT", f"{type(e).__name__}: {e}")
            await asyncio.sleep(2)


def select_active_market() -> Optional[int]:
    """Pick the current BTC 15-min market.
    Heuristics for 15-min auto-markets vs long-term accumulator markets:
      - marketId is HIGH (auto-markets get fresh IDs every 15min, currently 280k+)
      - orderCount moderate (5-100, not hundreds)
      - depth moderate (under $50k total — long-term markets accumulate $1M+)
      - prices in 0.05-0.95 range
      - recent updates in the last 60 seconds
    """
    now = time.time()
    candidates = []
    for mid, data in MARKETS.items():
        # Skip low marketIds (long-term markets)
        if mid < 200000:
            continue
        last = LAST_UPDATE.get(mid, 0)
        if now - last > 60:
            continue
        oc = int(data.get("orderCount", 0) or 0)
        if oc < MIN_ORDER_COUNT or oc > 100:
            continue
        bids = data.get("bids", []) or []
        asks = data.get("asks", []) or []
        if not bids or not asks:
            continue
        try:
            yes_bid = float(bids[0][0])
            yes_ask = float(asks[0][0])
            if not (0.05 <= yes_bid <= 0.95 and 0.05 <= yes_ask <= 0.95):
                continue
            total_usd = sum(float(b[0]) * float(b[1]) for b in bids) + \
                        sum(float(a[0]) * float(a[1]) for a in asks)
            if total_usd > 100000:  # over $100k = likely long-term market
                continue
        except Exception:
            continue
        # Score: prefer NEWEST marketId (fresh 15-min market)
        candidates.append((mid, oc, total_usd))

    if not candidates:
        return None
    # Sort by marketId DESCENDING; pick the Nth-ranked one (1-indexed)
    candidates.sort(key=lambda x: -x[0])
    if RANK > len(candidates):
        return None
    return candidates[RANK - 1][0]


async def discovery_loop(csvs):
    """Periodically re-pick the active market. Switch when the current one
    becomes inactive (no orderbook updates for INACTIVE_THRESHOLD_SEC)."""
    global CURRENT_MARKET_ID, CURRENT_MARKET_SINCE
    while not SHOULD_STOP:
        await asyncio.sleep(DISCOVERY_INTERVAL_SEC)
        # Check if current market is still active
        if CURRENT_MARKET_ID is not None:
            last = LAST_UPDATE.get(CURRENT_MARKET_ID, 0)
            if time.time() - last > INACTIVE_THRESHOLD_SEC:
                csvs.event("MARKET_CLOSED", f"market_id={CURRENT_MARKET_ID} no updates for {time.time()-last:.0f}s")
                CURRENT_MARKET_ID = None
        # If no current market or it just closed, pick a new one
        if CURRENT_MARKET_ID is None:
            picked = select_active_market()
            if picked:
                CURRENT_MARKET_ID = picked
                CURRENT_MARKET_SINCE = time.time()
                oc = int(MARKETS.get(picked, {}).get("orderCount", 0) or 0)
                csvs.market_picked(picked, oc)
                csvs.event("MARKET_PICKED", f"market_id={picked} orderCount={oc}")
                print(f"[{now_local()}] picked market {picked} (orderCount={oc})")


def best_level(side):
    if not side:
        return None, None
    try:
        first = side[0]
        return float(first[0]), float(first[1])
    except Exception:
        return None, None


def total_usd(side):
    t = 0.0
    for lvl in side:
        try:
            t += float(lvl[0]) * float(lvl[1])
        except Exception:
            pass
    return t


def total_no_usd(bids):
    t = 0.0
    for lvl in bids:
        try:
            p = float(lvl[0]); s = float(lvl[1])
            t += (1.0 - p) * s
        except Exception:
            pass
    return t


async def snapshot_loop(csvs):
    """Every second, write current orderbook snapshot of CURRENT_MARKET_ID."""
    while not SHOULD_STOP:
        if CURRENT_MARKET_ID is not None and CURRENT_MARKET_ID in MARKETS:
            ob = MARKETS[CURRENT_MARKET_ID]
            bids = ob.get("bids", []) or []
            asks = ob.get("asks", []) or []
            yes_bid_p, yes_bid_sz = best_level(bids)
            yes_ask_p, yes_ask_sz = best_level(asks)
            yes_bid_usd_total = total_usd(bids)
            yes_ask_usd_total = total_usd(asks)
            no_ask_implied = (1.0 - yes_bid_p) if yes_bid_p is not None else None
            no_bid_implied = (1.0 - yes_ask_p) if yes_ask_p is not None else None
            no_ask_usd_buyable = (no_ask_implied * yes_bid_sz) if (no_ask_implied is not None and yes_bid_sz is not None) else 0.0
            no_ask_usd_total_v = total_no_usd(bids)
            spread = (yes_ask_p - yes_bid_p) if (yes_ask_p is not None and yes_bid_p is not None) else None
            csvs.append_combined([
                now_local(), now_epoch(),
                ob.get("marketId", ""),
                f"{yes_bid_p:.4f}" if yes_bid_p is not None else "",
                f"{yes_ask_p:.4f}" if yes_ask_p is not None else "",
                f"{yes_bid_sz:.4f}" if yes_bid_sz is not None else "",
                f"{yes_ask_sz:.4f}" if yes_ask_sz is not None else "",
                f"{yes_bid_usd_total:.4f}",
                f"{yes_ask_usd_total:.4f}",
                f"{no_ask_implied:.4f}" if no_ask_implied is not None else "",
                f"{no_bid_implied:.4f}" if no_bid_implied is not None else "",
                f"{no_ask_usd_buyable:.4f}",
                f"{no_ask_usd_total_v:.4f}",
                f"{spread:.4f}" if spread is not None else "",
                ob.get("orderCount", 0),
                f"{time.time() - LAST_UPDATE.get(CURRENT_MARKET_ID, time.time()):.2f}",
            ])
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def main_async():
    csvs = CsvStore(DATA_DIR)
    csvs.event("START", "auto-discovery mode")
    print(f"[{now_local()}] starting recorder with auto-discovery")
    # Wait briefly for WS to populate before first discovery
    await asyncio.sleep(0)
    await asyncio.gather(
        ws_listener(csvs),
        discovery_loop(csvs),
        snapshot_loop(csvs),
    )
    csvs.event("STOP", "")


def main():
    global DATA_DIR, RANK
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default=DATA_DIR)
    p.add_argument("--rank", type=int, default=1, help="1=highest marketId, 2=second, ...")
    args = p.parse_args()
    DATA_DIR = args.data_dir
    RANK = args.rank
    setup_signals()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
