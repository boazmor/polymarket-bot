#!/usr/bin/env python3
"""KALSHI_RECORDER_15M — Kalshi BTC 15-min binary recorder.

Polls https://api.elections.kalshi.com/trade-api/v2/markets every 1 sec
for series_ticker=KXBTC15M&status=open. Captures every active 15-min
BTC up/down market.

Kalshi resolves on CF Benchmarks BRTI (60-sec average). We log Binance
price as a proxy for the live target.

Schema aligned with Limitless recorder for similar bot handling.

Usage:
  python3 KALSHI_RECORDER_15M.py --coin BTC --data-dir /root/data_kalshi_btc_15m
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

KALSHI_MARKETS = "https://api.elections.kalshi.com/trade-api/v2/markets"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
POLL_SEC = 1.0

UA = {"User-Agent": "Mozilla/5.0"}


def http_json(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def iso_to_epoch(s):
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def get_binance_price():
    d = http_json(BINANCE_URL, timeout=3)
    if not d:
        return None
    try:
        return float(d.get("price") or 0) or None
    except (ValueError, TypeError):
        return None


def ensure_csv(path, header):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(header)


HEADER_COMBINED = [
    "ts", "epoch_sec", "market_id", "ticker", "event_ticker", "title",
    "open_epoch", "close_epoch", "sec_from_open",
    "binance_now", "target_price", "distance_signed", "distance_abs",
    "yes_bid", "yes_ask", "yes_bid_size", "yes_ask_size",
    "no_bid", "no_ask", "last_price", "volume_24h", "open_interest", "status",
]

HEADER_MARKETS = [
    "local_ts", "market_id", "ticker", "event_ticker", "title",
    "open_epoch", "close_epoch", "target_price", "first_seen_ts",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--coin", default="BTC")
    p.add_argument("--window", default="15m")
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()

    if args.coin != "BTC":
        sys.exit(f"only BTC supported, got {args.coin}")

    series = "KXBTC15M" if args.window == "15m" else None
    if not series:
        sys.exit(f"window must be 15m, got {args.window}")

    os.makedirs(args.data_dir, exist_ok=True)
    combined_path = os.path.join(args.data_dir, "combined_per_second.csv")
    markets_path = os.path.join(args.data_dir, "markets.csv")
    ensure_csv(combined_path, HEADER_COMBINED)
    ensure_csv(markets_path, HEADER_MARKETS)

    known = {}  # market_id -> dict

    print(f"KALSHI_RECORDER_15M started coin=BTC window={args.window} dir={args.data_dir}", flush=True)

    last_binance = None
    last_binance_ts = 0

    while True:
        try:
            t0 = time.time()
            now_epoch = int(t0)
            now_iso = datetime.now(tz=timezone.utc).isoformat()

            if t0 - last_binance_ts >= 0.5:
                bp = get_binance_price()
                if bp:
                    last_binance = bp
                    last_binance_ts = t0

            data = http_json(f"{KALSHI_MARKETS}?series_ticker={series}&status=open&limit=20")
            if not data:
                time.sleep(POLL_SEC)
                continue
            markets = data.get("markets") or []
            rows_written = 0
            for m in markets:
                ticker = m.get("ticker")
                if not ticker:
                    continue
                open_epoch = iso_to_epoch(m.get("open_time"))
                close_epoch = iso_to_epoch(m.get("close_time"))
                if not open_epoch:
                    continue
                target = None
                try:
                    target = float(m.get("floor_strike") or 0) or None
                except (ValueError, TypeError):
                    pass

                if ticker not in known:
                    known[ticker] = {
                        "open_epoch": open_epoch,
                        "close_epoch": close_epoch,
                        "target": target,
                        "first_seen": now_epoch,
                    }
                    with open(markets_path, "a", newline="") as f:
                        csv.writer(f).writerow([
                            now_iso, ticker, ticker, m.get("event_ticker"),
                            m.get("title"), open_epoch, close_epoch,
                            target, now_epoch,
                        ])

                def f(v):
                    try:
                        return float(v) if v not in (None, "", "null") else None
                    except (ValueError, TypeError):
                        return None

                dist_signed = (last_binance - target) if (last_binance and target) else None
                dist_abs = abs(dist_signed) if dist_signed is not None else None

                row = [
                    now_iso, now_epoch, ticker, ticker, m.get("event_ticker"),
                    m.get("title"), open_epoch, close_epoch,
                    now_epoch - open_epoch,
                    last_binance, target, dist_signed, dist_abs,
                    f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars")),
                    f(m.get("yes_bid_size_fp")), f(m.get("yes_ask_size_fp")),
                    f(m.get("no_bid_dollars")), f(m.get("no_ask_dollars")),
                    f(m.get("last_price_dollars")), f(m.get("volume_24h_fp")),
                    f(m.get("open_interest_fp")), m.get("status"),
                ]
                with open(combined_path, "a", newline="") as fh:
                    csv.writer(fh).writerow(row)
                rows_written += 1

            elapsed = time.time() - t0
            if elapsed < POLL_SEC:
                time.sleep(POLL_SEC - elapsed)

            if int(t0) % 60 == 0:
                print(f"{now_iso}  hb  binance={last_binance}  markets={len(known)}  rows_this_tick={rows_written}", flush=True)

        except KeyboardInterrupt:
            print("interrupted", flush=True)
            break
        except Exception as e:
            print(f"ERROR {type(e).__name__}: {e}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
