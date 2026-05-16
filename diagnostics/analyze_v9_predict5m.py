#!/usr/bin/env python3
"""V9 dip-buy analysis using Predict.fun 5-min recorder.

For each 5-min window, find the first moment in first X seconds where
yes_ask or no_ask_implied is in [MIN_PRICE, MAX_PRICE] with depth >= $D.
'Buy' at that moment. Check if that side WON at expiry.

Sweep price thresholds and time thresholds. Print hit rate + EV.

Data source: /root/data_predict_btc_5m/combined_per_second.csv
Outcomes:    /root/data_predict_btc_5m/market_outcomes.csv
"""

import csv
import sys
from collections import defaultdict


PATH = "/root/data_predict_btc_5m/combined_per_second.csv"
OUTCOMES = "/root/data_predict_btc_5m/market_outcomes.csv"
WINDOW_SEC = 300
MIN_PRICE = 0.10
MIN_DEPTH = 1.0


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_outcomes():
    """Compute outcomes from the per-second data itself.
    For each window, take the LAST row with sec_from_open closest to 300s
    and compare binance_now to strike."""
    last_per_window = {}
    with open(PATH) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_open_epoch"])
                sec = int(r["sec_from_open"])
                strike = fnum(r.get("strike"))
                price = fnum(r.get("binance_now"))
                if strike <= 0 or price <= 0:
                    continue
                cur = last_per_window.get(ep)
                if cur is None or sec > cur[0]:
                    last_per_window[ep] = (sec, strike, price)
            except (KeyError, ValueError):
                continue
    out = {}
    for ep, (sec, strike, price) in last_per_window.items():
        if sec < 240:  # need close-to-end data
            continue
        out[ep] = "UP" if price > strike else "DOWN"
    return out


def main():
    outcomes = load_outcomes()
    print(f"loaded {len(outcomes)} outcomes")

    # per-window: list of (sec_from_open, yes_ask, yes_usd, no_ask, no_usd)
    by_window = defaultdict(list)
    with open(PATH) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_open_epoch"])
                sec = int(r["sec_from_open"])
                ya = fnum(r.get("yes_ask"))
                yu = fnum(r.get("yes_ask_usd"))
                na = fnum(r.get("no_ask_implied"))
                nu = fnum(r.get("no_ask_usd_buyable"))
                by_window[ep].append((sec, ya, yu, na, nu))
            except (KeyError, ValueError):
                continue
    print(f"loaded {len(by_window)} windows")

    print()
    print(f"filters: price in [{MIN_PRICE}, MAX] AND depth >= ${MIN_DEPTH}")
    print(f"strategy: buy at first dip, hold to expiry, win pays $1")
    print()
    print(f"{'max_price':<11}{'time_max':<10}{'buys':<7}{'wins':<6}{'losses':<8}{'pend':<6}{'rate':<8}{'tot_$/$1':<10}")
    for max_price in (0.15, 0.20, 0.25, 0.30, 0.35, 0.40):
        for time_max in (20, 30, 40, 50, 60):
            buys = 0
            wins = 0
            losses = 0
            pend = 0
            total_ret = 0.0
            for ep, recs in by_window.items():
                first = None
                for sec, ya, yu, na, nu in recs:
                    if sec > time_max:
                        continue
                    # yes side dip
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
                actual = outcomes.get(ep)
                buys += 1
                if actual not in ("UP", "DOWN"):
                    pend += 1
                    continue
                if actual == expected:
                    wins += 1
                    total_ret += (1.0 - price) / price
                else:
                    losses += 1
                    total_ret -= 1.0
            resolved = wins + losses
            rate = (wins / resolved * 100) if resolved else 0
            print(f"  {max_price:<11.2f}{time_max:<10}{buys:<7}{wins:<6}{losses:<8}{pend:<6}{rate:<8.1f}{total_ret:<+10.2f}")


if __name__ == "__main__":
    main()
