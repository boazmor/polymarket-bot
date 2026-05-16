#!/usr/bin/env python3
"""arb_v9_dip_research_5m.py — Research-only recorder for dip-buy on 5-min markets.

Does NOT trade. Records for each 5-min window:
  - For each second in the first 60 seconds:
    - lowest ask seen across all 3 platforms on each side
    - which platform offered it
    - depth at that ask
  - After window settles: outcome (UP / DOWN winner)

Output: /root/arb_v9_research_5m.csv
Columns: window_epoch, sec_in_window, plat, side, ask, depth_usd, outcome

This lets us sweep parameters offline. For any combination of
(price_threshold, time_threshold), compute:
  - how many windows would trigger a buy
  - average buy price (and what bid is needed for X% profit)
  - hit rate (would price reach profit target before window end? or
    would winning side pay $1)
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib3
from datetime import datetime, timezone

HTTP_POOL = urllib3.PoolManager(
    maxsize=10, block=False,
    retries=urllib3.Retry(connect=1, read=0, backoff_factor=0.05),
    headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
)

sys.path.insert(0, "/root")
from ws_feeds.state import STATE
from ws_feeds.runner import start_all_feeds

from dotenv import load_dotenv
load_dotenv("/root/live/btc_5m/.env", override=True)

WINDOW_SEC = 300
OBSERVATION_SEC = 60  # record first N seconds of each window
RESEARCH_CSV = "/root/arb_v9_research_5m.csv"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_row(path, row, header=None):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new and header:
            w.writerow(header)
        w.writerow(row)


def read_state_snapshot():
    poly = STATE.get("poly")
    pr = STATE.get("predict")
    lim = STATE.get("lim")
    return [
        ("poly", "yes", poly.best_ask, poly.best_bid, poly.ask_depth_usd, poly.connected),
        ("poly", "no",  poly.no_best_ask, round(1.0 - poly.best_ask, 4) if poly.best_ask > 0 else 0, poly.no_ask_depth_usd, poly.connected),
        ("pr",   "yes", pr.best_ask, pr.best_bid, pr.ask_depth_usd, pr.connected),
        ("pr",   "no",  pr.no_best_ask, round(1.0 - pr.best_ask, 4) if pr.best_ask > 0 else 0, pr.no_ask_depth_usd, pr.connected),
        ("lim",  "yes", lim.best_ask, lim.best_bid, lim.ask_depth_usd, lim.connected),
        ("lim",  "no",  lim.no_best_ask, round(1.0 - lim.best_ask, 4) if lim.best_ask > 0 else 0, lim.no_ask_depth_usd, lim.connected),
    ]


def fetch_poly_market_5m(epoch):
    slug = f"btc-updown-5m-{epoch}"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    try:
        r = HTTP_POOL.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=7.0))
        if r.status != 200:
            return None
        data = json.loads(r.data)
        if not data:
            return None
        m = data[0]
        token_ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
        outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
        return {
            "slug": m["slug"],
            "up_token": token_ids[outcomes.index("Up")],
            "down_token": token_ids[outcomes.index("Down")],
        }
    except Exception:
        return None


def get_predict_market_id():
    try:
        with open("/root/data_predict_btc_5m/markets.csv") as f:
            rows = list(csv.reader(f))
        return int(rows[-1][1])
    except Exception:
        return None


def get_lim_slug_5m():
    try:
        with open("/root/data_limitless_btc_5m/markets.csv") as f:
            rows = list(csv.reader(f))
        return rows[-1][2]
    except Exception:
        return None


def fetch_outcome_for_market(slug):
    """Try Polymarket gamma API for market closed/winner."""
    try:
        url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
        r = HTTP_POOL.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=7.0))
        if r.status != 200:
            return None
        data = json.loads(r.data)
        if not data:
            return None
        m = data[0]
        outcome_prices = m.get("outcomePrices")
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if not outcome_prices or not outcomes:
            return None
        # winner is whichever outcome has price = 1.0 (or near it)
        for o, p in zip(outcomes, outcome_prices):
            try:
                if float(p) >= 0.99:
                    return "UP" if o == "Up" else "DOWN"
            except (ValueError, TypeError):
                pass
        return None
    except Exception:
        return None


def main():
    print(f"=== arb_v9_dip_research_5m starting at {now_iso()} ===", flush=True)
    print(f"observing first {OBSERVATION_SEC}s of each 5-min window")
    print(f"output: {RESEARCH_CSV}")

    HEADER = ["ts", "window_epoch", "sec_in_window", "plat", "side",
              "ask", "bid", "ask_depth_usd", "connected", "poly_slug",
              "outcome_marker"]

    market_holders = {
        "poly_up_tokens": [], "poly_down_tokens": [],
        "predict_market_id": None, "lim_slug": None,
    }
    feeds = start_all_feeds(
        poly_tokens_provider=lambda: (market_holders["poly_up_tokens"], market_holders["poly_down_tokens"]),
        predict_market_id_provider=lambda: market_holders["predict_market_id"],
        limitless_slug_provider=lambda: market_holders["lim_slug"],
        state=STATE,
    )
    print(f"--- {len(feeds)} WS feeds started ---", flush=True)

    current_epoch = None
    current_poly_slug = None
    completed_windows = []  # list of (epoch, slug) awaiting outcome resolution
    POLL = 1.0  # 1-sec sampling is enough for this research

    while True:
        try:
            now_e = int(time.time())
            window_epoch = (now_e // WINDOW_SEC) * WINDOW_SEC
            sec_in_window = now_e - window_epoch

            if window_epoch != current_epoch:
                # New window - rotate
                if current_epoch is not None and current_poly_slug is not None:
                    completed_windows.append((current_epoch, current_poly_slug))
                current_epoch = window_epoch
                pm = fetch_poly_market_5m(window_epoch)
                current_poly_slug = pm["slug"] if pm else None
                if pm:
                    market_holders["poly_up_tokens"] = [pm["up_token"]]
                    market_holders["poly_down_tokens"] = [pm["down_token"]]
                pid = get_predict_market_id()
                if pid:
                    market_holders["predict_market_id"] = pid
                lim_slug = get_lim_slug_5m()
                if lim_slug:
                    market_holders["lim_slug"] = lim_slug
                print(f"\n[{now_iso()[11:19]}] NEW_WINDOW {window_epoch} poly={current_poly_slug} "
                      f"predict={pid} lim={lim_slug}", flush=True)

                # Try to resolve outcomes for windows that ended >= 60s ago
                still_pending = []
                for ep, slug in completed_windows:
                    if now_e - ep < 60:
                        still_pending.append((ep, slug))
                        continue
                    outcome = fetch_outcome_for_market(slug)
                    if outcome:
                        # write an outcome marker row
                        append_row(RESEARCH_CSV, [
                            now_iso(), ep, -1, "outcome", "n/a",
                            "", "", "", "", slug, outcome,
                        ], header=HEADER)
                        print(f"  OUTCOME {ep} ({slug}) = {outcome}", flush=True)
                    else:
                        still_pending.append((ep, slug))
                completed_windows = still_pending

            # Record snapshot if within observation window
            if sec_in_window < OBSERVATION_SEC and current_poly_slug:
                snap = read_state_snapshot()
                ts = now_iso()
                for plat, side, ask, bid, depth, conn in snap:
                    append_row(RESEARCH_CSV, [
                        ts, window_epoch, sec_in_window, plat, side,
                        round(ask, 4) if ask else 0,
                        round(bid, 4) if bid else 0,
                        round(depth, 2) if depth else 0,
                        int(conn), current_poly_slug, "",
                    ], header=HEADER)
            time.sleep(POLL)

        except KeyboardInterrupt:
            print("\nstopped by user", flush=True)
            break
        except Exception as e:
            print(f"main loop err: {type(e).__name__}: {e}", flush=True)
            time.sleep(POLL)


if __name__ == "__main__":
    main()
