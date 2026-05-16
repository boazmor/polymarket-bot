#!/usr/bin/env python3
"""Last-second lottery on 15m and 1h windows, filtered by distance.

Same idea as 5m: buy cheap side near end, with distance bucket filter.
For 15m: window=900, test last 60-180 seconds.
For 1h: window=3600, test last 60-300 seconds.

Distance buckets scale: BTC moves more in 15m and 1h.
"""

import csv
import sys
from collections import defaultdict


INVEST = 2.0

DATA_PATHS = {
    "15m": ("/root/data_btc_15m_research/combined_per_second.csv", 900),
    "1h":  ("/root/data_btc_1h_research/combined_per_second.csv",  3600),
}


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def reconstruct_outcomes(path, win_sec):
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r.get("market_epoch", 0))
                es = int(r.get("epoch_sec", 0))
                if not ep:
                    continue
                sec = es - ep
                if sec < win_sec - 60:
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


def scan(path, win_sec, outcomes, sec_start, sec_end, max_price, min_depth):
    rows = []
    with open(path) as f:
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


def run_window(win_label):
    path, win_sec = DATA_PATHS[win_label]
    print(f"loading {win_label}...", file=sys.stderr)
    outcomes = reconstruct_outcomes(path, win_sec)
    print(f"  outcomes={len(outcomes)}", file=sys.stderr)

    # Different distance buckets for different windows
    if win_label == "15m":
        buckets = [(0, 25), (25, 50), (50, 100), (100, 200), (200, 400), (400, 800), (800, 99999)]
        # last 60, 120, 180 sec
        end_starts = [(win_sec - 60, "60s אחרונות"), (win_sec - 120, "120s אחרונות"), (win_sec - 180, "180s אחרונות")]
    else:
        buckets = [(0, 50), (50, 100), (100, 200), (200, 400), (400, 800), (800, 1600), (1600, 99999)]
        end_starts = [(win_sec - 60, "60s אחרונות"), (win_sec - 180, "180s אחרונות"), (win_sec - 300, "300s אחרונות")]

    print()
    print(f"=== חלון {win_label} ===")

    for start_sec, label in end_starts:
        rows = scan(path, win_sec, outcomes, start_sec, win_sec - 1, 0.10, 5.0)
        if not rows:
            print(f"  {label}: אין נתונים")
            continue
        eps = sorted(set(r[0] for r in rows))
        period_h = (eps[-1] - eps[0] + win_sec) / 3600 if eps else 0
        days = period_h / 24
        print()
        print(f"  --- {label}, מחיר ≤0.05, עומק >= $5, {period_h:.0f} שעות ({days:.1f} ימים) ---")
        for lo, hi in buckets:
            filt = [r for r in rows if r[3] <= 0.05 and lo <= r[4] < hi]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            rate = (w/n*100) if n>0 else 0
            per_day = p/days if days>0 else 0
            label_str = f"${lo}-${hi if hi<99999 else 'בלי גבול'}"
            print(f"    מרחק {label_str.ljust(15)}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, יומי ${per_day:+.2f}")

    # Best cell matrix at last 60sec for cleaner picture
    print()
    print(f"  --- {win_label}, מטריצה רווח, חלון 60s אחרונות ---")
    rows = scan(path, win_sec, outcomes, win_sec - 60, win_sec - 1, 0.10, 5.0)
    if not rows:
        print("    אין נתונים")
        return
    eps = sorted(set(r[0] for r in rows))
    period_h = (eps[-1] - eps[0] + win_sec) / 3600 if eps else 0
    days = period_h / 24

    header = " ".rjust(10) + "".join(f"${b[0]}-{b[1] if b[1]<99999 else '+'}".rjust(13) for b in buckets[:6])
    print("    " + header)
    for mp in [0.01, 0.02, 0.03, 0.05, 0.10]:
        row = f"≤{mp:.2f}".rjust(10)
        for lo, hi in buckets[:6]:
            filt = [r for r in rows if r[3] <= mp and lo <= r[4] < hi]
            filt = first_per_side(filt)
            w, n, p = pnl(filt)
            cell = f"${p:+.0f} n{n}"
            row += cell.rjust(13)
        print("    " + row)


def main():
    for win in ["15m", "1h"]:
        run_window(win)
        print()


if __name__ == "__main__":
    main()
