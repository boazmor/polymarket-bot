#!/usr/bin/env python3
"""Last-second lottery analysis.

Idea: in the last N seconds of each 5-min market, buy the CHEAPEST side
at price <= MAX_PRICE. If we win, we get $1/share, payout 50x-100x.
Even 1-2% win rate breaks even at 1c.

Scans poly + predict + limitless 5m recordings.
For each (max_price, time_window) cell shows: trades, wins, win rate,
total PnL at $2 invest.
"""

import csv
import sys
import re
from collections import defaultdict


WINDOW_SEC = 300
INVEST = 2.0
MIN_DEPTH_USD = 5.0  # we need at least a few dollars in depth to fill

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
PRED = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def reconstruct_outcomes_from_poly():
    out = {}
    try:
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
    except FileNotFoundError:
        pass
    return out


def scan_all(outcomes):
    """rows: (ep, sec, side, price, depth_usd, win, plat)"""
    rows = []
    # poly
    try:
        with open(POLY) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch", 0))
                    es = int(r.get("epoch_sec", 0))
                    if not ep:
                        continue
                    sec = es - ep
                    if sec < 240 or sec > 300:
                        continue
                    if ep not in outcomes:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("up_ask"))
                    yu = fnum(r.get("up_usd_best"))
                    na = fnum(r.get("down_ask"))
                    nu = fnum(r.get("down_usd_best"))
                    if 0 < ya <= 0.10:
                        rows.append((ep, sec, "UP", ya, yu, 1 if w == "UP" else 0, "poly"))
                    if 0 < na <= 0.10:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if w == "DOWN" else 0, "poly"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass

    # predict
    try:
        with open(PRED) as f:
            for r in csv.DictReader(f):
                try:
                    ep_raw = r.get("market_open_epoch")
                    sec_raw = r.get("sec_from_open")
                    if not ep_raw or not sec_raw:
                        continue
                    ep = int(ep_raw); sec = int(sec_raw)
                    if sec < 240 or sec > 300 or ep not in outcomes:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("yes_ask"))
                    yu = fnum(r.get("yes_ask_usd"))
                    na = fnum(r.get("no_ask_implied"))
                    nu = fnum(r.get("no_ask_usd_buyable"))
                    if 0 < ya <= 0.10:
                        rows.append((ep, sec, "UP", ya, yu, 1 if w == "UP" else 0, "predict"))
                    if 0 < na <= 0.10:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if w == "DOWN" else 0, "predict"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass

    # limitless
    slug_re = re.compile(r"-5-min-(\d{10,13})$")
    try:
        with open(LIM) as f:
            for r in csv.DictReader(f):
                try:
                    slug = r.get("slug", "")
                    m = slug_re.search(slug)
                    if not m:
                        continue
                    lim_id = int(m.group(1))
                    if lim_id > 10**12:
                        lim_id = lim_id // 1000
                    ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                    if ep not in outcomes:
                        continue
                    es = int(r["epoch_sec"]); sec = es - ep
                    if sec < 240 or sec > 300:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("best_ask"))
                    yu = fnum(r.get("best_ask_size_usd"))
                    na = fnum(r.get("no_best_ask"))
                    nu = fnum(r.get("no_best_ask_size_usd"))
                    if 0 < ya <= 0.10:
                        rows.append((ep, sec, "UP", ya, yu, 1 if w == "UP" else 0, "lim"))
                    if 0 < na <= 0.10:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if w == "DOWN" else 0, "lim"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass

    return rows


def simulate(rows, max_price, sec_start, sec_end, min_depth):
    """One trade per (window, side, platform), first qualifying moment."""
    seen = set()
    total = 0.0
    wins = 0
    losses = 0
    for ep, sec, side, price, depth, win, plat in sorted(rows, key=lambda x: (x[0], x[1])):
        if sec < sec_start or sec > sec_end:
            continue
        if price > max_price:
            continue
        if depth < min_depth:
            continue
        key = (ep, side, plat)
        if key in seen:
            continue
        seen.add(key)
        shares = INVEST / price
        if win:
            total += shares * (1 - price)  # win pays $1/share, we paid price/share
            wins += 1
        else:
            total -= INVEST
            losses += 1
    n = wins + losses
    return n, wins, total


def main():
    print("loading...", file=sys.stderr)
    outcomes = reconstruct_outcomes_from_poly()
    rows = scan_all(outcomes)
    print(f"outcomes={len(outcomes)} cheap_rows={len(rows)}", file=sys.stderr)

    if not rows:
        print("no data")
        return

    eps = sorted(set(r[0] for r in rows))
    period_h = (eps[-1] - eps[0] + WINDOW_SEC) / 3600 if eps else 0
    days = period_h / 24

    print()
    print(f"=== לוטו שניה אחרונה ===")
    print(f"השקעה לעסקה: ${INVEST}")
    print(f"תקופה: {period_h:.0f} שעות, {days:.1f} ימים")
    print(f"חלונות עם תוצאה: {len(outcomes)}")
    print()

    print("=== חיתוך לפי מחיר מקס, חלון 270-299 שניות (30 אחרונות), עומק >= $5 ===")
    print()
    for mp in [0.01, 0.02, 0.03, 0.05, 0.10]:
        n, w, p = simulate(rows, mp, 270, 299, 5)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  מחיר ≤ {mp:.2f}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.3f} לעסקה, יומי ${per_day:+.2f}")

    print()
    print("=== חיתוך לפי חלון שניות בסוף, מחיר ≤ 0.05, עומק >= $5 ===")
    print()
    for start in [285, 270, 240, 210, 180, 150]:
        n, w, p = simulate(rows, 0.05, start, 299, 5)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        per_trade = p/n if n>0 else 0
        print(f"  שניות {start}-299: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, ${per_trade:+.3f} לעסקה, יומי ${per_day:+.2f}")

    print()
    print("=== חיתוך לפי עומק מינ׳, מחיר ≤ 0.05, שניות 270-299 ===")
    print()
    for d in [1, 2, 5, 10, 20, 50]:
        n, w, p = simulate(rows, 0.05, 270, 299, d)
        rate = (w/n*100) if n>0 else 0
        per_day = p/days if days>0 else 0
        print(f"  עומק ≥ ${d}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}, יומי ${per_day:+.2f}")

    print()
    print("=== מטריצה: מחיר × חלון זמן, עומק >= $5 ===")
    print()
    header = " ".rjust(12) + "".join(f"{s:>11}" for s in ["סוף 30s", "סוף 60s", "סוף 90s", "סוף 120s", "סוף 150s"])
    print(header)
    for mp in [0.01, 0.02, 0.03, 0.05, 0.10]:
        row = f"≤{mp:.2f}".rjust(12)
        for start in [270, 240, 210, 180, 150]:
            n, w, p = simulate(rows, mp, start, 299, 5)
            cell = f"${p:+.0f} n{n}"
            row += cell.rjust(11)
        print(row)

    print()
    print("=== פלטפורמה הכי טובה ב-≤0.05 ושניות 270-299, עומק >= $5 ===")
    print()
    for plat in ["poly", "predict", "lim"]:
        rows_p = [r for r in rows if r[6] == plat]
        n, w, p = simulate(rows_p, 0.05, 270, 299, 5)
        rate = (w/n*100) if n>0 else 0
        print(f"  {plat}: {n} עסקאות, {w} זכו, דיוק {rate:.1f}%, רווח ${p:+.2f}")

    print()
    print("=== מתי הזכייה הראשונה הופיעה ===")
    print()
    # Show how many windows have at least one winning <=0.05 dip in last 30s
    win_eps = set()
    loss_eps = set()
    for ep, sec, side, price, depth, win, plat in rows:
        if 270 <= sec <= 299 and 0 < price <= 0.05 and depth >= 5:
            if win:
                win_eps.add((ep, side))
            else:
                loss_eps.add((ep, side))
    print(f"  חלונות ייחודיים עם זכייה זמינה: {len(win_eps)}")
    print(f"  חלונות עם הפסד זמין: {len(loss_eps)}")


if __name__ == "__main__":
    main()
