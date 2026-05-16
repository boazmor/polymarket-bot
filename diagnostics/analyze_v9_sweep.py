#!/usr/bin/env python3
"""V9 parameter sweep: for each (price_threshold, time_threshold)
combination, compute hit rate and EV using hold-to-expiry payoff.

Data source: /root/arb_v9_research_5m.csv

Limitation: recorder only captures first 60 seconds. So we can NOT directly
evaluate "sell at +40% profit mid-window". Instead we use the hold-to-expiry
payoff:
  - if we bought side X at price P and side X wins -> return = (1-P)/P
  - if lost -> return = -100%

Strategy variants tested:
  price_threshold = 0.25, 0.28, 0.30, 0.32, 0.35, 0.38, 0.40, 0.45
  time_threshold  = 20, 25, 30, 35, 40, 50, 60 seconds
"""

import csv
import sys
from collections import defaultdict


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def main():
    path = "/root/arb_v9_research_5m.csv"
    # per-window: list of (sec, plat, side, ask, depth)
    by_window = defaultdict(list)
    outcomes = {}

    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                sec = int(r["sec_in_window"])
                ep = int(r["window_epoch"])
                if sec == -1 and r.get("plat") == "outcome":
                    outcomes[ep] = r.get("outcome_marker")
                    continue
                ask = fnum(r.get("ask"))
                depth = fnum(r.get("ask_depth_usd"))
                if 0 < ask < 1:
                    by_window[ep].append((sec, r.get("plat"), r.get("side"), ask, depth))
            except (KeyError, ValueError):
                continue

    print(f"loaded {len(by_window)} windows, {len(outcomes)} outcomes")

    SIDE_TO_OUTCOME = {"yes": "UP", "no": "DOWN"}
    MIN_DEPTH = 5.0  # need at least $5 depth to count as real
    MIN_PRICE = 0.10  # exclude phantom dust below this

    print(f"\nfilters: depth >= ${MIN_DEPTH}, price >= ${MIN_PRICE}")
    print()
    print(f"{'p_max':<8}{'time':<6}{'buys':<7}{'wins':<6}{'losses':<8}{'pend':<6}{'rate':<8}{'avg_ret':<10}{'tot$':<10}")
    for price_thresh in (0.15, 0.20, 0.25, 0.28, 0.30, 0.32, 0.35, 0.40):
        for time_thresh in (20, 30, 40, 50, 60):
            buys = 0
            wins = 0
            losses = 0
            unresolved = 0
            total_return = 0.0
            for ep, recs in by_window.items():
                first_dip = None
                for sec, plat, side, ask, depth in recs:
                    if sec > time_thresh:
                        continue
                    if not (MIN_PRICE <= ask <= price_thresh):
                        continue
                    if depth < MIN_DEPTH:
                        continue
                    if first_dip is None or sec < first_dip[0]:
                        first_dip = (sec, plat, side, ask, depth)
                if first_dip is None:
                    continue
                sec, plat, side, ask, depth = first_dip
                expected = SIDE_TO_OUTCOME.get(side)
                actual = outcomes.get(ep)
                buys += 1
                if actual is None:
                    unresolved += 1
                    continue
                if actual == expected:
                    wins += 1
                    total_return += (1.0 - ask) / ask
                else:
                    losses += 1
                    total_return -= 1.0
            resolved = wins + losses
            rate = (wins / resolved * 100) if resolved else 0
            avg_ret = (total_return / resolved * 100) if resolved else 0
            print(f"  {price_thresh:<8.2f}{time_thresh:<6}{buys:<7}{wins:<6}{losses:<8}{unresolved:<6}{rate:<8.1f}{avg_ret:<+10.1f}{total_return:<+10.2f}")


if __name__ == "__main__":
    main()
