#!/usr/bin/env python3
"""Quick check: do Polymarket and Predict.fun WS payloads include a server timestamp?

Subscribes to a current 15-min BTC market on each, captures 30 messages or 60 sec,
dumps raw samples, and prints what timestamp-ish fields exist.
"""

import asyncio
import json
import time
import sys
import urllib.request
import re

import websockets


POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PREDICT_WS = "wss://ws.predict.fun/ws"
UA = "Mozilla/5.0"


def http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def find_poly_token():
    """Read markets.csv tail for current 15m token."""
    try:
        with open("/root/data_btc_15m_research/markets.csv") as f:
            lines = [l for l in f if l.strip()]
        last = lines[-1].split(",")
        slug = last[1]
        token_up = last[5]
        return slug, token_up
    except Exception as e:
        print(f"poly local err: {e}")
    return None, None


def find_predict_market():
    """Read predict latest.json for current 15m market id."""
    try:
        with open("/root/data_predict_btc_15m/latest.json") as f:
            d = json.loads(f.read())
        return d.get("market_id")
    except Exception as e:
        print(f"predict local err: {e}")
    return None


def find_timestamp_fields(obj, prefix=""):
    """Recursively find any field that looks like a timestamp."""
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            kl = k.lower()
            if any(t in kl for t in ["timestamp", "time", "ts", "updated", "sentat", "publishtime", "eventtime"]):
                if isinstance(v, (str, int, float)):
                    found[full] = v
            if isinstance(v, (dict, list)):
                found.update(find_timestamp_fields(v, full))
    elif isinstance(obj, list) and obj:
        found.update(find_timestamp_fields(obj[0], f"{prefix}[0]"))
    return found


async def poly_probe(out):
    slug, token = find_poly_token()
    out.write(f"\n=== POLYMARKET ===\nslug={slug}\ntoken={token}\n")
    if not token:
        out.write("could not find token, skipping\n")
        return

    try:
        async with websockets.connect(POLY_WS, open_timeout=10, ping_interval=20) as ws:
            sub = {"assets_ids": [str(token)], "type": "market",
                   "custom_feature_enabled": True}
            await ws.send(json.dumps(sub))
            seen = 0
            deadline = time.time() + 60
            while seen < 30 and time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    out.write("[poly] no message in 5s\n")
                    continue
                local_ms = int(time.time() * 1000)
                try:
                    data = json.loads(msg)
                except Exception:
                    out.write(f"[poly] non-json: {msg[:200]}\n")
                    continue
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    seen += 1
                    if seen <= 3:
                        out.write(f"\n[poly raw msg #{seen}] local_ms={local_ms}\n")
                        out.write(json.dumps(entry, indent=2)[:2000] + "\n")
                    tfields = find_timestamp_fields(entry)
                    if tfields and seen <= 5:
                        out.write(f"[poly msg #{seen}] timestamp_fields={tfields}\n")
                if seen >= 30:
                    break
            out.write(f"\npoly: total msgs={seen}\n")
    except Exception as e:
        out.write(f"poly error: {type(e).__name__}: {e}\n")


async def predict_probe(out):
    mid = find_predict_market()
    out.write(f"\n=== PREDICT.FUN ===\nmarket_id={mid}\n")
    if not mid:
        out.write("could not find market, skipping\n")
        return

    try:
        async with websockets.connect(PREDICT_WS, open_timeout=10, ping_interval=20) as ws:
            sub = {"method": "subscribe", "requestId": 1,
                   "params": [f"predictOrderbook/{mid}"]}
            await ws.send(json.dumps(sub))
            seen = 0
            deadline = time.time() + 60
            while seen < 30 and time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    out.write("[predict] no message in 5s\n")
                    continue
                local_ms = int(time.time() * 1000)
                try:
                    data = json.loads(msg)
                except Exception:
                    out.write(f"[predict] non-json: {msg[:200]}\n")
                    continue
                seen += 1
                if seen <= 3:
                    out.write(f"\n[predict raw msg #{seen}] local_ms={local_ms}\n")
                    out.write(json.dumps(data, indent=2)[:2000] + "\n")
                tfields = find_timestamp_fields(data)
                if tfields and seen <= 5:
                    out.write(f"[predict msg #{seen}] timestamp_fields={tfields}\n")
            out.write(f"\npredict: total msgs={seen}\n")
    except Exception as e:
        out.write(f"predict error: {type(e).__name__}: {e}\n")


async def main():
    out = open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ts_check.txt", "w")
    await poly_probe(out)
    await predict_probe(out)
    out.close()


if __name__ == "__main__":
    asyncio.run(main())
