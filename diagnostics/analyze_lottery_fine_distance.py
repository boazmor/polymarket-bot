#!/usr/bin/env python3
"""Fine-grained distance buckets for last-second lottery on 5m markets.

Tests smaller distance buckets to see if there's an even sweeter spot
than $10-$25.
"""

import csv
import sys


WINDOW_SEC = 300
INVEST = 1.5  # user's chosen size

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


def scan(outcomes, sec_start, sec_end, max_price):
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
                btc = fnum(r.get("binance_price"))
                target = fnum(r.get("target_chainlink_at_open"))
                if btc <= 0 or target <= 0:
                    continue
                dist = abs(btc - target)
                ya = fnum(r.get("up_ask"))
                na = fnum(r.get("down_ask"))
                if 0 < ya <= max_price:
                    rows.append((ep, sec, "UP", ya, dist, 1 if w == "UP" else 0))
                if 0 < na <= max_price:
                    rows.append((ep, sec, "DOWN", na, dist, 1 if w == "DOWN" else 0))
            except (KeyError, ValueError):
                continue
    return rows


def first_per_side(rows):
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
    rows = scan(outcomes, 240, 299, 0.10)
    eps = sorted(set(r[0] for r in rows))
    period_h = (eps[-1] - eps[0] + WINDOW_SEC) / 3600 if eps else 0
    days = period_h / 24

    print()
    print(f"=== מרחק עדין על 5 דקות, השקעה ${INVEST}, חלון 240-299s ===")
    print(f"תקופה: {period_h:.0f} שעות, {days:.1f} ימים")
    print()

    fine_buckets = [
        (0, 2), (2, 5), (5, 8), (8, 10), (10, 12), (12, 15),
        (15, 18), (18, 20), (20, 22), (22, 25), (25, 30),
    ]

    print("=== מחיר ≤ 0.05, חיתוך מרחק עדין ===")
    print()
    for lo, hi in fine_buckets:
        filt = [r for r in rows if r[3] <= 0.05 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  ${lo}-${hi}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== מחיר ≤ 0.03, חיתוך מרחק עדין ===")
    print()
    for lo, hi in fine_buckets:
        filt = [r for r in rows if r[3] <= 0.03 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  ${lo}-${hi}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== מחיר ≤ 0.01, חיתוך מרחק עדין ===")
    print()
    for lo, hi in fine_buckets:
        filt = [r for r in rows if r[3] <= 0.01 and lo <= r[4] < hi]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  ${lo}-${hi}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")

    print()
    print("=== חיתוך מצטבר עד $X, מחיר ≤ 0.05 ===")
    print()
    for mx in [5, 8, 10, 12, 15, 18, 20, 22, 25, 30]:
        filt = [r for r in rows if r[3] <= 0.05 and 0 < r[4] <= mx]
        filt = first_per_side(filt)
        w, n, p = pnl(filt)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  עד מרחק ${mx}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה, יומי ${per_day:+.2f}")


if __name__ == "__main__":
    main()
