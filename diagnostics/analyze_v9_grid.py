#!/usr/bin/env python3
"""V9 grid: price 0.30..0.35 x time 30..55 seconds.

For each cell: n trades, win rate, total dollar PnL at $2/trade.
First-dip-per-window per platform, depth >= $20.
"""

import csv
import sys
import re
from collections import defaultdict


WINDOW_SEC = 300
INVEST = 2.0
MIN_DEPTH_USD = 20.0

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
                    if sec < 0 or sec > 300:
                        continue
                    if ep not in outcomes:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("up_ask"))
                    yu = fnum(r.get("up_usd_best"))
                    na = fnum(r.get("down_ask"))
                    nu = fnum(r.get("down_usd_best"))
                    if 0.05 <= ya and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, 1 if w == "UP" else 0, "poly"))
                    if 0.05 <= na and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, 1 if w == "DOWN" else 0, "poly"))
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
                    if sec < 0 or sec > 300 or ep not in outcomes:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("yes_ask"))
                    yu = fnum(r.get("yes_ask_usd"))
                    na = fnum(r.get("no_ask_implied"))
                    nu = fnum(r.get("no_ask_usd_buyable"))
                    if 0.05 <= ya and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, 1 if w == "UP" else 0, "predict"))
                    if 0.05 <= na and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, 1 if w == "DOWN" else 0, "predict"))
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
                    if sec < 0 or sec > 300:
                        continue
                    w = outcomes[ep]
                    ya = fnum(r.get("best_ask"))
                    yu = fnum(r.get("best_ask_size_usd"))
                    na = fnum(r.get("no_best_ask"))
                    nu = fnum(r.get("no_best_ask_size_usd"))
                    if 0.05 <= ya and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, 1 if w == "UP" else 0, "lim"))
                    if 0.05 <= na and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, 1 if w == "DOWN" else 0, "lim"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass

    return rows


def simulate(rows, price_max, time_max):
    seen = set()
    total = 0.0
    wins = 0
    losses = 0
    for ep, sec, side, price, win, plat in sorted(rows, key=lambda x: (x[0], x[1])):
        if sec > time_max or price > price_max:
            continue
        key = (ep, side, plat)
        if key in seen:
            continue
        seen.add(key)
        if win:
            total += INVEST * (1 - price) / price
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
    print(f"outcomes={len(outcomes)} dip_rows={len(rows)}", file=sys.stderr)

    eps = sorted(set(r[0] for r in rows))
    period_h = (eps[-1] - eps[0] + WINDOW_SEC) / 3600 if eps else 0
    days = period_h / 24

    print()
    print(f"=== V9 רשת מחיר וזמן ===")
    print(f"השקעה: ${INVEST} לעסקה")
    print(f"עומק מינ׳: ${MIN_DEPTH_USD}")
    print(f"תקופה: {period_h:.0f} שעות, {days:.1f} ימים")
    print()

    print("=== שינוי רק מחיר, זמן נשאר 30 שניות ===")
    print()
    base_p, base_w, base_pnl = simulate(rows, 0.30, 30)
    print(f"  בסיס 0.30 ב-30 שניות: {base_p} עסקאות, {base_w} זכו, רווח ${base_pnl:+.0f}")
    print()
    for inc in [1, 2, 3, 4, 5]:
        price = 0.30 + inc / 100.0
        n, w, p = simulate(rows, price, 30)
        delta_n = n - base_p
        delta_pnl = p - base_pnl
        rate = (w / n * 100) if n > 0 else 0
        per_day = p / days if days > 0 else 0
        print(f"  +{inc}¢ → 0.{30+inc}: {n} עסקאות, +{delta_n} מהבסיס, דיוק {rate:.0f}%, רווח ${p:+.0f}, שינוי ${delta_pnl:+.0f}, יומי ${per_day:+.1f}")

    print()
    print("=== שינוי רק זמן, מחיר נשאר 0.30 ===")
    print()
    print(f"  בסיס 30 שניות ב-0.30: {base_p} עסקאות, רווח ${base_pnl:+.0f}")
    print()
    for inc in [1, 2, 3, 4, 5]:
        secs = 30 + inc * 5  # add 5 sec per step for visibility
        n, w, p = simulate(rows, 0.30, secs)
        delta_n = n - base_p
        delta_pnl = p - base_pnl
        rate = (w / n * 100) if n > 0 else 0
        per_day = p / days if days > 0 else 0
        print(f"  +{inc*5} שניות → {secs}s: {n} עסקאות, +{delta_n} מהבסיס, דיוק {rate:.0f}%, רווח ${p:+.0f}, שינוי ${delta_pnl:+.0f}, יומי ${per_day:+.1f}")

    print()
    print("=== שינוי בשניהם יחד, +N¢ וגם +5N שניות ===")
    print()
    print(f"  בסיס 0.30 ב-30 שניות: {base_p} עסקאות, רווח ${base_pnl:+.0f}")
    print()
    for inc in [1, 2, 3, 4, 5]:
        price = 0.30 + inc / 100.0
        secs = 30 + inc * 5
        n, w, p = simulate(rows, price, secs)
        delta_n = n - base_p
        delta_pnl = p - base_pnl
        rate = (w / n * 100) if n > 0 else 0
        per_day = p / days if days > 0 else 0
        print(f"  +{inc}¢ ו-+{inc*5}s → 0.{30+inc} ב-{secs}s: {n} עסקאות, +{delta_n} מהבסיס, דיוק {rate:.0f}%, רווח ${p:+.0f}, שינוי ${delta_pnl:+.0f}, יומי ${per_day:+.1f}")

    print()
    print("=== מטריצה מלאה רווח דולרי ===")
    print()
    header = "מחיר / זמן".rjust(12) + "".join(f"{t:>10}s" for t in [30, 35, 40, 45, 50, 55])
    print(header)
    for p_inc in [0, 1, 2, 3, 4, 5]:
        price = 0.30 + p_inc / 100.0
        row = f"0.{30+p_inc}".rjust(12)
        for secs in [30, 35, 40, 45, 50, 55]:
            n, w, pnl = simulate(rows, price, secs)
            cell = f"${pnl:+.0f} n{n}"
            row += cell.rjust(11)
        print(row)


if __name__ == "__main__":
    main()
