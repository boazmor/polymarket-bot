# -*- coding: utf-8 -*-
"""probe_kalshi.py — first contact with Kalshi public API.
Side-by-side comparison of the SAME 15-minute BTC market on:
  Kalshi  vs  Polymarket
to see if there's price divergence (= arbitrage opportunity)."""

import time, json
from datetime import datetime, timezone
import requests

print("=" * 70)
print("KALSHI vs POLYMARKET — 15min BTC market comparison")
print("=" * 70)

# ============================================================
# 1. KALSHI — list current BTC 15min markets
# ============================================================
print("\n--- KALSHI ---")
try:
    r = requests.get(
        "https://api.elections.kalshi.com/trade-api/v2/markets",
        params={"series_ticker": "KXBTC15M", "status": "open", "limit": 5},
        timeout=10
    )
    print(f"HTTP {r.status_code}")
    data = r.json()
    markets = data.get("markets", [])
    print(f"open BTC-15m markets on Kalshi: {len(markets)}")
    for m in markets:
        print(f"  ticker:        {m.get('ticker')}")
        print(f"  title:         {m.get('title')}")
        print(f"  strike/target: {m.get('strike_target') or m.get('strike') or '?'}")
        print(f"  yes_bid:       {m.get('yes_bid_dollars') or m.get('yes_bid', 0)/100 if m.get('yes_bid') else '-'}")
        print(f"  yes_ask:       {m.get('yes_ask_dollars') or m.get('yes_ask', 0)/100 if m.get('yes_ask') else '-'}")
        print(f"  no_bid:        {m.get('no_bid_dollars') or m.get('no_bid', 0)/100 if m.get('no_bid') else '-'}")
        print(f"  no_ask:        {m.get('no_ask_dollars') or m.get('no_ask', 0)/100 if m.get('no_ask') else '-'}")
        print(f"  volume:        {m.get('volume') or m.get('volume_fp', '?')}")
        print(f"  expiration:    {m.get('expiration_time')}")
        print(f"  status:        {m.get('status')}")
        print()
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# ============================================================
# 2. POLYMARKET — current BTC 15min market
# ============================================================
print("\n--- POLYMARKET ---")
try:
    # 15-min epoch alignment
    now_ep = int(time.time())
    ep_15m = (now_ep // 900) * 900
    slug = f"btc-updown-15m-{ep_15m}"
    r2 = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"slug": slug},
        timeout=10
    )
    print(f"slug: {slug}")
    print(f"HTTP {r2.status_code}")
    d2 = r2.json()
    mkts2 = d2 if isinstance(d2, list) else d2.get("markets", [])
    if mkts2:
        m = mkts2[0]
        print(f"  question:    {m.get('question')}")
        print(f"  active:      {m.get('active')}")
        print(f"  closed:      {m.get('closed')}")
        # outcome prices
        outcome_prices = m.get('outcomePrices')
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)
        outcomes = m.get('outcomes')
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if outcome_prices and outcomes:
            for nm, px in zip(outcomes, outcome_prices):
                print(f"  {nm:5s}: ${float(px):.4f}")
        print(f"  volume:      {m.get('volume')}")
        print(f"  liquidity:   {m.get('liquidity')}")
    else:
        print(f"  no market found at slug {slug}")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# ============================================================
# 3. Comparison summary
# ============================================================
print("\n" + "=" * 70)
print("If both above show prices — we have side-by-side data.")
print("Next step: a full recorder that polls both every few seconds.")
print("=" * 70)
