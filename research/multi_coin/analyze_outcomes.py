# -*- coding: utf-8 -*-
"""analyze_outcomes.py — analyze per-market outcomes across 7 coins.
Runs locally on already-downloaded market_outcomes_<coin>.csv files."""

import csv, os, math
from datetime import datetime, timedelta
from collections import defaultdict

COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]
DIR = os.path.dirname(os.path.abspath(__file__))
NYC_OFFSET_HOURS = -4   # ET in May (EDT)

def load(coin):
    path = os.path.join(DIR, f"market_outcomes_{coin}.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                r["market_epoch"] = int(r["market_epoch"])
            except Exception:
                continue
            rows.append(r)
    return rows

def winner(r):
    # normalize: 'UP'/'Up' -> 'UP', 'DOWN'/'Down' -> 'DOWN'
    w = (r.get("winner_side") or "").strip().upper()
    if w in ("UP", "DOWN"): return w
    return None

def nyc_hour(epoch):
    return (datetime.utcfromtimestamp(epoch) + timedelta(hours=NYC_OFFSET_HOURS)).hour

def nyc_date(epoch):
    return (datetime.utcfromtimestamp(epoch) + timedelta(hours=NYC_OFFSET_HOURS)).strftime("%Y-%m-%d")

# ============================================================
# load data
# ============================================================
data = {c: load(c) for c in COINS}
print("=" * 78)
print("MULTI-COIN OUTCOME ANALYSIS")
print("=" * 78)
for c in COINS:
    n = len(data[c])
    if n:
        print(f"  {c:5s}: {n:3d} markets   {data[c][0]['local_ts']} ... {data[c][-1]['local_ts']}")
    else:
        print(f"  {c:5s}: NO DATA")

# ============================================================
# 1. PER-COIN OVERALL WIN RATE
# ============================================================
print()
print("=" * 78)
print("1. OVERALL WIN RATE per coin")
print("=" * 78)
print(f"{'coin':6s} {'total':>6s} {'UP':>6s} {'DN':>6s} {'UP%':>7s} {'DN%':>7s}")
for c in COINS:
    rows = data[c]
    ups = sum(1 for r in rows if winner(r) == "UP")
    dns = sum(1 for r in rows if winner(r) == "DOWN")
    t = ups + dns
    if t:
        print(f"{c:6s} {t:6d} {ups:6d} {dns:6d} {100*ups/t:6.1f}% {100*dns/t:6.1f}%")

# ============================================================
# 2. WIN RATE BY NYC HOUR
# ============================================================
print()
print("=" * 78)
print("2. WIN RATE BY NYC HOUR (UP%)")
print("   Each cell = % of markets in that hour that ended UP for that coin")
print("=" * 78)
print(f"{'hour':>4s} {'#mkt':>5s}  " + " ".join(f"{c:>5s}" for c in COINS))

per_hour = {h: {c: {"up":0,"dn":0} for c in COINS} for h in range(24)}
for c in COINS:
    for r in data[c]:
        w = winner(r)
        if not w: continue
        h = nyc_hour(r["market_epoch"])
        if w == "UP": per_hour[h][c]["up"] += 1
        else: per_hour[h][c]["dn"] += 1

for h in range(24):
    counts_per_coin = []
    n_btc = per_hour[h]["btc"]["up"] + per_hour[h]["btc"]["dn"]
    if all(per_hour[h][c]["up"] + per_hour[h][c]["dn"] == 0 for c in COINS):
        continue
    for c in COINS:
        u = per_hour[h][c]["up"]; d = per_hour[h][c]["dn"]; t = u+d
        if t: counts_per_coin.append(f"{100*u/t:4.0f}%")
        else: counts_per_coin.append("  -  ")
    print(f"{h:>3d}h {n_btc:>5d}   " + " ".join(counts_per_coin))

# ============================================================
# 3. CROSS-COIN OUTCOME CORRELATION
# ============================================================
print()
print("=" * 78)
print("3. CROSS-COIN OUTCOME AGREEMENT")
print("   For each pair: in markets where both have outcomes for the same")
print("   epoch, what % of times did they pick the same side?")
print("   100% = always agree (perfectly correlated)")
print("    50% = random (no relationship)")
print("=" * 78)
by_epoch = {c: {r["market_epoch"]: r for r in data[c]} for c in COINS}
print(f"{'pair':14s} {'common':>7s} {'agree':>6s} {'pct':>6s}")
agreement_matrix = {}
for i, a in enumerate(COINS):
    for b in COINS[i+1:]:
        common = set(by_epoch[a].keys()) & set(by_epoch[b].keys())
        agree, total = 0, 0
        for e in common:
            wa, wb = winner(by_epoch[a][e]), winner(by_epoch[b][e])
            if wa and wb:
                total += 1
                if wa == wb: agree += 1
        if total:
            pct = 100 * agree / total
            agreement_matrix[(a,b)] = pct
            print(f"{a:>5s}-{b:5s}    {total:7d} {agree:6d} {pct:5.1f}%")

# ============================================================
# 4. AGREEMENT MATRIX
# ============================================================
print()
print("=" * 78)
print("4. AGREEMENT MATRIX (each cell: % markets where both coins agreed)")
print("=" * 78)
print(f"{'':6s}" + "".join(f"{c:>6s}" for c in COINS))
for a in COINS:
    row = [a]
    for b in COINS:
        if a == b:
            row.append("  -- ")
        else:
            key = (a,b) if (a,b) in agreement_matrix else (b,a)
            v = agreement_matrix.get(key)
            row.append(f"{v:5.0f}%" if v else "  ?  ")
    print(f"{row[0]:6s}" + "".join(f"{x:>6s}" for x in row[1:]))

# ============================================================
# 5. SPECIFIC FOCUS — NYC 5-7am
# ============================================================
print()
print("=" * 78)
print("5. FOCUS: NYC 05:00–07:00 (the BTC 'losing hour' question)")
print("   Was that hour also losing/biased for the OTHER coins?")
print("=" * 78)
for h in [4, 5, 6, 7]:
    print(f"\n--- NYC {h:02d}:00 ---")
    print(f"{'coin':6s} {'n':>4s} {'UP':>4s} {'DN':>4s} {'UP%':>6s}")
    for c in COINS:
        u = per_hour[h][c]["up"]; d = per_hour[h][c]["dn"]; t = u+d
        if t:
            print(f"{c:6s} {t:>4d} {u:>4d} {d:>4d} {100*u/t:5.1f}%")

# ============================================================
# 6. DAILY DRIFT
# ============================================================
print()
print("=" * 78)
print("6. DAILY DRIFT — UP rate per (NYC) date per coin")
print("=" * 78)
per_date = defaultdict(lambda: defaultdict(lambda: {"up":0,"dn":0}))
for c in COINS:
    for r in data[c]:
        w = winner(r)
        if not w: continue
        d = nyc_date(r["market_epoch"])
        if w == "UP": per_date[d][c]["up"] += 1
        else: per_date[d][c]["dn"] += 1
dates = sorted(per_date.keys())
print(f"{'date':12s} " + " ".join(f"{c:>5s}" for c in COINS))
for d in dates:
    cells = []
    for c in COINS:
        u = per_date[d][c]["up"]; dn = per_date[d][c]["dn"]; t = u+dn
        if t: cells.append(f"{100*u/t:4.0f}%")
        else: cells.append("  -  ")
    print(f"{d}  " + " ".join(cells))

print()
print("=" * 78)
print("END")
print("=" * 78)
