#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEMINI_RECORDER.py
==================

Gemini Predictions 15-minute crypto market recorder.

Polls Gemini's public Predictions API every ~1 second to record the
currently open 15-minute market for a given coin. Designed to sit
alongside the existing Polymarket and Kalshi recorders for cross-platform
arbitrage analysis.

Gemini binary contracts only expose the YES side ("price >= strike").
To bet DOWN on Gemini you must short YES (sell at bid). For our buy-only
arb formula, that means Gemini is usable as the "BET ABOVE" leg —
pairing with PolyDOWN to form Direction B (both legs are pure buys).

Output: data_gemini_<coin>_15m/
  combined_per_second.csv   tick-by-tick orderbook + strike + status
  markets.csv               rollovers, market lifecycle
  events.csv                errors, anomalies

Run:
  python3 GEMINI_RECORDER.py --coin BTC
  python3 GEMINI_RECORDER.py --coin ETH --data-dir /root/data_gemini_eth_15m
"""

import argparse
import csv
import os
import shutil
import signal
import sys
import time
import urllib.request
import urllib.error
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

API_BASE = "https://api.gemini.com"
POLL_INTERVAL_SEC = 1.0
HTTP_TIMEOUT = 5

COIN: str = "BTC"
WINDOW: str = "15m"
SERIES: str = ""           # e.g. BTC15M
DATA_DIR: str = ""

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


def http_get(url: str) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return None


class CsvStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.paths: Dict[str, str] = {}

    def init_or_resume(self):
        os.makedirs(self.data_dir, exist_ok=True)
        self.paths = {
            "combined": os.path.join(self.data_dir, "combined_per_second.csv"),
            "markets":  os.path.join(self.data_dir, "markets.csv"),
            "events":   os.path.join(self.data_dir, "events.csv"),
        }
        self._init_if_missing(self.paths["combined"], [
            "local_ts", "epoch_sec", "sec_from_open",
            "ticker", "instrument_symbol", "strike",
            "yes_bid", "yes_ask",
            "yes_bid_size", "yes_ask_size",
            "yes_bid_usd", "yes_ask_usd",
            "no_ask_implied", "no_bid_implied",
            # NO-side depth in USD (cost to actually fill a buy-NO order):
            #   no_ask_usd_buyable = (1 - yes_bid) * yes_bid_size  at best level
            #   no_ask_usd_total   = sum across all yes_bid levels
            "no_ask_usd_buyable", "no_ask_usd_total",
            "last_trade_price",
            "open_time", "close_time", "status",
        ])
        self._init_if_missing(self.paths["markets"], [
            "local_ts", "ticker", "instrument_symbol", "strike",
            "open_time", "close_time", "title",
        ])
        self._init_if_missing(self.paths["events"], ["local_ts", "event", "detail"])
        self.event("RECORDER_RESUME", f"COIN={COIN} SERIES={SERIES}")

    @staticmethod
    def _init_if_missing(path: str, headers):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)

    def append(self, key: str, row):
        with open(self.paths[key], "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def event(self, ev: str, detail: str = ""):
        self.append("events", [now_local(), ev, detail])


def fetch_active_event(series: str) -> Optional[Dict[str, Any]]:
    """Find the currently-open event for the series (BTC15M, ETH15M, ...).
    Picks the soonest-expiring active event."""
    data = http_get(f"{API_BASE}/v1/prediction-markets/events?status=active&category=crypto&limit=200")
    if not data:
        return None
    events = [e for e in data.get("data", []) if e.get("series") == series]
    if not events:
        return None
    now = now_epoch_s()
    future = []
    for e in events:
        exp = parse_iso_to_epoch(e.get("expiryDate"))
        if exp is not None and exp > now:
            future.append((exp, e))
    if not future:
        return None
    future.sort()
    return future[0][1]


def fetch_orderbook(symbol: str) -> Dict[str, List[Dict[str, str]]]:
    """Fetch order book. Returns {'bids': [...], 'asks': [...]} or empty."""
    data = http_get(f"{API_BASE}/v1/book/{symbol}")
    if not data:
        return {"bids": [], "asks": []}
    return {"bids": data.get("bids", []) or [], "asks": data.get("asks", []) or []}


def parse_iso_to_epoch(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        s = s.split(".")[0].rstrip("Z")
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def best_levels(book_side: List[Dict[str, str]]):
    """Return (best_price, best_size, total_usd_at_best, total_usd_all_levels)."""
    if not book_side:
        return None, None, 0.0, 0.0
    try:
        best = book_side[0]
        bp = float(best.get("price") or 0)
        bs = float(best.get("amount") or 0)
        best_usd = bp * bs
        total_usd = 0.0
        for lvl in book_side:
            try:
                total_usd += float(lvl.get("price") or 0) * float(lvl.get("amount") or 0)
            except Exception:
                pass
        return bp, bs, best_usd, total_usd
    except Exception:
        return None, None, 0.0, 0.0


def main_loop(csvs: CsvStore):
    current_ticker: Optional[str] = None
    current_open_epoch: Optional[int] = None
    current_symbol: Optional[str] = None
    no_market_logged = False

    csvs.event("START", f"COIN={COIN} SERIES={SERIES} POLL={POLL_INTERVAL_SEC}s")

    while not SHOULD_STOP:
        loop_start = time.time()

        ev = fetch_active_event(SERIES)
        if ev is None:
            if not no_market_logged:
                csvs.event("NO_OPEN_MARKET", "")
                no_market_logged = True
            time.sleep(2.0)
            continue
        no_market_logged = False

        contracts = ev.get("contracts") or []
        if not contracts:
            csvs.event("EVENT_NO_CONTRACTS", ev.get("ticker", ""))
            time.sleep(2.0)
            continue

        # Gemini binary markets have exactly one contract (the YES side)
        c = contracts[0]
        instrument = c.get("instrumentSymbol")
        ticker = ev.get("ticker")
        strike_value = (c.get("strike") or {}).get("value")
        try:
            strike_f = float(strike_value) if strike_value is not None else None
        except Exception:
            strike_f = None

        if ticker != current_ticker:
            open_iso = ev.get("startTime") or c.get("effectiveDate") or ev.get("effectiveDate")
            close_iso = ev.get("expiryDate") or c.get("expiryDate")
            csvs.append("markets", [
                now_local(), ticker, instrument, strike_value,
                open_iso, close_iso, ev.get("title", ""),
            ])
            csvs.event("MARKET_ROLLOVER", f"{current_ticker} -> {ticker}")
            current_ticker = ticker
            current_symbol = instrument
            current_open_epoch = parse_iso_to_epoch(open_iso)

        sec_from_open = None
        if current_open_epoch:
            sec_from_open = max(0, now_epoch_s() - current_open_epoch)

        book = fetch_orderbook(instrument)
        bid_p, bid_sz, bid_usd_best, bid_usd_total = best_levels(book["bids"])
        ask_p, ask_sz, ask_usd_best, ask_usd_total = best_levels(book["asks"])

        # Both YES and NO are buyable via the same instrument with outcome=yes|no.
        # Buying NO at price X means matching with a yes_bid at (1-X), so:
        #   no_ask = 1 - yes_bid; no_bid = 1 - yes_ask
        # NO-side depth in USD: cost to buy 1 NO contract = (1 - yes_bid). Filling
        # yes_bid_size contracts costs (1 - yes_bid) * yes_bid_size at best level.
        no_ask_implied = (1.0 - bid_p) if bid_p is not None else None
        no_bid_implied = (1.0 - ask_p) if ask_p is not None else None
        no_ask_usd_buyable = (no_ask_implied * bid_sz) if (no_ask_implied is not None and bid_sz is not None) else 0.0
        no_ask_usd_total = 0.0
        for lvl in book["bids"]:
            try:
                yp = float(lvl.get("price") or 0)
                ya = float(lvl.get("amount") or 0)
                no_ask_usd_total += (1.0 - yp) * ya
            except Exception:
                pass

        prices = c.get("prices") or {}
        last_trade = prices.get("lastTradePrice")

        close_iso = ev.get("expiryDate")
        open_iso = ev.get("startTime") or c.get("effectiveDate")

        csvs.append("combined", [
            now_local(), now_epoch_s(), sec_from_open,
            ticker, instrument, strike_value,
            f"{bid_p:.4f}" if bid_p is not None else "",
            f"{ask_p:.4f}" if ask_p is not None else "",
            f"{bid_sz:.4f}" if bid_sz is not None else "",
            f"{ask_sz:.4f}" if ask_sz is not None else "",
            f"{bid_usd_total:.4f}",
            f"{ask_usd_total:.4f}",
            f"{no_ask_implied:.4f}" if no_ask_implied is not None else "",
            f"{no_bid_implied:.4f}" if no_bid_implied is not None else "",
            f"{no_ask_usd_buyable:.4f}",
            f"{no_ask_usd_total:.4f}",
            last_trade or "",
            open_iso or "", close_iso or "", c.get("status", ""),
        ])

        elapsed = time.time() - loop_start
        sleep_for = max(0.0, POLL_INTERVAL_SEC - elapsed)
        time.sleep(sleep_for)

    csvs.event("STOP", "")


def main():
    global COIN, WINDOW, SERIES, DATA_DIR
    p = argparse.ArgumentParser()
    p.add_argument("--coin", default="BTC", choices=["BTC", "ETH", "SOL", "XRP", "ZEC"])
    p.add_argument("--window", default="15m", choices=["5m", "15m", "1h"])
    p.add_argument("--data-dir", default=None)
    args = p.parse_args()

    COIN = args.coin.upper()
    WINDOW = args.window
    series_window = {"5m": "05M", "15m": "15M", "1h": "1H"}[WINDOW]
    SERIES = f"{COIN}{series_window}"
    DATA_DIR = args.data_dir or f"/root/data_gemini_{COIN.lower()}_{WINDOW}"

    print(f"[{now_local()}] starting GEMINI_RECORDER coin={COIN} series={SERIES} dir={DATA_DIR}")

    install_signal_handlers()
    csvs = CsvStore(DATA_DIR)
    csvs.init_or_resume()

    try:
        main_loop(csvs)
    except Exception as e:
        csvs.event("FATAL", f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
