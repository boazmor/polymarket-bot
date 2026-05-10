#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_data.py - independently verify recorder data against platform APIs.

For each platform, fetches the current orderbook via official API or WebSocket
and compares to what's in our recorder CSVs.

Run:
    python3 verify_data.py
"""
import asyncio
import csv
import json
import os
import re
import time
import requests
from curl_cffi import requests as cffi
import websockets


def tail_last(path):
    if not os.path.exists(path): return None
    try:
        with open(path) as f:
            header = f.readline().strip().split(",")
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", errors="ignore").splitlines()
        if not tail: return None
        return dict(zip(header, tail[-1].split(",")))
    except Exception as e:
        return None


def verify_polymarket():
    print("\n=== POLYMARKET ===")
    csv_row = tail_last("/root/data_btc_15m_research/combined_per_second.csv")
    if not csv_row:
        print("  no CSV data")
        return
    slug = csv_row.get("market_slug", "?")
    csv_up_ask = csv_row.get("up_ask", "?")
    csv_up_bid = csv_row.get("up_bid", "?")
    csv_dn_ask = csv_row.get("down_ask", "?")
    csv_dn_bid = csv_row.get("down_bid", "?")

    # Fetch the page for strike (priceToBeat)
    try:
        page = cffi.get(f"https://polymarket.com/event/{slug}", impersonate="chrome120", timeout=10).text
        m = re.search(r'"priceToBeat":\s*([\d.]+)', page)
        page_strike = m.group(1) if m else "?"
    except Exception as e:
        page_strike = f"err: {e}"

    print(f"  slug:           {slug}")
    print(f"  CSV recorder:   strike_chainlink={csv_row.get('target_chainlink_at_open','?')}")
    print(f"                  UP bid={csv_up_bid} ask={csv_up_ask}")
    print(f"                  DOWN bid={csv_dn_bid} ask={csv_dn_ask}")
    print(f"  PAGE official:  priceToBeat={page_strike}")
    print(f"  CSV ts:         {csv_row.get('local_ts','?')[:19]}")


def verify_predict_15m():
    print("\n=== PREDICT.FUN 15min ===")
    csv_row = tail_last("/root/data_predict_btc_15m/combined_per_second.csv")
    if not csv_row:
        print("  no CSV data")
        return
    slug = csv_row.get("slug", "?")
    market_id = csv_row.get("market_id", "?")
    csv_yes_ask = csv_row.get("yes_ask", "?")
    csv_yes_bid = csv_row.get("yes_bid", "?")
    csv_strike = csv_row.get("strike", "?")

    # Fetch page for official strike
    try:
        page = cffi.get(f"https://predict.fun/market/{slug}", impersonate="chrome120", timeout=10).text
        m = re.search(r'"startPrice":\s*([\d.]+)', page)
        page_strike = m.group(1) if m else "?"
        m2 = re.search(r'"marketId":\s*"?(\d+)"?', page)
        page_mid = m2.group(1) if m2 else "?"
    except Exception as e:
        page_strike = f"err: {e}"
        page_mid = "?"

    # Independent WS fetch of orderbook
    ws_bid = ws_ask = "?"
    try:
        async def fetch_ws():
            global ws_bid, ws_ask
            async with websockets.connect("wss://ws.predict.fun/ws", open_timeout=5) as ws:
                await ws.send(json.dumps({"method":"subscribe","requestId":1,"params":[f"predictOrderbook/{market_id}"]}))
                end = time.time() + 4
                while time.time() < end:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2)
                        d = json.loads(msg)
                        if d.get("type") == "M" and "Orderbook" in d.get("topic",""):
                            data = d.get("data", {})
                            if str(data.get("marketId")) == str(market_id):
                                bids = data.get("bids", []) or []
                                asks = data.get("asks", []) or []
                                if bids and asks:
                                    return float(bids[0][0]), float(asks[0][0])
                    except asyncio.TimeoutError: pass
            return None, None
        ws_bid, ws_ask = asyncio.run(fetch_ws())
    except Exception as e:
        ws_bid = ws_ask = f"err"

    print(f"  slug:           {slug}")
    print(f"  CSV recorder:   marketId={market_id} strike={csv_strike}")
    print(f"                  YES bid={csv_yes_bid} ask={csv_yes_ask}")
    print(f"  PAGE official:  marketId={page_mid} startPrice={page_strike}")
    print(f"  WS independent: YES bid={ws_bid} ask={ws_ask}")
    print(f"  CSV ts:         {csv_row.get('local_ts','?')[:19]}")


def verify_kalshi():
    print("\n=== KALSHI ===")
    csv_row = tail_last("/root/data_kalshi_btc_15m/combined_per_second.csv")
    if not csv_row:
        print("  no CSV data")
        return
    ticker = csv_row.get("market_ticker", "?")
    try:
        resp = requests.get(f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}", timeout=5)
        d = resp.json()
        api_strike = d.get("market", {}).get("floor_strike", "?")
        api_yes_bid = d.get("market", {}).get("yes_bid", "?")
        api_yes_ask = d.get("market", {}).get("yes_ask", "?")
    except Exception as e:
        api_strike = api_yes_bid = api_yes_ask = f"err: {e}"
    print(f"  ticker:         {ticker}")
    print(f"  CSV recorder:   strike={csv_row.get('floor_strike','?')} YES bid={csv_row.get('yes_bid','?')} ask={csv_row.get('yes_ask','?')}")
    print(f"  API official:   strike={api_strike} YES bid={api_yes_bid} ask={api_yes_ask}")
    print(f"  CSV ts:         {csv_row.get('local_ts','?')[:19]}")


def verify_gemini():
    print("\n=== GEMINI ===")
    csv_row = tail_last("/root/data_gemini_btc_15m/combined_per_second.csv")
    if not csv_row:
        print("  no CSV data")
        return
    ticker = csv_row.get("ticker", "?")
    try:
        resp = requests.get(f"https://api.gemini.com/v1/predictions/markets/{ticker}", timeout=5)
        d = resp.json()
        api_strike = d.get("strike", "?")
        api_yes_bid = d.get("yes_bid", "?")
        api_yes_ask = d.get("yes_ask", "?")
    except Exception as e:
        api_strike = api_yes_bid = api_yes_ask = f"err: {e}"
    print(f"  ticker:         {ticker}")
    print(f"  CSV recorder:   strike={csv_row.get('strike','?')} YES bid={csv_row.get('yes_bid','?')} ask={csv_row.get('yes_ask','?')}")
    print(f"  API official:   strike={api_strike} YES bid={api_yes_bid} ask={api_yes_ask}")
    print(f"  CSV ts:         {csv_row.get('local_ts','?')[:19]}")


def main():
    print(f"=== אימות נתונים מול הפלטפורמות, {time.strftime('%H:%M:%S')} ===")
    verify_polymarket()
    verify_predict_15m()
    verify_kalshi()
    verify_gemini()
    print("\n✓ אם הערכים זהים, הריקורדר נכון")
    print("✗ אם יש פער, צריך לתקן את הריקורדר")


if __name__ == "__main__":
    main()
