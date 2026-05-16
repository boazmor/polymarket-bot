#!/usr/bin/env python3
"""Short-window lottery: focus on the last 10-60 seconds with fine
distance buckets to test whether near-strike + late-time pays off.

If user's hypothesis is right, small distance + very late = best.
"""

import csv
import sys


WINDOW_SEC = 300
INVEST = 1.5

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
                if sec < 280:
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
    outcomes = reconstruct_outcomes()
    print(f"outcomes={len(outcomes)}", file=sys.stderr)

    fine = [(0, 2), (2, 5), (5, 8), (8, 10), (10, 15), (15, 20), (20, 25), (25, 30)]
    time_windows = [
        (290, 299, "10s אחרונות"),
        (285, 299, "15s אחרונות"),
        (280, 299, "20s אחרונות"),
        (270, 299, "30s אחרונות"),
        (240, 299, "60s אחרונות"),
    ]

    for sec_s, sec_e, label in time_windows:
        rows = scan(outcomes, sec_s, sec_e, 0.05)
        if not rows:
            continue
        eps = sorted(set(r[0] for r in rows))
        period_h = (eps[-1] - eps[0] + WINDOW_SEC) / 3600 if eps else 0
        days = period_h / 24
        print()
        print(f"=== {label}, מחיר ≤ 0.05, {days:.1f} ימים ===")
        for lo, hi in fine:
            filt = [r for r in rows if lo <= r[4] < hi]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            rate = (w/n*100) if n>0 else 0
            per_trade = (p/n) if n>0 else 0
            print(f"  ${lo:>2}-${hi:<2}: {n:>4} עסקאות, {w} זכו, דיוק {rate:>4.1f}%, רווח ${p:+.2f}, ${per_trade:+.2f}/עסקה")

    print()
    print("=== חיתוך מצטבר עד $X לכל חלון זמן, מחיר ≤ 0.05 ===")
    print()
    print(f"  זמן         עד $5    עד $10    עד $15    עד $20    עד $25    עד $30")
    for sec_s, sec_e, label in time_windows:
        rows = scan(outcomes, sec_s, sec_e, 0.05)
        if not rows:
            continue
        row_str = f"  {label.ljust(12)}"
        for mx in [5, 10, 15, 20, 25, 30]:
            filt = [r for r in rows if r[4] <= mx]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            row_str += f" ${p:+.0f}/n{n}".rjust(11)
        print(row_str)

    print()
    print("=== מחיר ≤ 0.01 (1 סנט בלבד), אותה השוואה ===")
    print()
    print(f"  זמן         עד $5    עד $10    עד $15    עד $20    עד $25    עד $30")
    for sec_s, sec_e, label in time_windows:
        rows = scan(outcomes, sec_s, sec_e, 0.01)
        if not rows:
            continue
        row_str = f"  {label.ljust(12)}"
        for mx in [5, 10, 15, 20, 25, 30]:
            filt = [r for r in rows if r[4] <= mx]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            row_str += f" ${p:+.0f}/n{n}".rjust(11)
        print(row_str)


if __name__ == "__main__":
    main()
