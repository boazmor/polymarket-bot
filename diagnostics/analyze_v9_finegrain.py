#!/usr/bin/env python3
"""V9 fine-grain analysis: for each precise price level and time second,
report frequency and outcomes. Output as indented Hebrew-friendly lists,
not as a wide table.
"""

import csv
import sys
from bisect import bisect_left
from collections import defaultdict


PATH = "/root/predict5m_helsinki.csv"
BINANCE_TICKS = "/root/data_btc_15m_research/binance_ticks.csv"
WINDOW_SEC = 300
MIN_DEPTH = 1.0


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_binance():
    times = []
    prices = []
    with open(BINANCE_TICKS) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                t = int(r["trade_time_ms"])
                p = float(r["price"])
                times.append(t)
                prices.append(p)
            except (KeyError, ValueError):
                continue
    print(f"binance ticks: {len(times):,}", file=sys.stderr)
    return times, prices


def find_price_at(times, prices, epoch_ms):
    if not times:
        return None
    i = bisect_left(times, epoch_ms)
    if i >= len(times):
        return prices[-1]
    if i == 0:
        return prices[0]
    if abs(times[i] - epoch_ms) < abs(times[i-1] - epoch_ms):
        return prices[i]
    return prices[i-1]


def main():
    times, prices = load_binance()

    by_window = defaultdict(list)
    window_strike = {}
    with open(PATH) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep_raw = r.get("market_open_epoch")
                sec_raw = r.get("sec_from_open")
                if not ep_raw or not sec_raw:
                    continue
                ep = int(ep_raw)
                sec = int(sec_raw)
                strike = fnum(r.get("strike"))
                if strike > 0:
                    window_strike[ep] = strike
                ya = fnum(r.get("yes_ask"))
                yu = fnum(r.get("yes_ask_usd"))
                na = fnum(r.get("no_ask_implied"))
                nu = fnum(r.get("no_ask_usd_buyable"))
                by_window[ep].append((sec, ya, yu, na, nu))
            except (KeyError, ValueError, TypeError):
                continue

    outcomes = {}
    for ep, strike in window_strike.items():
        close_ms = (ep + WINDOW_SEC) * 1000
        bp = find_price_at(times, prices, close_ms)
        if bp is None:
            continue
        outcomes[ep] = "UP" if bp > strike else "DOWN"

    print(f"windows: {len(by_window)}, with outcomes: {len(outcomes)}")
    print()

    # Strategy: for each (max_price, time_max), simulate the V9 buy
    SIDE_TO_OUT = {"yes": "UP", "no": "DOWN"}

    def simulate(max_p, t_max):
        buys = 0
        wins = 0
        losses = 0
        tot = 0.0
        for ep, recs in by_window.items():
            first = None
            for sec, ya, yu, na, nu in recs:
                if sec > t_max:
                    continue
                if 0.10 <= ya <= max_p and yu >= MIN_DEPTH:
                    if first is None or sec < first[0]:
                        first = (sec, "yes", ya)
                if 0.10 <= na <= max_p and nu >= MIN_DEPTH:
                    if first is None or sec < first[0]:
                        first = (sec, "no", na)
            if not first:
                continue
            sec, side, price = first
            expected = SIDE_TO_OUT.get(side)
            if ep not in outcomes:
                continue
            actual = outcomes[ep]
            buys += 1
            if actual == expected:
                wins += 1
                tot += (1.0 - price) / price
            else:
                losses += 1
                tot -= 1.0
        return buys, wins, losses, tot

    # === Fine-grain price scan at fixed time=30 ===
    print("חתך לפי מחיר בזמן 30 שניות")
    print()
    for max_p in [0.25, 0.26, 0.27, 0.28, 0.29, 0.30, 0.31, 0.32, 0.33, 0.34, 0.35, 0.40, 0.45]:
        b, w, l, t = simulate(max_p, 30)
        res = w + l
        rate = (w / res * 100) if res else 0
        avg = (t / res * 100) if res else 0
        print(f"  מחיר עד {max_p:.2f}: {b} קניות, {w} ניצחונות, {l} הפסדים, דיוק {rate:.0f}%, רווח לעסקה {avg:+.1f}%")
    print()

    # === Fine-grain time scan at price max=0.30 ===
    print("חתך לפי זמן במחיר עד 0.30")
    print()
    for t_max in [20, 25, 30, 35, 40, 45, 50, 60]:
        b, w, l, t = simulate(0.30, t_max)
        res = w + l
        rate = (w / res * 100) if res else 0
        avg = (t / res * 100) if res else 0
        print(f"  זמן עד {t_max} שניות: {b} קניות, {w} ניצחונות, {l} הפסדים, דיוק {rate:.0f}%, רווח לעסקה {avg:+.1f}%")
    print()

    # === Frequency of price levels and time of first appearance ===
    print("תדירות מחירים נמוכים במצטבר בכל החלון")
    print()
    counters = defaultdict(lambda: defaultdict(int))
    # counters[max_price][time_bucket] = number of windows where some side
    # had ask in [0.10, max_price] with depth>=1 FIRST appeared at time_bucket
    for ep, recs in by_window.items():
        seen_for_price = {}  # max_price -> earliest sec seen at <= max_price
        for sec, ya, yu, na, nu in recs:
            for ask, depth in [(ya, yu), (na, nu)]:
                if not (0.10 <= ask) or depth < 1:
                    continue
                for mp in [0.30, 0.35, 0.40]:
                    if ask <= mp:
                        if mp not in seen_for_price or sec < seen_for_price[mp]:
                            seen_for_price[mp] = sec
        for mp, sec in seen_for_price.items():
            bucket = (sec // 10) * 10
            counters[mp][bucket] += 1

    for mp in [0.30, 0.35, 0.40]:
        print(f"  מחיר עד {mp:.2f}, תזמון הופעה ראשונה:")
        for bucket in sorted(counters[mp].keys())[:8]:
            n = counters[mp][bucket]
            print(f"    שניות {bucket}-{bucket+9}: {n} חלונות")
        total = sum(counters[mp].values())
        print(f"    סך הכל: {total} חלונות עם הצעה כזו")
        print()


if __name__ == "__main__":
    main()
