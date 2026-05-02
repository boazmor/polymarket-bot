# -*- coding: utf-8 -*-
"""multi_coin_analysis.py — analyze 7-coin recording for:
  1. Per-coin hour-by-hour winner_side (UP/DOWN) bias by NYC time
  2. Cross-coin correlation of market outcomes (do they all win together?)
  3. Cross-coin price-delta correlation + lead-lag (who moves first?)
  4. The specific question: was NYC 06:00 (BTC's losing hour) also losing
     for the other coins on the same day?

Reads /root/data_<coin>_5m_research/market_outcomes.csv (small) for
market-level analysis, and /root/data_<coin>_5m_research/combined_per_second.csv
(large) for tick-level correlation.

Designed to run on the server. Output is a single text report
to /root/multi_coin_report.txt.
"""

import os, csv, sys, json, math
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter

COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb", "hype"]
DATA_BASE = "/root"
REPORT_PATH = "/root/multi_coin_report.txt"

NYC_TZ_OFFSET_HOURS = -4   # ET in May (EDT)

out_lines = []
def W(s=""):
    print(s)
    out_lines.append(s)

# ---------------------------------------------------------------- LOAD OUTCOMES
def load_outcomes(coin):
    path = f"{DATA_BASE}/data_{coin}_5m_research/market_outcomes.csv"
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            try:
                r["market_epoch"] = int(r["market_epoch"])
                r["target_price"] = float(r.get("target_price") or 0)
                r["final_binance_price"] = float(r.get("final_binance_price") or 0) if r.get("final_binance_price") else None
                r["final_distance_signed"] = float(r.get("final_distance_signed") or 0) if r.get("final_distance_signed") else None
            except Exception:
                continue
            rows.append(r)
    return rows

W("=" * 70)
W("MULTI-COIN RECORDING ANALYSIS")
W(f"  generated at: {datetime.now().isoformat()}")
W("=" * 70)

outcomes = {}
for c in COINS:
    rows = load_outcomes(c)
    outcomes[c] = rows
    W(f"  {c:5s}: {len(rows):3d} markets  ({rows[0]['local_ts'] if rows else '-'} ... {rows[-1]['local_ts'] if rows else '-'})")

# ---------------------------------------------------------------- 1. WIN RATE PER COIN
W("")
W("=" * 70)
W("1. PER-COIN WIN RATE (UP vs DOWN)")
W("=" * 70)
W(f"{'coin':6s} {'total':>6s} {'UP':>6s} {'DOWN':>6s} {'UP%':>6s}")
for c in COINS:
    rows = outcomes[c]
    if not rows: continue
    n_up = sum(1 for r in rows if r.get("winner_side") == "Up")
    n_dn = sum(1 for r in rows if r.get("winner_side") == "Down")
    total = n_up + n_dn
    up_pct = 100.0 * n_up / total if total else 0
    W(f"{c:6s} {total:6d} {n_up:6d} {n_dn:6d} {up_pct:5.1f}%")

# ---------------------------------------------------------------- 2. WIN RATE BY NYC HOUR
W("")
W("=" * 70)
W("2. WIN RATE BY NYC HOUR (looking for time-of-day patterns)")
W("=" * 70)
W("hour:  total markets in that hour, then UP% per coin")
W(f"{'hour':>4s} {'n':>4s}  {' '.join(f'{c:>5s}' for c in COINS)}")

# bucket by NYC hour
def nyc_hour(epoch_int):
    dt = datetime.utcfromtimestamp(epoch_int)
    nyc = dt + timedelta(hours=NYC_TZ_OFFSET_HOURS)
    return nyc.hour

per_hour = defaultdict(lambda: defaultdict(lambda: {"up":0, "dn":0}))
for c in COINS:
    for r in outcomes[c]:
        h = nyc_hour(r["market_epoch"])
        if r.get("winner_side") == "Up":
            per_hour[h][c]["up"] += 1
        elif r.get("winner_side") == "Down":
            per_hour[h][c]["dn"] += 1

for h in sorted(per_hour.keys()):
    cols = []
    n_total = 0
    for c in COINS:
        u = per_hour[h][c]["up"]
        d = per_hour[h][c]["dn"]
        t = u + d
        if t == 0:
            cols.append("  -  ")
        else:
            cols.append(f"{100*u/t:4.0f}%")
        if c == "btc":
            n_total = t
    W(f"{h:>3d}h {n_total:>4d}  {' '.join(cols)}")

# ---------------------------------------------------------------- 3. WINNER CORRELATION
W("")
W("=" * 70)
W("3. CROSS-COIN WINNER CORRELATION")
W("   For each pair of coins (A, B): in markets where they both have")
W("   outcomes for the same epoch, what % do they have the same winner?")
W("   100% = always agree (perfectly correlated). 50% = random.")
W("=" * 70)

# Index outcomes by epoch
by_epoch = {c: {r["market_epoch"]: r for r in outcomes[c]} for c in COINS}

W(f"{'pair':12s} {'common':>6s} {'agree':>6s} {'pct':>6s}")
for i, a in enumerate(COINS):
    for b in COINS[i+1:]:
        common_epochs = set(by_epoch[a].keys()) & set(by_epoch[b].keys())
        if not common_epochs: continue
        agree = 0
        total = 0
        for e in common_epochs:
            wa = by_epoch[a][e].get("winner_side")
            wb = by_epoch[b][e].get("winner_side")
            if wa and wb and wa not in ("", None) and wb not in ("", None):
                total += 1
                if wa == wb:
                    agree += 1
        if total:
            pct = 100 * agree / total
            W(f"{a}-{b:8s} {total:6d} {agree:6d} {pct:5.1f}%")

# ---------------------------------------------------------------- 4. NYC 06:00 ANALYSIS
W("")
W("=" * 70)
W("4. SPECIFIC: NYC 05:00–06:00 hour analysis")
W("   (Question: BTC traditionally LOSES at this hour. Do other coins too?)")
W("=" * 70)
for h_target in [5, 6, 7]:
    W(f"\n--- NYC hour {h_target:02d}:00 ---")
    W(f"{'coin':6s} {'n':>4s} {'UP':>4s} {'DN':>4s} {'UP%':>6s} {'DN%':>6s}")
    for c in COINS:
        u = per_hour[h_target][c]["up"]
        d = per_hour[h_target][c]["dn"]
        t = u + d
        if t == 0:
            W(f"{c:6s} {'-':>4s}")
        else:
            W(f"{c:6s} {t:>4d} {u:>4d} {d:>4d} {100*u/t:5.1f}% {100*d/t:5.1f}%")

# ---------------------------------------------------------------- 5. PRICE CORRELATION (sampled)
W("")
W("=" * 70)
W("5. CROSS-COIN PRICE-DELTA CORRELATION")
W("   Sample 1 in N rows from combined_per_second.csv to keep it fast.")
W("   Then for each pair: Pearson correlation of binance_price log-returns.")
W("=" * 70)

import math

def load_prices(coin, sample_every=10):
    """Load (epoch_sec, price) pairs from combined_per_second, sampled."""
    path = f"{DATA_BASE}/data_{coin}_5m_research/combined_per_second.csv"
    if not os.path.exists(path):
        return []
    pts = []
    try:
        with open(path, newline='') as f:
            rd = csv.DictReader(f)
            for i, r in enumerate(rd):
                if i % sample_every != 0:
                    continue
                try:
                    es = int(r["epoch_sec"])
                    bp = float(r["binance_price"]) if r.get("binance_price") else None
                    if bp and bp > 0:
                        pts.append((es, bp))
                except Exception:
                    continue
    except Exception as e:
        W(f"  load_prices({coin}) failed: {e}")
    return pts

W("loading sampled prices for each coin (every 10s)...")
prices = {}
for c in COINS:
    pts = load_prices(c, sample_every=10)
    prices[c] = dict(pts)  # epoch_sec -> price
    W(f"  {c}: {len(pts)} samples")

# Compute log-returns aligned by epoch_sec
def aligned_returns(c1, c2):
    p1 = prices[c1]
    p2 = prices[c2]
    common_secs = sorted(set(p1.keys()) & set(p2.keys()))
    r1, r2 = [], []
    last1, last2 = None, None
    for s in common_secs:
        v1 = p1[s]
        v2 = p2[s]
        if last1 is not None and last2 is not None and last1 > 0 and last2 > 0:
            r1.append(math.log(v1 / last1))
            r2.append(math.log(v2 / last2))
        last1, last2 = v1, v2
    return r1, r2

def pearson(x, y):
    n = len(x)
    if n < 2: return 0
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a-mx)**2 for a in x)
    syy = sum((a-my)**2 for a in y)
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    if sxx == 0 or syy == 0: return 0
    return sxy / math.sqrt(sxx * syy)

W("")
W(f"{'pair':12s} {'n':>6s} {'corr':>6s}")
for i, a in enumerate(COINS):
    for b in COINS[i+1:]:
        r1, r2 = aligned_returns(a, b)
        c = pearson(r1, r2)
        W(f"{a}-{b:8s} {len(r1):6d} {c:6.3f}")

# ---------------------------------------------------------------- 6. LEAD-LAG
W("")
W("=" * 70)
W("6. LEAD-LAG ANALYSIS")
W("   For each pair, correlate A's return at time T with B's return at T+lag.")
W("   Higher correlation at positive lag means A LEADS B.")
W("   Lags tested: -30, -10, -5, 0, +5, +10, +30 seconds (sampled per 10s).")
W("=" * 70)

def lagged_corr(c1, c2, lag_steps):
    """Correlation of c1[t] vs c2[t+lag_steps] (each step = 10s sampling)."""
    p1 = prices[c1]
    p2 = prices[c2]
    common_secs = sorted(set(p1.keys()) & set(p2.keys()))
    if len(common_secs) < 50:
        return 0
    # build returns lists
    r1, r2 = [], []
    last1, last2 = None, None
    secs_with_returns = []
    for s in common_secs:
        v1, v2 = p1[s], p2[s]
        if last1 and last2 and last1 > 0 and last2 > 0:
            r1.append(math.log(v1/last1))
            r2.append(math.log(v2/last2))
            secs_with_returns.append(s)
        last1, last2 = v1, v2
    if lag_steps == 0:
        return pearson(r1, r2)
    if lag_steps > 0:
        return pearson(r1[:-lag_steps], r2[lag_steps:])
    else:
        return pearson(r1[-lag_steps:], r2[:lag_steps])

LAGS = [-3, -1, 0, 1, 3]   # in 10s units => -30s, -10s, 0, +10s, +30s
W(f"{'pair':12s}  {' '.join(f'{l*10:+4d}s' for l in LAGS)}")
for i, a in enumerate(COINS):
    for b in COINS[i+1:]:
        if a == "hype" or b == "hype":
            continue
        cors = [lagged_corr(a, b, l) for l in LAGS]
        W(f"{a}-{b:8s}  {' '.join(f'{c:+.2f}' for c in cors)}")
        # interpretation
        peak_lag = LAGS[cors.index(max(cors))]
        if peak_lag > 0:
            W(f"          -> {a} LEADS {b} by ~{peak_lag*10}s")
        elif peak_lag < 0:
            W(f"          -> {b} LEADS {a} by ~{-peak_lag*10}s")
        else:
            W(f"          -> simultaneous")

# ---------------------------------------------------------------- WRITE
W("")
W("=" * 70)
W("END OF REPORT")
with open(REPORT_PATH, 'w') as f:
    f.write("\n".join(out_lines))
print(f"\n[wrote {REPORT_PATH}]")
