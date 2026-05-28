#!/usr/bin/env python3
"""OKX Event Contracts recorder — BTC UPDOWN 5-min and 15-min markets.

OKX exposes binary UP/DOWN events via the public v5 market API (no auth for
data). instId format: BTC-UPDOWN-5MIN-YYMMDD-HHMM-HHMM where the two times are
the window start/end in UTC+8 (Singapore). ask/bid are the UP-side prices;
the DOWN ask is taken as (1 - up_bid).

Writes one row per second for the CURRENTLY-RUNNING window of each timeframe to:
  --data-dir-5m   (default /root/data_okx_btc_5m)
  --data-dir-15m  (default /root/data_okx_btc_15m)

Strike (target_price) for an UPDOWN market = the price at window open, captured
from Binance at the first second we see the window. distance_signed = binance - strike.

Settlement oracle is OKX's own index — a fresh source distinct from Chainlink
(Polymarket), Binance (Predict/Limitless), Kaiko (Gemini), Kalshi index.
"""
import argparse
import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta

OKX_TICKERS = "https://www.okx.com/api/v5/market/tickers?instType=EVENTS"
BINANCE_PX = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

HEADER = [
    "ts", "epoch_sec", "inst_id", "market_open_epoch", "market_close_epoch",
    "sec_from_open", "up_ask", "up_bid", "down_ask", "down_bid",
    "binance_now", "target_price", "distance_signed", "distance_abs",
]


def now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "okx-rec/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_binance_price():
    try:
        d = http_json(BINANCE_PX)
        return float(d["price"])
    except Exception:
        return None


def parse_instid_window(inst_id):
    """BTC-UPDOWN-5MIN-260528-1305-1310 -> (open_epoch_utc, close_epoch_utc, tf).
    Times are UTC+8 (Singapore)."""
    parts = inst_id.split("-")
    if len(parts) != 6:
        return None
    _, _, tf, ymd, hm_start, hm_end = parts
    try:
        yy = int(ymd[0:2]); mm = int(ymd[2:4]); dd = int(ymd[4:6])
        sh = int(hm_start[0:2]); smin = int(hm_start[2:4])
        eh = int(hm_end[0:2]); emin = int(hm_end[2:4])
    except (ValueError, IndexError):
        return None
    tz8 = timezone(timedelta(hours=8))
    start = datetime(2000 + yy, mm, dd, sh, smin, tzinfo=tz8)
    # end may roll past midnight
    end = datetime(2000 + yy, mm, dd, eh, emin, tzinfo=tz8)
    if end <= start:
        end += timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()), tf


def f(v):
    try:
        if v in (None, "", "0", 0):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


class Writer:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.combined = os.path.join(data_dir, "combined_per_second.csv")
        self.markets = os.path.join(data_dir, "markets.csv")
        if not os.path.exists(self.combined) or os.path.getsize(self.combined) == 0:
            with open(self.combined, "w", newline="") as fh:
                csv.writer(fh).writerow(HEADER)
        if not os.path.exists(self.markets) or os.path.getsize(self.markets) == 0:
            with open(self.markets, "w", newline="") as fh:
                csv.writer(fh).writerow(["ts", "inst_id", "market_open_epoch",
                                         "market_close_epoch", "target_price"])
        self.seen_markets = set()

    def record_market(self, inst_id, oe, ce, strike):
        if inst_id in self.seen_markets:
            return
        self.seen_markets.add(inst_id)
        with open(self.markets, "a", newline="") as fh:
            csv.writer(fh).writerow([now_iso(), inst_id, oe, ce, strike])

    def write_row(self, row):
        with open(self.combined, "a", newline="") as fh:
            csv.writer(fh).writerow(row)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir-5m", default="/root/data_okx_btc_5m")
    p.add_argument("--data-dir-15m", default="/root/data_okx_btc_15m")
    p.add_argument("--poll", type=float, default=1.0)
    args = p.parse_args()

    w5 = Writer(args.data_dir_5m)
    w15 = Writer(args.data_dir_15m)
    strikes = {}  # inst_id -> strike captured near open

    print(f"[{now_iso()}] OKX_RECORDER starting; 5m={args.data_dir_5m} 15m={args.data_dir_15m}", flush=True)

    while True:
        loop_start = time.time()
        try:
            data = http_json(OKX_TICKERS)
            rows = data.get("data", []) if isinstance(data, dict) else []
            binance = get_binance_price()
            now = time.time()

            for r in rows:
                inst = r.get("instId", "")
                if not inst.startswith("BTC-UPDOWN-"):
                    continue
                parsed = parse_instid_window(inst)
                if not parsed:
                    continue
                oe, ce, tf = parsed
                # only the currently-running window
                if not (oe <= now < ce):
                    continue
                sec_from_open = int(now - oe)
                up_ask = f(r.get("askPx"))
                up_bid = f(r.get("bidPx"))
                down_ask = (1 - up_bid) if up_bid is not None else None
                down_bid = (1 - up_ask) if up_ask is not None else None

                # capture strike near open
                if inst not in strikes and sec_from_open <= 3 and binance is not None:
                    strikes[inst] = binance
                strike = strikes.get(inst)
                dist_signed = (binance - strike) if (binance is not None and strike is not None) else None
                dist_abs = abs(dist_signed) if dist_signed is not None else None

                w = w5 if tf == "5MIN" else (w15 if tf == "15MIN" else None)
                if w is None:
                    continue
                if strike is not None:
                    w.record_market(inst, oe, ce, strike)
                w.write_row([
                    now_iso(), int(now), inst, oe, ce, sec_from_open,
                    up_ask, up_bid, down_ask, down_bid,
                    binance, strike, dist_signed, dist_abs,
                ])
        except Exception as e:
            print(f"[{now_iso()}] ERROR {type(e).__name__}: {e}", flush=True)

        elapsed = time.time() - loop_start
        time.sleep(max(0.1, args.poll - elapsed))


if __name__ == "__main__":
    main()
