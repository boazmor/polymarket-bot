#!/usr/bin/env python3
"""Limitless WS feed diagnostic. Run on Helsinki AND USA in parallel.

Subscribes to current BTC 15-min market and logs every orderbookUpdate
event as JSONL with local_ts_ms, msg_size, raw payload (first 2KB), and
any server-side timestamp fields found.

Usage:
    python3 limitless_ws_diag.py --out /root/lim_diag_$(hostname).jsonl --hours 24

Analyze afterward by diffing inter-arrival gaps between Helsinki and USA.
"""

import argparse
import asyncio
import json
import re
import signal
import sys
import time
import urllib.request
import urllib.error

from limitless_sdk.websocket import WebSocketClient, WebSocketConfig


API = "https://api.limitless.exchange"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HEADERS = {"User-Agent": UA}
PAGE_URL = "https://limitless.exchange/markets/btc-15-min-price"
SLUG_REGEX = r"btc-up-or-down-15-min-\d+"


SHOULD_STOP = False


def stop_handler(*_):
    global SHOULD_STOP
    SHOULD_STOP = True


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)


def http_get(path, timeout=10):
    req = urllib.request.Request(f"{API}{path}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except Exception:
        return None, None


def fetch_page_slug():
    try:
        req = urllib.request.Request(PAGE_URL, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        m = re.search(SLUG_REGEX, html)
        return m.group(0) if m else None
    except Exception:
        return None


def find_current_15m_slug():
    status, data = http_get("/markets/active")
    if status == 200 and data:
        for m in data.get("data") or []:
            title = (m.get("title") or "")
            if "BTC" in title.upper() and "15 Min" in title:
                return m.get("slug")
    return fetch_page_slug()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()

    deadline = time.time() + args.hours * 3600
    out = open(args.out, "a", buffering=1)

    start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out.write(json.dumps({
        "type": "start", "iso": start_iso,
        "host": __import__("socket").gethostname(),
        "deadline_s": int(deadline),
    }) + "\n")

    current_slug = None
    backoff = 1.0

    while not SHOULD_STOP and time.time() < deadline:
        try:
            ws = WebSocketClient(WebSocketConfig(auto_reconnect=True))

            @ws.on("orderbookUpdate")
            async def on_ob(data):
                now_ms = int(time.time() * 1000)
                raw = json.dumps(data) if not isinstance(data, str) else data
                size = len(raw)
                ts_fields = {}
                if isinstance(data, dict):
                    for k in ("timestamp", "ts", "serverTime", "server_ts",
                              "updatedAt", "updated_at", "time", "eventTime"):
                        if k in data:
                            ts_fields[k] = data[k]
                    ob = data.get("orderbook") or {}
                    if isinstance(ob, dict):
                        for k in ("timestamp", "ts", "updatedAt"):
                            if k in ob:
                                ts_fields[f"orderbook.{k}"] = ob[k]
                rec = {
                    "type": "msg",
                    "local_ts_ms": now_ms,
                    "size": size,
                    "ts_fields": ts_fields,
                    "sample": raw[:2048],
                }
                out.write(json.dumps(rec) + "\n")

            await ws.connect()
            out.write(json.dumps({
                "type": "connected",
                "local_ts_ms": int(time.time() * 1000),
            }) + "\n")
            backoff = 1.0

            current_slug = find_current_15m_slug()
            if current_slug:
                out.write(json.dumps({
                    "type": "subscribe", "slug": current_slug,
                    "local_ts_ms": int(time.time() * 1000),
                }) + "\n")
                await ws.subscribe("subscribe_market_prices",
                                   {"marketSlugs": [current_slug]})

            last_rotate_check = time.time()
            while not SHOULD_STOP and time.time() < deadline:
                await asyncio.sleep(1.0)
                if time.time() - last_rotate_check > 30:
                    last_rotate_check = time.time()
                    new_slug = find_current_15m_slug()
                    if new_slug and new_slug != current_slug:
                        out.write(json.dumps({
                            "type": "rotate",
                            "old_slug": current_slug, "new_slug": new_slug,
                            "local_ts_ms": int(time.time() * 1000),
                        }) + "\n")
                        try:
                            await ws.unsubscribe("subscribe_market_prices",
                                                 {"marketSlugs": [current_slug]})
                        except Exception:
                            pass
                        await ws.subscribe("subscribe_market_prices",
                                           {"marketSlugs": [new_slug]})
                        current_slug = new_slug

            try:
                await ws.disconnect()
            except Exception:
                pass

        except Exception as e:
            out.write(json.dumps({
                "type": "error",
                "err": f"{type(e).__name__}: {e}",
                "local_ts_ms": int(time.time() * 1000),
            }) + "\n")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    out.write(json.dumps({
        "type": "end",
        "local_ts_ms": int(time.time() * 1000),
    }) + "\n")
    out.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
