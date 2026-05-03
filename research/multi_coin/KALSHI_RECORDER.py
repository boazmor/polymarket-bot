#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KALSHI_RECORDER.py
==================

Kalshi 15-minute crypto market recorder.

Polls Kalshi's public API every ~1 second to record the currently open
15-minute market for a given coin (BTC, ETH, SOL, XRP, DOGE, BNB, HYPE).

Output is per-coin, designed for side-by-side comparison with Polymarket
recordings on the same window:

  data_kalshi_<coin>_15m/
    combined_per_second.csv   -> tick-by-tick orderbook + prices + volume
    markets.csv               -> rollovers, market lifecycle
    events.csv                -> errors, reconnects, anomalies

Polled fields (one row per second):
  local_ts, epoch_sec, sec_from_open
  market_ticker, event_ticker, floor_strike
  yes_bid, yes_ask, yes_bid_size, yes_ask_size
  no_bid, no_ask
  last_price, volume_fp, volume_24h_fp, open_interest_fp
  open_time, close_time

Run:
    python3 KALSHI_RECORDER.py --coin BTC
    python3 KALSHI_RECORDER.py --coin ETH --data-dir /root/data_kalshi_eth_15m
"""

import argparse
import csv
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import requests

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
POLL_INTERVAL_SEC = 1.0   # one snapshot per second
HTTP_TIMEOUT = 5

# --- runtime config (set by main()) ---
COIN: str = "BTC"
WINDOW: str = "15m"      # Kalshi naming: 15M
SERIES_TICKER: str = ""  # e.g. KXBTC15M
DATA_DIR: str = ""

# --- shutdown flag ---
SHOULD_STOP = False


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def now_epoch_s() -> int:
    return int(time.time())


def install_signal_handlers():
    def handler(signum, frame):
        global SHOULD_STOP
        SHOULD_STOP = True
        print(f"\n[{now_local()}] received signal {signum}, stopping...")
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ============================================================
# CSV writer
# ============================================================
class CsvStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.paths: Dict[str, str] = {}

    def init_clean(self):
        if os.path.exists(self.data_dir):
            shutil.rmtree(self.data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.paths = {
            "combined": os.path.join(self.data_dir, "combined_per_second.csv"),
            "markets":  os.path.join(self.data_dir, "markets.csv"),
            "events":   os.path.join(self.data_dir, "events.csv"),
        }
        self._init(self.paths["combined"], [
            "local_ts", "epoch_sec", "sec_from_open",
            "market_ticker", "event_ticker", "floor_strike", "strike_type",
            "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size",
            "no_bid", "no_ask",
            "last_price", "volume_fp", "volume_24h_fp", "open_interest_fp",
            "open_time", "close_time", "status",
        ])
        self._init(self.paths["markets"], [
            "local_ts", "market_ticker", "event_ticker", "floor_strike",
            "open_time", "close_time", "expected_expiration_time", "title",
        ])
        self._init(self.paths["events"], ["local_ts", "event", "detail"])

    @staticmethod
    def _init(path: str, headers):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

    def append(self, key: str, row):
        with open(self.paths[key], "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def event(self, ev: str, detail: str = ""):
        self.append("events", [now_local(), ev, detail])


# ============================================================
# Kalshi API
# ============================================================
def fetch_open_market(series_ticker: str) -> Optional[Dict[str, Any]]:
    """Return the currently-open market for the series, or None if no open market."""
    try:
        r = requests.get(
            f"{API_BASE}/markets",
            params={"series_ticker": series_ticker, "status": "open", "limit": 5},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        markets = r.json().get("markets", [])
        if not markets:
            return None
        # Of the open markets, pick the one whose close_time is soonest in the future
        # (the "current" one — Kalshi keeps the next few markets pre-listed).
        now = now_epoch_s()
        future_markets = []
        for m in markets:
            ct_epoch = time_to_epoch(m.get("close_time"))
            if ct_epoch is not None and ct_epoch > now:
                future_markets.append((ct_epoch, m))
        if not future_markets:
            return None  # no genuinely-open market right now
        future_markets.sort()
        return future_markets[0][1]
    except Exception:
        return None


def time_to_epoch(s: Optional[str]) -> Optional[int]:
    """Parse Kalshi UTC timestamp ('2026-05-03T02:15:00Z') to epoch seconds.
    The trailing Z means UTC — must be set explicitly so .timestamp() doesn't
    interpret it as the local timezone (which causes ±hours of error)."""
    if not s:
        return None
    try:
        dt = datetime.strptime(s.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


# ============================================================
# Main loop
# ============================================================
def main_loop(csvs: CsvStore):
    current_ticker: Optional[str] = None
    current_open_epoch: Optional[int] = None

    csvs.event("START", f"COIN={COIN} SERIES={SERIES_TICKER} POLL={POLL_INTERVAL_SEC}s")

    while not SHOULD_STOP:
        loop_start = time.time()

        m = fetch_open_market(SERIES_TICKER)
        if m is None:
            csvs.event("NO_OPEN_MARKET", "")
            time.sleep(2.0)
            continue

        ticker = m.get("ticker")
        if ticker != current_ticker:
            # rollover (or first market)
            event_ticker = m.get("event_ticker", "")
            csvs.append("markets", [
                now_local(), ticker, event_ticker, m.get("floor_strike"),
                m.get("open_time"), m.get("close_time"),
                m.get("expected_expiration_time"), m.get("title"),
            ])
            csvs.event("MARKET_ROLLOVER", f"{current_ticker} -> {ticker}")
            current_ticker = ticker
            current_open_epoch = time_to_epoch(m.get("open_time"))

        sec_from_open = None
        if current_open_epoch:
            sec_from_open = max(0, now_epoch_s() - current_open_epoch)

        csvs.append("combined", [
            now_local(),
            now_epoch_s(),
            sec_from_open,
            ticker,
            m.get("event_ticker"),
            m.get("floor_strike"),
            m.get("strike_type"),
            m.get("yes_bid_dollars"),
            m.get("yes_ask_dollars"),
            m.get("yes_bid_size_fp"),
            m.get("yes_ask_size_fp"),
            m.get("no_bid_dollars"),
            m.get("no_ask_dollars"),
            m.get("last_price_dollars"),
            m.get("volume_fp"),
            m.get("volume_24h_fp"),
            m.get("open_interest_fp"),
            m.get("open_time"),
            m.get("close_time"),
            m.get("status"),
        ])

        # tick the loop
        elapsed = time.time() - loop_start
        sleep_for = max(0.0, POLL_INTERVAL_SEC - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)


# ============================================================
# CLI
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(description="Kalshi 15-min crypto market recorder.")
    p.add_argument("--coin", required=True, choices=["BTC","ETH","SOL","XRP","DOGE","BNB","HYPE"])
    p.add_argument("--data-dir", default=None,
                   help="Output directory (default: data_kalshi_<coin>_15m)")
    p.add_argument("--poll-sec", type=float, default=1.0)
    return p.parse_args()


def main():
    global COIN, SERIES_TICKER, DATA_DIR, POLL_INTERVAL_SEC
    args = parse_args()
    COIN = args.coin.upper()
    SERIES_TICKER = f"KX{COIN}15M"
    DATA_DIR = args.data_dir or f"data_kalshi_{COIN.lower()}_15m"
    POLL_INTERVAL_SEC = args.poll_sec

    print(f"COIN={COIN}  SERIES={SERIES_TICKER}  DATA_DIR={DATA_DIR}")
    print(f"poll interval: {POLL_INTERVAL_SEC}s")

    csvs = CsvStore(DATA_DIR)
    csvs.init_clean()
    install_signal_handlers()

    try:
        main_loop(csvs)
    except KeyboardInterrupt:
        pass
    finally:
        csvs.event("STOP", "exit")
        print(f"[{now_local()}] stopped")


if __name__ == "__main__":
    main()
