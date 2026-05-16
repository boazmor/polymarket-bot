#!/usr/bin/env python3
"""Calendar spread analysis on Polymarket BTC.

At each common closing time T:
  - 5m market 5m: opened T-300, strike S5
  - 15m market: opened T-900, strike S15
  - 1h market: opened T-3600, strike S1h (only if T % 3600 == 0)

At T-30 sec before close, the YES ask prices on these markets should
respect the strike-monotonic rule:
  If S_a < S_b, then YES_a >= YES_b (lower strike easier to beat).

Any violation (YES_lower_strike < YES_higher_strike) is an arbitrage.

Also compare to Binance BTC at T (the actual closing price).
"""

import csv
import sys
from collections import defaultdict


WINDOW_5M = 300
WINDOW_15M = 900
WINDOW_1H = 3600

POLY_5M = "/root/data_btc_5m_research/combined_per_second.csv"  # may not exist on hetzner
POLY_5M_ALT = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_15M = "/root/data_btc_15m_research/combined_per_second.csv"
POLY_1H = "/root/data_btc_1h_research/combined_per_second.csv"


def fnum(s):
    try:
        return float(s) if s not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def load_per_sec(path, win_sec):
    """Returns dict: (ep, sec_from_start) -> {'up_ask','down_ask','up_bid','down_bid',
    'strike','btc'}"""
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch") or 0)
                    es = int(r.get("epoch_sec") or 0)
                    if not ep:
                        continue
                    sec = es - ep
                    if sec < 0 or sec > win_sec:
                        continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("up_ask")),
                        "down_ask": fnum(r.get("down_ask")),
                        "up_bid": fnum(r.get("up_bid")),
                        "down_bid": fnum(r.get("down_bid")),
                        "strike": fnum(r.get("target_chainlink_at_open")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        return out
    return out


def reconstruct_outcomes(per_sec, win_sec):
    """ep -> 'UP' or 'DOWN' based on late-window bids."""
    by_ep = defaultdict(list)
    for (ep, sec), d in per_sec.items():
        if sec >= win_sec - 60:
            by_ep[ep].append((sec, d))
    out = {}
    for ep, ticks in by_ep.items():
        winner = None
        for sec, d in sorted(ticks):
            if d["up_bid"] >= 0.95:
                winner = "UP"
            elif d["down_bid"] >= 0.95:
                winner = "DOWN"
        if winner:
            out[ep] = winner
    return out


def analyze_pair(name_a, win_a, data_a, name_b, win_b, data_b, outcomes_a, outcomes_b, common_period):
    """At every T that's a multiple of common_period, check pair (win_a, win_b)
    where win_a < win_b. The win_a market opened at T - win_a, win_b at T - win_b.

    Look at T-30 sec snapshot of each market, check strike-monotonic rule.
    """
    violations = []
    consistencies = []

    # Collect all unique close times
    close_times = set()
    for (ep, sec) in data_a:
        if sec == 0:
            close_times.add(ep + win_a)
    for (ep, sec) in data_b:
        if sec == 0:
            close_times.add(ep + win_b)

    common_closes = [t for t in close_times if t % common_period == 0]
    common_closes.sort()

    for T in common_closes:
        ep_a = T - win_a
        ep_b = T - win_b
        # Look at T-30 sec snapshot of each
        snap_a = data_a.get((ep_a, win_a - 30))
        snap_b = data_b.get((ep_b, win_b - 30))
        if not snap_a or not snap_b:
            continue
        if snap_a["strike"] <= 0 or snap_b["strike"] <= 0:
            continue
        # YES = UP. Lower strike -> easier UP, higher YES ask.
        # If S_a < S_b: expect YES_a > YES_b. Violation: YES_a < YES_b - margin
        # Use last known asks
        # Skip if either market has empty/extreme orderbook (not tradable)
        LIQUID_LO, LIQUID_HI = 0.03, 0.97
        if not (LIQUID_LO <= snap_a["up_ask"] <= LIQUID_HI):
            continue
        if not (LIQUID_LO <= snap_b["up_ask"] <= LIQUID_HI):
            continue
        # Skip very different strikes (not comparable)
        if abs(snap_a["strike"] - snap_b["strike"]) > 500:
            continue

        if snap_a["strike"] < snap_b["strike"]:
            low, high = "a", "b"
            low_yes = snap_a["up_ask"]; high_yes = snap_b["up_ask"]
            low_strike = snap_a["strike"]; high_strike = snap_b["strike"]
        elif snap_a["strike"] > snap_b["strike"]:
            low, high = "b", "a"
            low_yes = snap_b["up_ask"]; high_yes = snap_a["up_ask"]
            low_strike = snap_b["strike"]; high_strike = snap_a["strike"]
        else:
            # Same strike -- should have same YES
            low = high = "equal"
            low_yes = snap_a["up_ask"]; high_yes = snap_b["up_ask"]
            low_strike = high_strike = snap_a["strike"]

        gap = low_yes - high_yes  # should be >= 0
        record = {
            "T": T,
            "btc": snap_a["btc"],
            "strike_a": snap_a["strike"], "strike_b": snap_b["strike"],
            "yes_a": snap_a["up_ask"], "yes_b": snap_b["up_ask"],
            "gap": gap,
            "winner_a": outcomes_a.get(ep_a),
            "winner_b": outcomes_b.get(ep_b),
        }
        if gap < -0.05:  # violation: YES on lower strike < YES on higher strike by > 5c
            violations.append(record)
        else:
            consistencies.append(record)

    return violations, consistencies, len(common_closes)


def main():
    # Load
    print("loading 5m...", file=sys.stderr)
    poly5 = load_per_sec(POLY_5M_ALT, WINDOW_5M)  # path on helsinki; on hetzner use POLY_5M
    print(f"  5m ticks: {len(poly5)}", file=sys.stderr)

    print("loading 15m...", file=sys.stderr)
    poly15 = load_per_sec(POLY_15M, WINDOW_15M)
    print(f"  15m ticks: {len(poly15)}", file=sys.stderr)

    print("loading 1h...", file=sys.stderr)
    poly1h = load_per_sec(POLY_1H, WINDOW_1H)
    print(f"  1h ticks: {len(poly1h)}", file=sys.stderr)

    out5 = reconstruct_outcomes(poly5, WINDOW_5M)
    out15 = reconstruct_outcomes(poly15, WINDOW_15M)
    out1h = reconstruct_outcomes(poly1h, WINDOW_1H)
    print(f"  outcomes: 5m={len(out5)} 15m={len(out15)} 1h={len(out1h)}", file=sys.stderr)

    print()
    print("=== Calendar Spread על Polymarket ===")
    print()

    # 5m vs 15m, common close every 15 min
    if poly5 and poly15:
        viols, cons, total = analyze_pair("5m", WINDOW_5M, poly5,
                                            "15m", WINDOW_15M, poly15,
                                            out5, out15,
                                            common_period=WINDOW_15M)
        print(f"5דק מול 15דק, סגירות משותפות: {total}")
        print(f"  עקבי: {len(cons)}")
        print(f"  הפרות: {len(viols)} מקרים שצד עם טרגט נמוך יותר נסחר זול יותר")
        if viols:
            print()
            print("  --- 10 הפרות גדולות ביותר ---")
            viols.sort(key=lambda r: r["gap"])
            for v in viols[:10]:
                d = abs(v["strike_a"] - v["strike_b"])
                print(f"    T={v['T']} BTC={v['btc']:.1f} | "
                      f"strikes A={v['strike_a']:.1f} B={v['strike_b']:.1f} "
                      f"(הפרש ${d:.0f}) | "
                      f"YES_a={v['yes_a']:.2f} YES_b={v['yes_b']:.2f} | "
                      f"פער={v['gap']:+.2f} | "
                      f"winners {v['winner_a']}/{v['winner_b']}")
        print()

    # 5m vs 1h, common close every hour
    if poly5 and poly1h:
        viols, cons, total = analyze_pair("5m", WINDOW_5M, poly5,
                                            "1h", WINDOW_1H, poly1h,
                                            out5, out1h,
                                            common_period=WINDOW_1H)
        print(f"5דק מול שעה, סגירות משותפות: {total}")
        print(f"  עקבי: {len(cons)}")
        print(f"  הפרות: {len(viols)}")
        if viols:
            print("  --- 10 הפרות גדולות ביותר ---")
            viols.sort(key=lambda r: r["gap"])
            for v in viols[:10]:
                d = abs(v["strike_a"] - v["strike_b"])
                print(f"    T={v['T']} BTC={v['btc']:.1f} | "
                      f"strikes 5m={v['strike_a']:.1f} 1h={v['strike_b']:.1f} "
                      f"(הפרש ${d:.0f}) | "
                      f"YES_5m={v['yes_a']:.2f} YES_1h={v['yes_b']:.2f} | "
                      f"פער={v['gap']:+.2f}")
        print()

    # 15m vs 1h, common close every hour
    if poly15 and poly1h:
        viols, cons, total = analyze_pair("15m", WINDOW_15M, poly15,
                                            "1h", WINDOW_1H, poly1h,
                                            out15, out1h,
                                            common_period=WINDOW_1H)
        print(f"15דק מול שעה, סגירות משותפות: {total}")
        print(f"  עקבי: {len(cons)}")
        print(f"  הפרות: {len(viols)}")
        if viols:
            print("  --- 10 הפרות גדולות ביותר ---")
            viols.sort(key=lambda r: r["gap"])
            for v in viols[:10]:
                d = abs(v["strike_a"] - v["strike_b"])
                print(f"    T={v['T']} BTC={v['btc']:.1f} | "
                      f"strikes 15m={v['strike_a']:.1f} 1h={v['strike_b']:.1f} "
                      f"(הפרש ${d:.0f}) | "
                      f"YES_15m={v['yes_a']:.2f} YES_1h={v['yes_b']:.2f} | "
                      f"פער={v['gap']:+.2f}")
        print()


if __name__ == "__main__":
    main()
