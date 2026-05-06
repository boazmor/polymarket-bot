#!/usr/bin/env python3
"""
PREDICT_RECORDER.py — Predict.fun BTC 15-minute market recorder.

Subscribes to public WebSocket (no auth needed) at wss://ws.predict.fun/ws
and records orderbook snapshots per second for the currently-active BTC
15-minute market.

The bot discovers the active BTC market by listening to predictTrades/*
and identifying markets with the highest activity matching BTC volume.
For now, this version takes the marketId on the command line; we'll
auto-discover in v2.

Output: data_predict_btc_15m/
  combined_per_second.csv — tick-by-tick orderbook snapshot
  events.csv              — subscribe events, market changes, errors

The orderbook format from Predict.fun:
  bids: [[price, size], ...]   — orders to BUY YES
  asks: [[price, size], ...]   — orders to SELL YES = orders to BUY NO at (1-price)

So:
  yes_ask = bids best price's complement? NO — yes_ask = asks best price (we buy YES)
  yes_bid = bids best price (someone willing to buy YES from us)
  no_ask  = 1 - yes_bid (to buy NO we sell YES at bid)
  no_bid  = 1 - yes_ask

Run:
  python3 PREDICT_RECORDER.py --market-id 285689
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
from typing import Optional

WS_URL = "wss://ws.predict.fun/ws"
DATA_DIR = "/root/data_predict_btc_15m"
POLL_INTERVAL_SEC = 1.0

SHOULD_STOP = False
LATEST_OB = None  # latest orderbook from WS
LAST_OB_TS = 0.0


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


async def ws_listener(market_id, csvs):
    """Connect to WS and keep LATEST_OB updated."""
    global LATEST_OB, LAST_OB_TS
    while not SHOULD_STOP:
        try:
            async with websockets.connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                # Subscribe to orderbook for the specific market
                req = {"method": "subscribe", "requestId": 1, "params": [f"predictOrderbook/{market_id}"]}
                await ws.send(json.dumps(req))
                csvs.event("WS_CONNECT", f"subscribed to predictOrderbook/{market_id}")
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
                        LATEST_OB = d.get("data", {})
                        LAST_OB_TS = time.time()
                    elif t == "R" and not d.get("success"):
                        csvs.event("SUB_ERROR", json.dumps(d.get("error", {})))
        except Exception as e:
            csvs.event("WS_DISCONNECT", f"{type(e).__name__}: {e}")
            await asyncio.sleep(2)


def best_level(side):
    """side = list of [price, size]. Returns (best_price, best_size, total_usd_at_best)."""
    if not side:
        return None, None, 0.0
    try:
        first = side[0]
        p = float(first[0]); s = float(first[1])
        return p, s, p * s
    except Exception:
        return None, None, 0.0


def total_usd(side):
    t = 0.0
    for lvl in side:
        try:
            t += float(lvl[0]) * float(lvl[1])
        except Exception:
            pass
    return t


def total_no_usd(bids):
    """For NO depth: each YES bid at price p with size s represents NO sellable at (1-p)*s USD."""
    t = 0.0
    for lvl in bids:
        try:
            p = float(lvl[0]); s = float(lvl[1])
            t += (1.0 - p) * s
        except Exception:
            pass
    return t


async def snapshot_loop(csvs):
    """Every second, write current orderbook snapshot."""
    while not SHOULD_STOP:
        if LATEST_OB:
            ob = LATEST_OB
            bids = ob.get("bids", []) or []
            asks = ob.get("asks", []) or []
            yes_bid_p, yes_bid_sz, _ = best_level(bids)
            yes_ask_p, yes_ask_sz, _ = best_level(asks)
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
                f"{time.time() - LAST_OB_TS:.2f}",
            ])
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def main_async(market_id):
    csvs = CsvStore(DATA_DIR)
    csvs.event("START", f"market_id={market_id}")
    print(f"[{now_local()}] starting recorder for market_id={market_id}")
    await asyncio.gather(
        ws_listener(market_id, csvs),
        snapshot_loop(csvs),
    )
    csvs.event("STOP", "")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market-id", type=int, required=True, help="Predict.fun marketId")
    args = p.parse_args()
    setup_signals()
    asyncio.run(main_async(args.market_id))


if __name__ == "__main__":
    main()
