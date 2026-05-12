#!/usr/bin/env python3
"""LIMITLESS_RECORDER_WS - WebSocket recorder for Limitless Exchange.

Replaces the 1Hz HTTP-poll recorder with a streaming WebSocket recorder so
latest.json updates within ~50ms of every orderbook change instead of up to
1 second behind.

Architecture:
  - WS subscription via limitless-sdk's WebSocketClient (Socket.IO under the hood)
  - Subscribes to 'subscribe_market_prices' for the current BTC window market
  - On each orderbookUpdate event, writes latest.json + appends to CSV
  - HTTP poll runs every 15 sec to detect market rollover (new market every
    5/15/60 min depending on --window) and re-subscribe

Output schema matches LIMITLESS_RECORDER.py exactly so existing bots and CSVs
work without changes:
  best_bid / best_ask + size_usd + shares
  total_bid_usd / total_ask_usd
  no_best_ask / no_best_bid (+ CTF-complement size_usd + shares)
  spread, bid_levels, ask_levels
"""

import argparse
import asyncio
import csv
import json
import os
import re
import signal
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from limitless_sdk.websocket import WebSocketClient, WebSocketConfig


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HEADERS = {"User-Agent": UA}
API = "https://api.limitless.exchange"

WINDOW_TO_KEYWORD = {"5m": "5 Min", "15m": "15 Min", "1h": "Hourly"}
PAGE_URL_FOR_WINDOW = {
    "5m":  "https://limitless.exchange/markets/btc-5-min-price",
    "15m": "https://limitless.exchange/markets/btc-15-min-price",
    "1h":  "https://limitless.exchange/markets/btc-hourly-price",
}
SLUG_REGEX_FOR_WINDOW = {
    "5m":  r"btc-up-or-down-5-min-\d+",
    "15m": r"btc-up-or-down-15-min-\d+",
    "1h":  r"btc-up-or-down-hourly-\d+",
}


SHOULD_STOP = False
def _stop_handler(*a):
    global SHOULD_STOP
    SHOULD_STOP = True
signal.signal(signal.SIGTERM, _stop_handler)
signal.signal(signal.SIGINT, _stop_handler)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def http_get(path, timeout=10):
    url = f"{API}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def find_current_market(keyword, window):
    code, data = http_get("/markets/active")
    if code == 200 and data:
        for m in data.get("data", []):
            title = (m.get("title") or "")
            if "BTC" in title.upper() and keyword in title:
                return {
                    "id": m.get("id"),
                    "slug": m.get("slug"),
                    "title": title,
                    "expirationTimestamp": m.get("expirationTimestamp"),
                    "startAt": m.get("startAt"),
                }
    if window and window in PAGE_URL_FOR_WINDOW:
        try:
            req = urllib.request.Request(PAGE_URL_FOR_WINDOW[window], headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", errors="replace")
            mm = re.search(SLUG_REGEX_FOR_WINDOW[window], html)
            if mm:
                slug = mm.group(0)
                code, m = http_get(f"/markets/{slug}")
                if code == 200 and m:
                    return {
                        "id": m.get("id"),
                        "slug": slug,
                        "title": m.get("title", ""),
                        "expirationTimestamp": m.get("expirationTimestamp"),
                        "startAt": m.get("startAt"),
                    }
        except Exception:
            pass
    return None


def parse_size_to_usd(price, size_raw):
    # WS event already gives size in shares (float). HTTP gives raw size*1e6.
    # Handle both: if size > 1e4 we assume it's the raw HTTP scale.
    try:
        s = float(size_raw)
    except (TypeError, ValueError):
        return 0.0, 0.0
    if s > 1e4:
        shares = s / 1e6
    else:
        shares = s
    return price * shares, shares


def snapshot_to_row(market, orderbook):
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bids_sorted = sorted(bids, key=lambda x: -float(x["price"]))
    asks_sorted = sorted(asks, key=lambda x: float(x["price"]))

    if bids_sorted:
        best_bid_price = float(bids_sorted[0]["price"])
        best_bid_usd, best_bid_shares = parse_size_to_usd(best_bid_price, bids_sorted[0]["size"])
    else:
        best_bid_price = 0; best_bid_usd = 0; best_bid_shares = 0
    if asks_sorted:
        best_ask_price = float(asks_sorted[0]["price"])
        best_ask_usd, best_ask_shares = parse_size_to_usd(best_ask_price, asks_sorted[0]["size"])
    else:
        best_ask_price = 0; best_ask_usd = 0; best_ask_shares = 0

    total_bid_usd = sum(parse_size_to_usd(float(b["price"]), b["size"])[0] for b in bids_sorted)
    total_ask_usd = sum(parse_size_to_usd(float(a["price"]), a["size"])[0] for a in asks_sorted)

    no_ask_price = round(1.0 - best_bid_price, 4) if best_bid_price > 0 else 0
    no_ask_size_usd = round(best_bid_usd, 4) if best_bid_price > 0 else 0
    no_ask_shares = round(best_bid_shares, 4) if best_bid_price > 0 else 0
    no_bid_price = round(1.0 - best_ask_price, 4) if best_ask_price > 0 else 0
    no_bid_size_usd = round(best_ask_usd, 4) if best_ask_price > 0 else 0
    no_bid_shares = round(best_ask_shares, 4) if best_ask_price > 0 else 0

    return {
        "ts": now_iso(),
        "epoch_sec": int(time.time()),
        "market_id": market.get("id", ""),
        "slug": market.get("slug", ""),
        "title": market.get("title", ""),
        "expiration": market.get("expirationTimestamp", ""),
        "best_bid": best_bid_price,
        "best_bid_size_usd": round(best_bid_usd, 4),
        "best_bid_shares": round(best_bid_shares, 4),
        "best_ask": best_ask_price,
        "best_ask_size_usd": round(best_ask_usd, 4),
        "best_ask_shares": round(best_ask_shares, 4),
        "total_bid_usd": round(total_bid_usd, 4),
        "total_ask_usd": round(total_ask_usd, 4),
        "spread": round(best_ask_price - best_bid_price, 4) if (best_ask_price and best_bid_price) else 0,
        "bid_levels": len(bids_sorted),
        "ask_levels": len(asks_sorted),
        "no_best_ask": no_ask_price,
        "no_best_ask_size_usd": no_ask_size_usd,
        "no_best_ask_shares": no_ask_shares,
        "no_best_bid": no_bid_price,
        "no_best_bid_size_usd": no_bid_size_usd,
        "no_best_bid_shares": no_bid_shares,
    }


class Recorder:
    def __init__(self, window, data_dir):
        self.window = window
        self.keyword = WINDOW_TO_KEYWORD[window]
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.combined_path = os.path.join(data_dir, "combined_per_second.csv")
        self.markets_path = os.path.join(data_dir, "markets.csv")
        self.events_path = os.path.join(data_dir, "events.csv")
        self.latest_path = os.path.join(data_dir, "latest.json")
        self.fieldnames = [
            "ts", "epoch_sec", "market_id", "slug", "title", "expiration",
            "best_bid", "best_bid_size_usd", "best_bid_shares",
            "best_ask", "best_ask_size_usd", "best_ask_shares",
            "total_bid_usd", "total_ask_usd",
            "spread", "bid_levels", "ask_levels",
            "no_best_ask", "no_best_ask_size_usd", "no_best_ask_shares",
            "no_best_bid", "no_best_bid_size_usd", "no_best_bid_shares",
        ]
        if not os.path.exists(self.combined_path):
            with open(self.combined_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.fieldnames).writeheader()
        if not os.path.exists(self.markets_path):
            with open(self.markets_path, "w", newline="") as f:
                f.write("ts,market_id,slug,title,startAt,expirationTimestamp\n")
        if not os.path.exists(self.events_path):
            with open(self.events_path, "w", newline="") as f:
                f.write("ts,event,detail\n")
        self.current_market = None
        self.last_csv_write_sec = 0

    def log_event(self, ev, detail=""):
        with open(self.events_path, "a") as f:
            f.write(f"{now_iso()},{ev},{str(detail).replace(',',';')[:300]}\n")

    def write_snapshot(self, market, orderbook):
        row = snapshot_to_row(market, orderbook)
        # CSV: at most one row per second to avoid blowup
        sec = int(time.time())
        if sec != self.last_csv_write_sec:
            with open(self.combined_path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=self.fieldnames).writerow(row)
            self.last_csv_write_sec = sec
        # latest.json on every event for sub-second bot reads
        tmp = self.latest_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({**row, "ts_ms": int(time.time() * 1000)}, f)
        os.replace(tmp, self.latest_path)

    def maybe_log_new_market(self, m):
        if not self.current_market or m["id"] != self.current_market["id"]:
            with open(self.markets_path, "a") as f:
                f.write(
                    f"{now_iso()},{m['id']},{m['slug']},\"{m['title']}\","
                    f"{m.get('startAt','')},{m.get('expirationTimestamp','')}\n"
                )
            self.log_event("MARKET_SET", f"id={m['id']} slug={m['slug']}")
            print(f"[{now_iso()}] new market: id={m['id']}  slug={m['slug']}", flush=True)
            return True
        return False


async def main_async(window, data_dir):
    rec = Recorder(window, data_dir)
    rec.log_event("START", f"window={window} keyword={rec.keyword} dir={data_dir} mode=WS")
    print(f"[{now_iso()}] starting LIMITLESS WS recorder for window={window} ({rec.keyword})", flush=True)
    print(f"  data dir: {data_dir}", flush=True)

    ws = WebSocketClient(WebSocketConfig(auto_reconnect=True))

    @ws.on("orderbookUpdate")
    async def on_ob(data):
        try:
            slug = data.get("marketSlug", "")
            if not rec.current_market or rec.current_market.get("slug") != slug:
                return
            ob = data.get("orderbook") or {}
            rec.write_snapshot(rec.current_market, ob)
        except Exception as e:
            rec.log_event("WRITE_ERR", f"{type(e).__name__}: {e}")

    @ws.on("orderbook")
    async def on_ob_alt(data):
        await on_ob(data)

    await ws.connect()
    rec.log_event("WS_CONNECTED", "")
    print(f"[{now_iso()}] ws connected", flush=True)

    while not SHOULD_STOP:
        try:
            m = find_current_market(rec.keyword, window)
            if m and (not rec.current_market or m["id"] != rec.current_market["id"]):
                # Unsubscribe old
                if rec.current_market:
                    try:
                        await ws.unsubscribe("subscribe_market_prices",
                                              {"marketSlugs": [rec.current_market["slug"]]})
                    except Exception:
                        pass
                # Subscribe new
                try:
                    await ws.subscribe("subscribe_market_prices",
                                       {"marketSlugs": [m["slug"]]})
                except Exception as e:
                    rec.log_event("SUBSCRIBE_ERR", f"{type(e).__name__}: {e}")
                rec.maybe_log_new_market(m)
                rec.current_market = m
            elif not m:
                rec.log_event("MARKET_NOT_FOUND", f"keyword={rec.keyword}")

            # Fallback HTTP snapshot every 5 sec in case WS is quiet (no ob change),
            # so latest.json doesn't go stale.
            if rec.current_market:
                code, ob = http_get(f"/markets/{rec.current_market['slug']}/orderbook")
                if code == 200 and ob:
                    rec.write_snapshot(rec.current_market, ob)
        except Exception as e:
            rec.log_event("LOOP_ERR", f"{type(e).__name__}: {e}")
        # Sleep with periodic stop check
        for _ in range(15):
            if SHOULD_STOP:
                break
            await asyncio.sleep(1)

    try:
        await ws.disconnect()
    except Exception:
        pass
    rec.log_event("STOP", "received signal")
    print(f"[{now_iso()}] stopped", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--window", choices=["5m", "15m", "1h"], default="15m")
    p.add_argument("--data-dir", default=None)
    args = p.parse_args()
    data_dir = args.data_dir or f"/root/data_limitless_btc_{args.window}"
    try:
        asyncio.run(main_async(args.window, data_dir))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
