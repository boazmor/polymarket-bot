#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_4_platforms.py - live monitor for 4 prediction-market platforms.

Reads the latest row from each recorder's combined_per_second.csv every second
and displays a Hebrew-labeled snapshot you can cross-check against the actual
websites.

Run:
    python3 monitor_4_platforms.py
"""
import csv
import os
import time
from datetime import datetime

PATHS = {
    "POLY":    "/root/data_btc_15m_research/combined_per_second.csv",
    "KALSHI":  "/root/data_kalshi_btc_15m/combined_per_second.csv",
    "GEMINI":  "/root/data_gemini_btc_15m/combined_per_second.csv",
    "PREDICT": "/root/data_predict_btc_15m/combined_per_second.csv",
}

# URL templates
URL = {
    "POLY":    "polymarket.com/event/{slug}",
    "KALSHI":  "kalshi.com/markets/kxbtc15m/btc-15m",
    "GEMINI":  "gemini.com/predictions/{ticker}/...",
    "PREDICT": "predict.fun/market/{slug}",
}


def tail_last(path):
    """Efficiently read last line of CSV by seeking from end of file."""
    if not os.path.exists(path):
        return None
    try:
        # Read header
        with open(path) as f:
            header = f.readline().strip().split(",")
        # Read last line by seeking from end
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            seek_back = min(size, 4096)
            f.seek(size - seek_back)
            tail = f.read().decode("utf-8", errors="ignore").splitlines()
        if not tail:
            return None
        last_line = tail[-1]
        values = last_line.split(",")
        return dict(zip(header, values))
    except Exception:
        return None


def fmt(v, w=10):
    s = str(v) if v is not None else "-"
    return s[:w].rjust(w)


def render():
    rows = {k: tail_last(p) for k, p in PATHS.items()}
    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n=== מעקב 4 פלטפורמות BTC 15min  שעה: {now} ===")

    # POLY
    r = rows.get("POLY")
    if r:
        slug = r.get('market_slug','-')
        print(f"\nפולי")
        print(f"  4 ספרות אחרונות בכתובת: {slug[-4:]}")
        print(f"  סטרייק צ'יינלינק: {r.get('target_chainlink_at_open','-')}")
        print(f"  ביננס עכשיו:    {r.get('binance_price','-')}")
        print(f"  UP  bid={r.get('up_bid','-')}  ask={r.get('up_ask','-')}")
        print(f"  DOWN bid={r.get('down_bid','-')}  ask={r.get('down_ask','-')}")
        print(f"  עדכון: {r.get('local_ts','-')[:19]}")

    # KALSHI
    r = rows.get("KALSHI")
    if r:
        ticker = r.get('market_ticker','-')
        print(f"\nקלשי")
        print(f"  סוף הקוד מרקט: {ticker[-7:]}")
        print(f"  סטרייק: {r.get('floor_strike','-')}")
        print(f"  ביננס עכשיו: {r.get('binance_now','-')}")
        print(f"  YES bid={r.get('yes_bid','-')}  ask={r.get('yes_ask','-')}")
        print(f"  NO  bid={r.get('no_bid','-')}  ask={r.get('no_ask','-')}")
        print(f"  עדכון: {r.get('local_ts','-')[:19]}")

    # GEMINI
    r = rows.get("GEMINI")
    if r:
        ticker = r.get('ticker','-')
        print(f"\nג'מיני")
        print(f"  סוף הקוד מרקט: {ticker[-4:]}")
        print(f"  סטרייק: {r.get('strike','-')}")
        print(f"  ביננס עכשיו: {r.get('binance_now','-')}")
        print(f"  YES bid={r.get('yes_bid','-')}  ask={r.get('yes_ask','-')}")
        print(f"  NO  ask={r.get('no_ask_implied','-')}")
        print(f"  עדכון: {r.get('local_ts','-')[:19]}")

    # PREDICT
    r = rows.get("PREDICT")
    if r:
        slug = r.get('slug','-')
        print(f"\nפרדי")
        print(f"  4 ספרות אחרונות בכתובת: {slug[-4:]}")
        print(f"  סטרייק: {r.get('strike','-')}")
        print(f"  ביננס עכשיו: {r.get('binance_now','-')}")
        print(f"  YES bid={r.get('yes_bid','-')}  ask={r.get('yes_ask','-')}")
        print(f"  NO  ask={r.get('no_ask_implied','-')}")
        print(f"  עדכון: {r.get('local_ts','-')[:19]}")


def main():
    while True:
        # ANSI clear screen + cursor home
        print("\033[2J\033[H", end="")
        render()
        time.sleep(1)


if __name__ == "__main__":
    main()
