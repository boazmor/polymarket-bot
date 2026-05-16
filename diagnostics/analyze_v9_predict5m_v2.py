#!/usr/bin/env python3
"""V9 dip-buy analysis on Predict.fun 5-min data using REAL Binance prices
for outcome resolution. Run on Hetzner (has binance_ticks.csv).
"""

import csv
import sys
from collections import defaultdict
from bisect import bisect_left


PATH = "/root/data_predict_btc_5m/combined_per_second.csv"
BINANCE_TICKS = "/root/data_btc_15m_research/binance_ticks.csv"
WINDOW_SEC = 300
MIN_PRICE = 0.10
MIN_DEPTH = 1.0


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_binance():
    """Returns (times_list, prices_list). File is already chronological
    so we skip the expensive sort."""
    times = []
    prices = []
    prev_ts = 0
    out_of_order = 0
    with open(BINANCE_TICKS) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                t = int(r["trade_time_ms"])
                p = float(r["price"])
                if t < prev_ts:
                    out_of_order += 1
                prev_ts = t
                times.append(t)
                prices.append(p)
            except (KeyError, ValueError):
                continue
    print(f"loaded {len(times):,} binance ticks, out_of_order={out_of_order}", file=sys.stderr)
    return times, prices


def find_price_at(times, prices, epoch_ms):
    """Binary search nearest tick in pre-built lists."""
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
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = PATH
    print(f"data source: {path}", file=sys.stderr)
    print(f"loading binance ticks...", file=sys.stderr)
    times, prices = load_binance()

    by_window = defaultdict(list)
    window_strike = {}
    with open(path) as f:
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
        outcomes[ep] = ("UP" if bp > strike else "DOWN", strike, bp)

    print(f"windows: {len(by_window)}, with outcomes: {len(outcomes)}")
    print()
    print(f"filters: price in [{MIN_PRICE}, MAX], depth >= ${MIN_DEPTH}")
    print(f"strategy: buy at first dip in 1st N seconds, hold to expiry")
    print()
    print(f"{'max_p':<8}{'time':<6}{'buys':<7}{'wins':<6}{'losses':<8}{'rate':<8}{'tot_$/$1':<10}{'avg_ret_pct':<10}")
    for max_price in (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45):
        for time_max in (20, 30, 40, 50, 60):
            buys = 0
            wins = 0
            losses = 0
            total_ret = 0.0
            for ep, recs in by_window.items():
                first = None
                for sec, ya, yu, na, nu in recs:
                    if sec > time_max:
                        continue
                    if MIN_PRICE <= ya <= max_price and yu >= MIN_DEPTH:
                        if first is None or sec < first[0]:
                            first = (sec, "yes", ya)
                    if MIN_PRICE <= na <= max_price and nu >= MIN_DEPTH:
                        if first is None or sec < first[0]:
                            first = (sec, "no", na)
                if not first:
                    continue
                sec, side, price = first
                expected = "UP" if side == "yes" else "DOWN"
                if ep not in outcomes:
                    continue
                actual = outcomes[ep][0]
                buys += 1
                if actual == expected:
                    wins += 1
                    total_ret += (1.0 - price) / price
                else:
                    losses += 1
                    total_ret -= 1.0
            resolved = wins + losses
            rate = (wins / resolved * 100) if resolved else 0
            avg_ret = (total_ret / resolved * 100) if resolved else 0
            print(f"  {max_price:<8.2f}{time_max:<6}{buys:<7}{wins:<6}{losses:<8}{rate:<8.1f}{total_ret:<+10.2f}{avg_ret:<+10.1f}")


if __name__ == "__main__":
    main()
