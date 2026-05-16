#!/usr/bin/env python3
"""Last-second lottery, filtered by BTC distance from strike.

Hypothesis: when BTC is close to the target ($25 or less), a reversal
is more likely, so 1¢ buys could actually pay off.

Uses Polymarket 5m recorder. Columns binance_price (6), target_price (8),
distance_abs (10).

For each dip at price <= MAX_PRICE in time window, look at distance_abs
at that moment and bucket.
"""

import csv
import sys
from collections import defaultdict


WINDOW_SEC = 300
INVEST = 2.0

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def reconstruct_outcomes():
    out = {}
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r.get("market_epoch", 0))
                es = int(r.get("epoch_sec", 0))
                if not ep:
                    continue
                sec = es - ep
                if sec < 240:
                    continue
                up_bid = fnum(r.get("up_bid"))
                dn_bid = fnum(r.get("down_bid"))
                if up_bid >= 0.95:
                    out[ep] = "UP"
                elif dn_bid >= 0.95:
                    out[ep] = "DOWN"
            except (KeyError, ValueError):
                continue
    return out


def scan(outcomes, sec_start=270, sec_end=299, max_price=0.05, min_depth=5.0):
    """Yield (ep, sec, side, price, distance_abs, win) tuples."""
    rows = []
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r.get("market_epoch", 0))
                es = int(r.get("epoch_sec", 0))
                if not ep:
                    continue
                sec = es - ep
                if sec < sec_start or sec > sec_end:
                    continue
                if ep not in outcomes:
                    continue
                w = outcomes[ep]
                # distance_abs is blank in 5m data; compute manually
                btc = fnum(r.get("binance_price"))
                target = fnum(r.get("target_chainlink_at_open"))
                if btc <= 0 or target <= 0:
                    continue
                dist = abs(btc - target)
                ya = fnum(r.get("up_ask"))
                yu = fnum(r.get("up_usd_best"))
                na = fnum(r.get("down_ask"))
                nu = fnum(r.get("down_usd_best"))
                if 0 < ya <= max_price and yu >= min_depth:
                    rows.append((ep, sec, "UP", ya, dist, 1 if w == "UP" else 0))
                if 0 < na <= max_price and nu >= min_depth:
                    rows.append((ep, sec, "DOWN", na, dist, 1 if w == "DOWN" else 0))
            except (KeyError, ValueError):
                continue
    return rows


def first_per_side(rows):
    """One trade per (ep, side), first qualifying moment."""
    seen = set()
    keep = []
    for ep, sec, side, price, dist, win in sorted(rows, key=lambda x: (x[0], x[1])):
        key = (ep, side)
        if key in seen:
            continue
        seen.add(key)
        keep.append((ep, sec, side, price, dist, win))
    return keep


def pnl(trades):
    total = 0.0
    wins = 0
    for ep, sec, side, price, dist, win in trades:
        shares = INVEST / price
        if win:
            total += shares * (1 - price)
            wins += 1
        else:
            total -= INVEST
    return wins, len(trades), total


def main():
    print("loading outcomes...", file=sys.stderr)
    outcomes = reconstruct_outcomes()
    print(f"outcomes={len(outcomes)}", file=sys.stderr)

    # We want to test multiple max_price values; cap at 0.10 to get all rows
    rows = scan(outcomes, sec_start=270, sec_end=299, max_price=0.10, min_depth=5.0)
    print(f"cheap_rows (any dist, price<=0.10): {len(rows)}", file=sys.stderr)

    if not rows:
        print("no data")
        return

    eps = sorted(set(r[0] for r in rows))
    period_h = (eps[-1] - eps[0] + WINDOW_SEC) / 3600 if eps else 0
    days = period_h / 24

    print()
    print(f"=== לוטו שניה אחרונה, לפי מרחק מטרגט ===")
    print(f"השקעה: ${INVEST}, פלטפורמה: poly, חלון 270-299s, עומק >= $5")
    print(f"תקופה: {period_h:.0f} שעות, {days:.1f} ימים")
    print()

    print("=== מחיר ≤ 0.05, חיתוך לפי מרחק BTC מהטרגט ===")
    print()
    buckets = [(0, 10), (10, 25), (25, 50), (50, 100), (100, 200), (200, 500), (500, 99999)]
    for lo, hi in buckets:
        filt = [r for r in rows if r[3] <= 0.05 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        label = f"מרחק ${lo}-${hi if hi<99999 else 'בלי גבול'}"
        print(f"  {label.ljust(22)}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== מחיר ≤ 0.01, חיתוך לפי מרחק ===")
    print()
    for lo, hi in buckets:
        filt = [r for r in rows if r[3] <= 0.01 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        label = f"מרחק ${lo}-${hi if hi<99999 else 'בלי גבול'}"
        print(f"  {label.ljust(22)}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== מחיר ≤ 0.03, חיתוך לפי מרחק ===")
    print()
    for lo, hi in buckets:
        filt = [r for r in rows if r[3] <= 0.03 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        label = f"מרחק ${lo}-${hi if hi<99999 else 'בלי גבול'}"
        print(f"  {label.ljust(22)}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== מטריצה רווח: מחיר × מרחק ===")
    print()
    header = " ".rjust(12) + "".join(f"{f'$0-{hi}':>11}" for lo, hi in buckets[:6])
    print(header)
    for mp in [0.01, 0.02, 0.03, 0.05, 0.10]:
        row = f"≤{mp:.2f}".rjust(12)
        for lo, hi in buckets[:6]:
            filt = [r for r in rows if r[3] <= mp and lo <= r[4] < hi]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            cell = f"${p:+.0f} n{n}"
            row += cell.rjust(11)
        print(row)

    print()
    print("=== חלון יותר ארוך, 240-299, מחיר ≤ 0.05, לפי מרחק ===")
    print()
    rows60 = scan(outcomes, sec_start=240, sec_end=299, max_price=0.05, min_depth=5.0)
    for lo, hi in buckets:
        filt = [r for r in rows60 if lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        label = f"מרחק ${lo}-${hi if hi<99999 else 'בלי גבול'}"
        print(f"  {label.ljust(22)}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")


if __name__ == "__main__":
    main()
