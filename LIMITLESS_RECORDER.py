#!/usr/bin/env python3
"""LIMITLESS_RECORDER — records BTC Up/Down market orderbook depth on Limitless Exchange.

Polls /markets/active to find the current market for a given window (5 Min / 15 Min / Hourly).
Polls /markets/{slug}/orderbook every second.

Output:
  data_dir/combined_per_second.csv — per-second snapshot
  data_dir/markets.csv             — market lifecycle log
  data_dir/events.csv              — errors, transitions
  data_dir/latest.json             — current snapshot for live bots
"""

import argparse
import csv
import json
import os
import signal
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HEADERS = {"User-Agent": UA}
API = "https://api.limitless.exchange"

WINDOW_TO_KEYWORD = {
    "5m":  "5 Min",
    "15m": "15 Min",
    "1h":  "Hourly",
}

SHOULD_STOP = False
def stop_handler(*a):
    global SHOULD_STOP
    SHOULD_STOP = True
signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)


def http_get(path, timeout=10):
    url = f"{API}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, str(e)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


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

import re

def find_current_market(keyword: str, window: str = None):
    """Try /markets/active first. If not found and window given, scrape page for slug."""
    code, data = http_get("/markets/active")
    if code == 200 and data:
        for m in data.get("data", []):
            title = (m.get("title") or "")
            if "BTC" in title.upper() and keyword in title:
                return {
                    "id": m.get("id"),
                    "slug": m.get("slug"),
                    "conditionId": m.get("conditionId"),
                    "title": title,
                    "expirationTimestamp": m.get("expirationTimestamp"),
                    "startAt": m.get("startAt"),
                    "createdAt": m.get("createdAt"),
                }
    # Fallback: scrape page for slug (markets in FUNDED status not in /active)
    if window and window in PAGE_URL_FOR_WINDOW:
        try:
            url = PAGE_URL_FOR_WINDOW[window]
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", errors="replace")
            slug_match = re.search(SLUG_REGEX_FOR_WINDOW[window], html)
            if slug_match:
                slug = slug_match.group(0)
                # Fetch market metadata by slug
                code, m = http_get(f"/markets/{slug}")
                if code == 200 and m:
                    return {
                        "id": m.get("id"),
                        "slug": slug,
                        "conditionId": m.get("conditionId"),
                        "title": m.get("title", ""),
                        "expirationTimestamp": m.get("expirationTimestamp"),
                        "startAt": m.get("startAt"),
                        "createdAt": m.get("createdAt"),
                    }
        except Exception as e:
            pass
    return None


def get_orderbook(slug: str):
    code, data = http_get(f"/markets/{slug}/orderbook")
    if code != 200 or not data:
        return None
    return data


def parse_size_to_usd(price: float, size_raw):
    """Convert size from raw API units to USD amount."""
    # Empirically size_raw is in micro-units (10000000 = 10 USDC worth at some price scale).
    # The market trades shares each worth $1 at resolution. Size is likely in shares × 10^6.
    # 10000000 / 1e6 = 10 shares. At $0.5 each that's $5.
    shares = float(size_raw) / 1e6
    return price * shares, shares


def snapshot_to_row(m, ob):
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
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

    # NO outcome is the CTF complement of YES: NO_ask_price = 1 - YES_bid_price,
    # NO_bid_price = 1 - YES_ask_price. Liquidity carries over to the opposite side.
    no_ask_price = round(1.0 - best_bid_price, 4) if best_bid_price > 0 else 0
    no_ask_size_usd = round(best_bid_usd, 4) if best_bid_price > 0 else 0
    no_ask_shares = round(best_bid_shares, 4) if best_bid_price > 0 else 0
    no_bid_price = round(1.0 - best_ask_price, 4) if best_ask_price > 0 else 0
    no_bid_size_usd = round(best_ask_usd, 4) if best_ask_price > 0 else 0
    no_bid_shares = round(best_ask_shares, 4) if best_ask_price > 0 else 0

    return {
        "ts": now_iso(),
        "epoch_sec": int(time.time()),
        "market_id": m["id"],
        "slug": m["slug"],
        "title": m["title"],
        "expiration": m.get("expirationTimestamp", ""),
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--window", choices=["5m", "15m", "1h"], default="15m")
    p.add_argument("--data-dir", default=None)
    args = p.parse_args()

    keyword = WINDOW_TO_KEYWORD[args.window]
    data_dir = args.data_dir or f"/root/data_limitless_btc_{args.window}"
    os.makedirs(data_dir, exist_ok=True)

    combined_path = os.path.join(data_dir, "combined_per_second.csv")
    markets_path = os.path.join(data_dir, "markets.csv")
    events_path = os.path.join(data_dir, "events.csv")
    latest_path = os.path.join(data_dir, "latest.json")

    fieldnames = [
        "ts", "epoch_sec", "market_id", "slug", "title", "expiration",
        "best_bid", "best_bid_size_usd", "best_bid_shares",
        "best_ask", "best_ask_size_usd", "best_ask_shares",
        "total_bid_usd", "total_ask_usd",
        "spread", "bid_levels", "ask_levels",
        "no_best_ask", "no_best_ask_size_usd", "no_best_ask_shares",
        "no_best_bid", "no_best_bid_size_usd", "no_best_bid_shares",
    ]
    # Init CSVs
    if not os.path.exists(combined_path):
        with open(combined_path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    if not os.path.exists(markets_path):
        with open(markets_path, "w", newline="") as f:
            f.write("ts,market_id,slug,title,startAt,expirationTimestamp\n")
    if not os.path.exists(events_path):
        with open(events_path, "w", newline="") as f:
            f.write("ts,event,detail\n")

    def log_event(ev, detail=""):
        with open(events_path, "a") as f:
            f.write(f"{now_iso()},{ev},{detail.replace(',',';')[:300]}\n")

    log_event("START", f"window={args.window} keyword={keyword} dir={data_dir}")
    print(f"[{now_iso()}] starting LIMITLESS recorder for window={args.window} ({keyword})")
    print(f"  data dir: {data_dir}")

    current_market_id = None
    last_market = None

    while not SHOULD_STOP:
        try:
            m = find_current_market(keyword, args.window)
            if not m:
                log_event("MARKET_NOT_FOUND", f"keyword={keyword}")
                time.sleep(5)
                continue

            # Detect market rollover
            if m["id"] != current_market_id:
                current_market_id = m["id"]
                with open(markets_path, "a") as f:
                    f.write(f"{now_iso()},{m['id']},{m['slug']},\"{m['title']}\",{m.get('startAt','')},{m.get('expirationTimestamp','')}\n")
                log_event("MARKET_SET", f"id={m['id']} slug={m['slug']}")
                print(f"\n[{now_iso()}] new market: id={m['id']}  slug={m['slug']}")

            ob = get_orderbook(m["slug"])
            if not ob:
                log_event("ORDERBOOK_FETCH_FAILED", f"slug={m['slug']}")
                time.sleep(1)
                continue

            row = snapshot_to_row(m, ob)
            with open(combined_path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writerow(row)

            # Write latest.json for bots to read with sub-second latency
            tmp = latest_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({**row, "ts_ms": int(time.time() * 1000)}, f)
            os.replace(tmp, latest_path)

            last_market = m
        except Exception as e:
            log_event("LOOP_ERROR", f"{type(e).__name__}: {e}")

        time.sleep(1)

    log_event("STOP", "received signal")
    print("stopped")


if __name__ == "__main__":
    main()
