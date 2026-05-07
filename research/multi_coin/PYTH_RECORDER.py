#!/usr/bin/env python3
"""
PYTH_RECORDER.py — Pyth Network BTC/USD price recorder.

Polls Pyth's public Hermes API every second, records BTC/USD price.
This is the SAME oracle Predict.fun uses for strike determination.
At any 15-min boundary (00, 15, 30, 45 each hour), the price recorded
here IS the strike of the BTC 15-min Predict.fun market that just opened.

Same logic for 5-min boundaries (every 5 min).

API: https://hermes.pyth.network/v2/updates/price/latest
No authentication required, public endpoint.

Output: data_pyth_btc/per_second.csv

Run:
  screen -dmS pyth_btc python3 PYTH_RECORDER.py
"""

import csv
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime

API_URL = "https://hermes.pyth.network/v2/updates/price/latest?ids%5B%5D=e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"
DATA_DIR = "/root/data_pyth_btc"
POLL_INTERVAL_SEC = 1.0
HTTP_TIMEOUT = 5

SHOULD_STOP = False


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
        self.combined = os.path.join(self.data_dir, "per_second.csv")
        self.events = os.path.join(self.data_dir, "events.csv")
        self._init_if_missing(self.combined, [
            "local_ts", "epoch_sec",
            "btc_price",
            "pyth_publish_time",
            "pyth_conf",
            "is_market_boundary_15m",
            "is_market_boundary_5m",
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


def fetch_pyth_btc():
    """Returns (btc_price, publish_time, conf) or (None, None, None) on error."""
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            d = json.loads(r.read())
        p = d["parsed"][0]["price"]
        price = float(p["price"]) * (10 ** int(p["expo"]))
        publish_time = int(p["publish_time"])
        conf = float(p["conf"]) * (10 ** int(p["expo"]))
        return price, publish_time, conf
    except Exception:
        return None, None, None


def main():
    setup_signals()
    csvs = CsvStore(DATA_DIR)
    csvs.event("START", f"polling Pyth BTC/USD every {POLL_INTERVAL_SEC}s")
    print(f"[{now_local()}] starting Pyth recorder for BTC/USD")

    while not SHOULD_STOP:
        loop_start = time.time()
        price, pub_time, conf = fetch_pyth_btc()
        if price is None:
            csvs.event("FETCH_ERR", "")
        else:
            e = now_epoch()
            # Detect boundary moments (these are when Predict.fun strikes are SET)
            is_15m = (e % 900 == 0)
            is_5m = (e % 300 == 0)
            csvs.append_combined([
                now_local(), e,
                f"{price:.4f}",
                pub_time or "",
                f"{conf:.4f}" if conf else "",
                "1" if is_15m else "0",
                "1" if is_5m else "0",
            ])
            if is_15m:
                csvs.event("MARKET_15M_OPEN_STRIKE", f"btc=${price:.2f}")
            elif is_5m:
                csvs.event("MARKET_5M_OPEN_STRIKE", f"btc=${price:.2f}")
        elapsed = time.time() - loop_start
        sleep_for = max(0.0, POLL_INTERVAL_SEC - elapsed)
        time.sleep(sleep_for)

    csvs.event("STOP", "")


if __name__ == "__main__":
    main()
