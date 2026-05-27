#!/usr/bin/env python3
"""GEMINI_RECORDER_5M — Gemini Predictions BTC 5-min binary recorder.

Polls https://api.gemini.com/v1/prediction-markets/events every 1 sec.
Captures every active BTC 5-min binary market. Writes one row per
(ts, market) snapshot to combined_per_second.csv.

Schema is aligned with /root/data_limitless_btc_5m/combined_per_second.csv
so the bot can read both with similar code.

Outcome reference: Kaiko (per Gemini docs). We log binance_now as a proxy
since we already poll Binance for other recorders; the comparison ≤ $50.

Usage:
  python3 GEMINI_RECORDER_5M.py --coin BTC --window 5m --data-dir /root/data_gemini_btc_5m
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

GEM_EVENTS = "https://api.gemini.com/v1/prediction-markets/events"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
POLL_SEC = 1.0

UA = {"User-Agent": "Mozilla/5.0"}

# Ticker pattern: GEMI-BTC05M2605271205-UP / -DN (UP / Down labels)
TICKER_RE = re.compile(r"^BTC(05M|15M)(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$")


def http_json(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return None


def parse_resolution_epoch(ticker):
    """BTC05M2605271210 -> epoch_sec for 2026-05-27 12:10 UTC (market RESOLVES then)."""
    m = TICKER_RE.match(ticker)
    if not m:
        return None
    dur, yy, mo, dd, hh, mm = m.groups()
    try:
        dt = datetime(2000 + int(yy), int(mo), int(dd), int(hh), int(mm), 0, tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def parse_open_epoch(ticker):
    """Trading-open epoch = resolution - duration. 5M -> -300, 15M -> -900."""
    m = TICKER_RE.match(ticker)
    if not m:
        return None
    dur = m.group(1)
    res = parse_resolution_epoch(ticker)
    if res is None:
        return None
    return res - (300 if dur == "05M" else 900)


def get_binance_price():
    d = http_json(BINANCE_URL, timeout=3)
    if not d:
        return None
    try:
        return float(d.get("price") or 0) or None
    except (ValueError, TypeError):
        return None


def ensure_data_dir(path):
    os.makedirs(path, exist_ok=True)


def ensure_csv(path, header):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(header)


HEADER_COMBINED = [
    "ts", "epoch_sec", "market_id", "slug", "ticker", "title",
    "market_open_epoch", "sec_from_open", "expiration",
    "binance_now", "target_price", "distance_signed", "distance_abs",
    "best_bid", "best_ask", "best_bid_size_usd", "best_ask_size_usd",
    "no_best_bid", "no_best_ask", "no_best_bid_size_usd", "no_best_ask_size_usd",
    "yes_last_trade", "no_last_trade", "market_state",
]

HEADER_MARKETS = [
    "local_ts", "market_id", "slug", "ticker", "market_open_epoch",
    "expiration", "target_price", "first_seen_ts",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--coin", default="BTC")  # only BTC supported
    p.add_argument("--window", default="5m")  # 5m or 15m
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()

    if args.coin != "BTC":
        sys.exit(f"only BTC supported, got {args.coin}")
    dur_tag = "05M" if args.window == "5m" else "15M" if args.window == "15m" else None
    if not dur_tag:
        sys.exit(f"window must be 5m or 15m, got {args.window}")

    ensure_data_dir(args.data_dir)
    combined_path = os.path.join(args.data_dir, "combined_per_second.csv")
    markets_path = os.path.join(args.data_dir, "markets.csv")
    ensure_csv(combined_path, HEADER_COMBINED)
    ensure_csv(markets_path, HEADER_MARKETS)

    # known markets cache (market_id -> dict with target_price, open_epoch, ...)
    known = {}

    print(f"GEMINI_RECORDER_5M started  coin=BTC window={args.window} dir={args.data_dir}", flush=True)
    last_binance = None
    last_binance_ts = 0
    binance_min_interval = 0.5  # don't hit Binance more than 2/sec

    while True:
        try:
            t0 = time.time()
            now_epoch = int(t0)
            now_iso = datetime.now(tz=timezone.utc).isoformat()

            # Refresh Binance reference
            if t0 - last_binance_ts >= binance_min_interval:
                bp = get_binance_price()
                if bp:
                    last_binance = bp
                    last_binance_ts = t0

            # Fetch Gemini events
            data = http_json(GEM_EVENTS, timeout=5)
            if not data:
                time.sleep(POLL_SEC)
                continue
            events = data.get("data") or []

            # Filter to BTC binary 5/15 min
            target_btc = f"BTC{dur_tag}"
            rows_written = 0
            for e in events:
                eticker = e.get("ticker") or ""
                if not eticker.startswith(target_btc):
                    continue
                if e.get("status") != "active":
                    continue
                open_epoch = parse_open_epoch(eticker)
                resolution_epoch = parse_resolution_epoch(eticker)
                if not open_epoch:
                    continue
                contracts = e.get("contracts") or []
                # Gemini BTC 5/15 min: single binary contract labeled "Up".
                # The DOWN side is the same contract's buy.no / sell.no field.
                up_c = None
                for c in contracts:
                    if (c.get("label") or "").strip().upper() == "UP":
                        up_c = c
                        break
                if not up_c:
                    continue

                # Discover new market
                mid = str(e.get("id") or eticker)
                if mid not in known:
                    known[mid] = {
                        "open_epoch": open_epoch,
                        "resolution_epoch": resolution_epoch,
                        "target_price": None,
                        "first_seen": now_epoch,
                    }

                # Lock in target_price at first sample within first 10 sec of market open
                if known[mid]["target_price"] is None:
                    if now_epoch >= open_epoch and now_epoch - open_epoch <= 10:
                        known[mid]["target_price"] = last_binance
                        with open(markets_path, "a", newline="") as f:
                            csv.writer(f).writerow([
                                now_iso, mid, e.get("slug"), eticker,
                                open_epoch, resolution_epoch,
                                known[mid]["target_price"], now_epoch,
                            ])

                target = known[mid]["target_price"]
                dist_signed = (last_binance - target) if (last_binance and target) else None
                dist_abs = abs(dist_signed) if dist_signed is not None else None

                up_prices = up_c.get("prices") or {}
                buy = up_prices.get("buy") or {}
                sell = up_prices.get("sell") or {}
                def f(v):
                    try:
                        return float(v) if v not in (None, "", "null") else None
                    except (ValueError, TypeError):
                        return None

                # Gemini binary: same contract serves both directions.
                # buy.yes = ask to bet UP; sell.yes = bid to exit UP.
                # buy.no  = ask to bet DOWN; sell.no  = bid to exit DOWN.
                row = [
                    now_iso, now_epoch, mid, e.get("slug"), eticker, e.get("title"),
                    open_epoch, now_epoch - open_epoch, resolution_epoch,
                    last_binance, target, dist_signed, dist_abs,
                    f(sell.get("yes")), f(buy.get("yes")),  # best_bid, best_ask (UP)
                    None, None,  # size USD not in /events response; needs /v1/book
                    f(sell.get("no")), f(buy.get("no")),  # no_best_bid, no_best_ask
                    None, None,
                    f(up_prices.get("lastTradePrice")),
                    None,
                    up_c.get("marketState"),
                ]
                with open(combined_path, "a", newline="") as f:
                    csv.writer(f).writerow(row)
                rows_written += 1

            elapsed = time.time() - t0
            if elapsed < POLL_SEC:
                time.sleep(POLL_SEC - elapsed)

            # heartbeat log (stderr-ish)
            if int(t0) % 60 == 0:
                print(f"{now_iso}  hb  binance={last_binance}  active_markets={len(known)}  rows_this_tick={rows_written}", flush=True)

        except KeyboardInterrupt:
            print("interrupted", flush=True)
            break
        except Exception as e:
            print(f"ERROR {type(e).__name__}: {e}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
