#!/usr/bin/env python3
"""Compare lottery results at price <=0.10 vs <=0.05."""

import csv
import sys


WINDOW_SEC = 300
INVEST = 1.5

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"


def fnum(s):
    try:
        return float(s) if s not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def reconstruct_outcomes():
    out = {}
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r.get("market_epoch", 0))
                es = int(r.get("epoch_sec", 0))
                if not ep: continue
                sec = es - ep
                if sec < 240: continue
                ub = fnum(r.get("up_bid"))
                db = fnum(r.get("down_bid"))
                if ub >= 0.95: out[ep] = "UP"
                elif db >= 0.95: out[ep] = "DOWN"
            except (KeyError, ValueError): continue
    return out


def scan(outcomes, sec_start, sec_end, max_price):
    rows = []
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r.get("market_epoch", 0))
                es = int(r.get("epoch_sec", 0))
                if not ep: continue
                sec = es - ep
                if sec < sec_start or sec > sec_end: continue
                if ep not in outcomes: continue
                w = outcomes[ep]
                btc = fnum(r.get("binance_price"))
                target = fnum(r.get("target_chainlink_at_open"))
                if btc <= 0 or target <= 0: continue
                dist = abs(btc - target)
                ya = fnum(r.get("up_ask"))
                na = fnum(r.get("down_ask"))
                if 0 < ya <= max_price:
                    rows.append((ep, sec, "UP", ya, dist, 1 if w == "UP" else 0))
                if 0 < na <= max_price:
                    rows.append((ep, sec, "DOWN", na, dist, 1 if w == "DOWN" else 0))
            except (KeyError, ValueError): continue
    return rows


def first_per_side(rows):
    seen = set()
    keep = []
    for ep, sec, side, price, dist, win in sorted(rows, key=lambda x: (x[0], x[1])):
        key = (ep, side)
        if key in seen: continue
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
    rows = scan(outcomes, 240, 299, 0.10)
    eps = sorted(set(r[0] for r in rows))
    days = ((eps[-1] - eps[0] + WINDOW_SEC) / 86400) if eps else 0

    fine = [(0, 2), (2, 5), (5, 8), (8, 10), (10, 15), (15, 20), (20, 25), (25, 30)]

    print(f"=== השוואת תקרה 0.05 מול 0.10, חלון 60s אחרונות, השקעה ${INVEST}, {days:.1f} ימים ===")
    print()
    print(f"  {'מרחק':<10}{'≤0.05 רווח':>18}{'≤0.10 רווח':>18}{'תוספת':>14}")
    print()

    sum_05 = 0.0
    sum_10 = 0.0
    n_05 = 0
    n_10 = 0
    for lo, hi in fine:
        f05 = first_per_side([r for r in rows if lo <= r[4] < hi and r[3] <= 0.05])
        f10 = first_per_side([r for r in rows if lo <= r[4] < hi and r[3] <= 0.10])
        w5, t5, p5 = pnl(f05)
        w10, t10, p10 = pnl(f10)
        sum_05 += p5; sum_10 += p10
        n_05 += t5; n_10 += t10
        delta = p10 - p5
        line = f"  ${lo}-${hi}".ljust(10)
        line += f"{p5:+.0f} (n{t5}, w{w5})".rjust(18)
        line += f"{p10:+.0f} (n{t10}, w{w10})".rjust(18)
        line += f"{delta:+.0f}".rjust(14)
        print(line)

    print()
    print(f"  סה״כ".ljust(10)
          + f"{sum_05:+.0f} (n{n_05})".rjust(18)
          + f"{sum_10:+.0f} (n{n_10})".rjust(18)
          + f"{sum_10 - sum_05:+.0f}".rjust(14))

    print()
    print(f"=== חיתוך מצטבר עד מרחק $X, השוואת 0.05 מול 0.10 ===")
    print()
    print(f"  {'מרחק עד':<12}{'≤0.05':>14}{'≤0.10':>14}{'תוספת':>10}")
    for mx in [10, 15, 20, 25, 30]:
        f05 = first_per_side([r for r in rows if r[4] < mx and r[3] <= 0.05])
        f10 = first_per_side([r for r in rows if r[4] < mx and r[3] <= 0.10])
        w5, t5, p5 = pnl(f05)
        w10, t10, p10 = pnl(f10)
        per_day_05 = p5 / days if days > 0 else 0
        per_day_10 = p10 / days if days > 0 else 0
        line = f"  ${mx}".ljust(12)
        line += f"${p5:+.0f}/יום ${per_day_05:+.1f}".rjust(14)
        line += f"${p10:+.0f}/יום ${per_day_10:+.1f}".rjust(14)
        line += f"{p10-p5:+.0f}".rjust(10)
        print(line)


if __name__ == "__main__":
    main()
