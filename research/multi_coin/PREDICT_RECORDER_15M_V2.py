#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PREDICT_RECORDER_15M_V2.py
=========================

Reliable 1-hour BTC market recorder for Predict.fun.

Improvements over V1:
- Slug-based market identification (no heuristic guessing)
- Computes current slug from clock time
- Extracts marketId from og:image meta tag via curl_cffi (bypasses Cloudflare)
- Subscribes to predictOrderbook/{marketId} specifically
- Strike computed from Binance first tick at market open
- Auto rollover at hour boundary

Output:
  /root/data_predict_btc_15m/
    combined_per_second.csv  - tick-by-tick orderbook
    markets.csv              - market lifecycle log
    events.csv               - errors, reconnects, rollovers
    market_outcomes.csv      - final settlement of each market

Run:
    python3 PREDICT_RECORDER_15M_V2.py --data-dir /root/data_predict_btc_15m
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
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

import websockets
from curl_cffi import requests as cffi_requests

WS_URL = "wss://ws.predict.fun/ws"
PAGE_URL_TEMPLATE = "https://predict.fun/market/{slug}"
POLY_BINANCE_TICKS = "/root/data_btc_15m_research/binance_ticks.csv"

DATA_DIR = "/root/data_predict_btc_15m"

# State
SHOULD_STOP = False
CURRENT_MARKET_ID: Optional[int] = None
CURRENT_SLUG: Optional[str] = None
CURRENT_MARKET_OPEN_EPOCH: Optional[int] = None
CURRENT_STRIKE: Optional[float] = None
LATEST_OB: Optional[dict] = None
LATEST_OB_TS: float = 0.0


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def now_epoch() -> int:
    return int(time.time())


# Hour-to-12hr-clock helpers for ET slug
HOUR_TO_TAG = {
    0: "12am", 1: "1am", 2: "2am", 3: "3am", 4: "4am", 5: "5am",
    6: "6am", 7: "7am", 8: "8am", 9: "9am", 10: "10am", 11: "11am",
    12: "12pm", 13: "1pm", 14: "2pm", 15: "3pm", 16: "4pm", 17: "5pm",
    18: "6pm", 19: "7pm", 20: "8pm", 21: "9pm", 22: "10pm", 23: "11pm",
}

MONTH_TO_TAG = {
    1: "january", 2: "february", 3: "march", 4: "april", 5: "may",
    6: "june", 7: "july", 8: "august", 9: "september", 10: "october",
    11: "november", 12: "december",
}


def utc_to_et_dst(utc_dt: datetime) -> datetime:
    """Approximate US Eastern Time with DST. Mar-Nov = EDT (-4), else EST (-5).
    Refine if exact DST transitions matter at boundary days."""
    month = utc_dt.month
    if 3 <= month <= 11:
        return utc_dt - timedelta(hours=4)
    return utc_dt - timedelta(hours=5)


def slug_for_1h_market_open(market_open_epoch: int) -> str:
    """Compute Predict.fun slug for a market that opens at the given UTC epoch.
    Slug format observed: bitcoin-up-or-down-{month}-{day}-{year}-{hour}{am|pm}-et
    where hour is in ET (UTC-4 in summer, UTC-5 in winter)."""
    utc_dt = datetime.fromtimestamp(market_open_epoch, tz=timezone.utc).replace(tzinfo=None)
    et_dt = utc_to_et_dst(utc_dt)
    month_tag = MONTH_TO_TAG[et_dt.month]
    hour_tag = HOUR_TO_TAG[et_dt.hour]
    return f"bitcoin-up-or-down-{month_tag}-{et_dt.day}-{et_dt.year}-{hour_tag}-et"


def current_1h_market_open() -> int:
    """Floor current time to the most recent hour boundary (UTC)."""
    return (now_epoch() // 3600) * 3600
def slug_for_15m_market_open(market_open_epoch: int) -> str:
    """Predict.fun 15m slug uses same pattern as Polymarket: btc-updown-15m-{epoch}"""
    return f"btc-updown-15m-{market_open_epoch}"


def current_15m_market_open() -> int:
    """Floor current time to nearest 15-min boundary (UTC)."""
    return (now_epoch() // 900) * 900




def fetch_market_metadata(slug: str) -> Optional[dict]:
    """Fetch the page and extract market metadata from embedded JSON.
    Returns dict with: marketId, startPrice (strike), endPrice (settlement),
    priceFeedProvider, priceFeedSymbol. Returns None on failure."""
    url = PAGE_URL_TEMPLATE.format(slug=slug)
    try:
        r = cffi_requests.get(url, impersonate="chrome120", timeout=15)
        if r.status_code != 200:
            return None
        text = r.text
        # The page embeds JSON like (with quotes possibly escaped as \"):
        # "marketData":[{"marketId":"292200","priceFeedId":null,"priceFeedSymbol":"BTCUSDT","priceFeedProvider":"BINANCE","startPrice":80527.92,"startPricePublishTime":null,"endPrice":null,...
        # Use Q to match either " or \" (with backslash escape).
        Q = r'\\?"'
        m = re.search(
            rf'{Q}marketData{Q}:\[\{{{Q}marketId{Q}:{Q}(\d+){Q},{Q}priceFeedId{Q}:[^,]+,{Q}priceFeedSymbol{Q}:{Q}([^"\\]+){Q},{Q}priceFeedProvider{Q}:{Q}([^"\\]+){Q},{Q}startPrice{Q}:([\d.]+|null),{Q}startPricePublishTime{Q}:[^,]+,{Q}endPrice{Q}:([\d.]+|null)',
            text,
        )
        if not m:
            # Fallback: just get marketId from og:image
            mid_match = re.findall(r"marketId[=](\d+)", text)
            if mid_match:
                return {"marketId": int(mid_match[0]), "startPrice": None, "endPrice": None,
                        "priceFeedProvider": None, "priceFeedSymbol": None}
            return None
        return {
            "marketId": int(m.group(1)),
            "priceFeedSymbol": m.group(2),
            "priceFeedProvider": m.group(3),
            "startPrice": float(m.group(4)) if m.group(4) != "null" else None,
            "endPrice": float(m.group(5)) if m.group(5) != "null" else None,
        }
    except Exception:
        return None


def fetch_market_id_for_slug(slug: str) -> Optional[int]:
    md = fetch_market_metadata(slug)
    return md["marketId"] if md else None


def lookup_binance_at_market_open(market_open_epoch: int) -> Optional[float]:
    """Read the FIRST Binance tick at or after market_open_epoch from poly recorder data.
    This is the strike-defining moment per our 09/05 analysis."""
    if not os.path.exists(POLY_BINANCE_TICKS):
        return None
    open_str = datetime.fromtimestamp(market_open_epoch, tz=timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    try:
        # tail enough rows to cover recent activity
        from subprocess import run, PIPE
        out = run(["tail", "-n", "20000", POLY_BINANCE_TICKS], stdout=PIPE, stderr=PIPE, timeout=5).stdout.decode("utf-8", errors="ignore")
        for line in out.splitlines():
            parts = line.split(",")
            if len(parts) < 4:
                continue
            ts = parts[0]
            if ts >= open_str:
                try:
                    return float(parts[3])
                except Exception:
                    return None
    except Exception:
        return None
    return None


class CsvStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.combined = os.path.join(data_dir, "combined_per_second.csv")
        self.events = os.path.join(data_dir, "events.csv")
        self.markets = os.path.join(data_dir, "markets.csv")
        self.outcomes = os.path.join(data_dir, "market_outcomes.csv")
        self._init(self.combined, [
            "local_ts", "epoch_sec",
            "market_id", "slug",
            "market_open_epoch", "sec_from_open",
            "strike", "binance_now",
            "distance_signed", "distance_abs",
            "yes_bid", "yes_ask",
            "yes_bid_size", "yes_ask_size",
            "yes_bid_usd", "yes_ask_usd",
            "no_ask_implied", "no_bid_implied",
            "no_ask_usd_buyable",
            "spread", "order_count",
        ])
        self._init(self.events, ["local_ts", "event", "detail"])
        self._init(self.markets, ["local_ts", "market_id", "slug", "market_open_epoch", "strike", "first_seen_ts"])
        self._init(self.outcomes, ["local_ts", "market_id", "slug", "market_open_epoch", "strike", "settlement_price", "winner_side"])

    @staticmethod
    def _init(path: str, headers):
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)

    def event(self, ev: str, detail: str = ""):
        with open(self.events, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now_local(), ev, detail])
        print(f"[{now_local()}] {ev}: {detail}", flush=True)

    def market_picked(self, market_id: int, slug: str, market_open: int, strike: Optional[float]):
        with open(self.markets, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now_local(), market_id, slug, market_open, strike or "", now_epoch()])

    def append_combined(self, row):
        with open(self.combined, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def market_outcome(self, market_id: int, slug: str, market_open: int, strike: float, settlement: float, winner: str):
        with open(self.outcomes, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([now_local(), market_id, slug, market_open, strike, settlement, winner])


# ----------------- WebSocket listener -----------------

async def ws_listener(csvs: CsvStore):
    """Connect to WS and keep LATEST_OB updated for the current marketId."""
    global LATEST_OB, LATEST_OB_TS
    while not SHOULD_STOP:
        try:
            async with websockets.connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                if CURRENT_MARKET_ID is None:
                    await asyncio.sleep(1)
                    continue
                topic = f"predictOrderbook/{CURRENT_MARKET_ID}"
                req = {"method": "subscribe", "requestId": 1, "params": [topic]}
                await ws.send(json.dumps(req))
                csvs.event("WS_CONNECT", f"subscribed to {topic}")
                while not SHOULD_STOP:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    if d.get("type") == "M" and d.get("topic", "").startswith("predictOrderbook/"):
                        data = d.get("data", {})
                        if data.get("marketId") == CURRENT_MARKET_ID:
                            LATEST_OB = data
                            LATEST_OB_TS = time.time()
        except Exception as e:
            csvs.event("WS_DISCONNECT", f"{type(e).__name__}: {e}")
            await asyncio.sleep(2)


# ----------------- Recorder loop (writes once per second) -----------------

async def recorder_loop(csvs: CsvStore):
    """Every second, snapshot LATEST_OB and write a row."""
    while not SHOULD_STOP:
        try:
            await asyncio.sleep(1.0)
            if CURRENT_MARKET_ID is None or LATEST_OB is None:
                continue
            ob = LATEST_OB
            ts_sec = now_epoch()
            sec_from_open = ts_sec - (CURRENT_MARKET_OPEN_EPOCH or ts_sec)
            bids = ob.get("bids", []) or []
            asks = ob.get("asks", []) or []
            if not bids or not asks:
                continue
            yes_bid = float(bids[0][0])
            yes_ask = float(asks[0][0])
            yes_bid_size = float(bids[0][1])
            yes_ask_size = float(asks[0][1])
            yes_bid_usd = sum(float(b[0]) * float(b[1]) for b in bids)
            yes_ask_usd = sum(float(a[0]) * float(a[1]) for a in asks)
            no_ask_implied = round(1.0 - yes_bid, 4)
            no_bid_implied = round(1.0 - yes_ask, 4)
            no_ask_usd_buyable = sum(float(b[0]) * float(b[1]) for b in bids)  # NO ask = buying inverse of YES bids
            spread = round(yes_ask - yes_bid, 4)
            order_count = int(ob.get("orderCount", 0) or 0)
            binance_now = lookup_binance_at_market_open(ts_sec) or 0.0
            strike = CURRENT_STRIKE or 0.0
            distance_signed = round(binance_now - strike, 4) if (binance_now and strike) else 0
            distance_abs = abs(distance_signed)
            row = [
                now_local(), ts_sec,
                CURRENT_MARKET_ID, CURRENT_SLUG,
                CURRENT_MARKET_OPEN_EPOCH, sec_from_open,
                strike, binance_now,
                distance_signed, distance_abs,
                yes_bid, yes_ask,
                yes_bid_size, yes_ask_size,
                round(yes_bid_usd, 2), round(yes_ask_usd, 2),
                no_ask_implied, no_bid_implied,
                round(no_ask_usd_buyable, 2),
                spread, order_count,
            ]
            csvs.append_combined(row)
        except Exception as e:
            csvs.event("RECORDER_ERROR", f"{type(e).__name__}: {e}")


# ----------------- Market manager (slug rollover at hour boundary) -----------------

async def market_manager(csvs: CsvStore):
    """At each hour boundary, compute new slug, fetch marketId, settle previous market."""
    global CURRENT_MARKET_ID, CURRENT_SLUG, CURRENT_MARKET_OPEN_EPOCH, CURRENT_STRIKE, LATEST_OB
    while not SHOULD_STOP:
        try:
            target_open = current_15m_market_open()
            if CURRENT_MARKET_OPEN_EPOCH != target_open:
                # Settle previous market if any
                if CURRENT_MARKET_ID is not None and CURRENT_STRIKE is not None:
                    settlement_price = lookup_binance_at_market_open(target_open) or 0.0
                    winner = "UP" if settlement_price > CURRENT_STRIKE else ("DOWN" if settlement_price > 0 else "UNKNOWN")
                    csvs.market_outcome(CURRENT_MARKET_ID, CURRENT_SLUG or "", CURRENT_MARKET_OPEN_EPOCH or 0, CURRENT_STRIKE, settlement_price, winner)
                # Resolve new market
                slug = slug_for_15m_market_open(target_open)
                csvs.event("RESOLVING", f"slug={slug}")
                # Try to fetch full metadata (may fail if page not yet live; retry)
                md = None
                for attempt in range(5):
                    md = fetch_market_metadata(slug)
                    if md and md.get("marketId"):
                        break
                    csvs.event("FETCH_RETRY", f"attempt {attempt+1}/5 slug={slug}")
                    await asyncio.sleep(2)
                if md is None or not md.get("marketId"):
                    csvs.event("RESOLVE_FAILED", f"could not get marketId for slug={slug}")
                    await asyncio.sleep(30)
                    continue
                mid = md["marketId"]
                # Strike: prefer page's startPrice (Predict's official value),
                # fall back to our Binance lookup.
                strike_from_page = md.get("startPrice")
                strike_from_binance = None
                # Wait briefly for Binance ticks to land in poly recorder
                for attempt in range(10):
                    strike_from_binance = lookup_binance_at_market_open(target_open)
                    if strike_from_binance and strike_from_binance > 0:
                        break
                    await asyncio.sleep(1)
                strike = strike_from_page if strike_from_page else strike_from_binance
                CURRENT_MARKET_ID = mid
                CURRENT_SLUG = slug
                CURRENT_MARKET_OPEN_EPOCH = target_open
                CURRENT_STRIKE = strike
                LATEST_OB = None  # force re-subscription via ws_listener
                csvs.market_picked(mid, slug, target_open, strike)
                csvs.event("MARKET_SET",
                           f"id={mid} slug={slug} strike_page={strike_from_page} strike_binance={strike_from_binance} "
                           f"oracle={md.get('priceFeedProvider')} symbol={md.get('priceFeedSymbol')}")
            await asyncio.sleep(10)
        except Exception as e:
            csvs.event("MANAGER_ERROR", f"{type(e).__name__}: {e}")
            await asyncio.sleep(5)


# ----------------- Main -----------------

def handle_signal(*args):
    global SHOULD_STOP
    SHOULD_STOP = True


async def main():
    global DATA_DIR
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default=DATA_DIR)
    args = p.parse_args()
    DATA_DIR = args.data_dir
    csvs = CsvStore(DATA_DIR)
    csvs.event("START", f"PREDICT_RECORDER_15M_V2 starting; data_dir={DATA_DIR}")
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    await asyncio.gather(
        market_manager(csvs),
        ws_listener(csvs),
        recorder_loop(csvs),
    )


if __name__ == "__main__":
    asyncio.run(main())
