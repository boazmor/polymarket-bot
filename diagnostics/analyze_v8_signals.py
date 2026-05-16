#!/usr/bin/env python3
"""Analyze V8 conditions using V5's SKIP_SIZE_OVER_CAP log.

V5 logs every poll where it sees a candidate that exceeds the cap. Each row
includes all 6 platform asks (poly_ua, poly_da, pr_ya, pr_na, lim_ua, lim_da).
This gives us a per-50ms snapshot of all 3 platforms' top-of-book.

For each snapshot, evaluate V8's logic:
  - count of platforms with YES <= 0.10
  - count of platforms with NO  <= 0.10
  - if >=2 YES phantoms, find lowest NO across platforms
  - if NO ask in [0.50, 0.80] -> V8 SIGNAL
"""

import csv
import sys
from collections import defaultdict


CHEAP_THRESH = 0.10
OPP_MIN = 0.50
OPP_MAX = 0.80


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def main():
    path = "/root/arb_v5_3way_live_orders.csv"
    rows_total = 0
    yes_phantom_count = defaultdict(int)
    no_phantom_count = defaultdict(int)
    opp_in_range = defaultdict(int)
    near_signal_yes = 0
    near_signal_no = 0
    full_signal_yes = 0
    full_signal_no = 0
    blockers_yes = defaultdict(int)
    blockers_no = defaultdict(int)
    sample_signals = []

    with open(path) as f:
        rd = csv.reader(f)
        next(rd)  # skip header (stale)
        # Positional layout for SKIP_SIZE_OVER_CAP rows:
        # 0:ts 1:stage 2:dir 3:min_p 4:max_p 5:cap
        # 6:poly_ua 7:poly_da 8:pr_ya 9:pr_na 10:lim_ua 11:lim_da 12:sec_in
        for row in rd:
            if len(row) < 13 or row[1] != "SKIP_SIZE_OVER_CAP":
                continue
            rows_total += 1
            try:
                poly_ua = fnum(row[6])
                poly_da = fnum(row[7])
                pr_ya = fnum(row[8])
                pr_na = fnum(row[9])
                lim_ua = fnum(row[10])
                lim_da = fnum(row[11])
            except (ValueError, IndexError):
                continue

            yes_phantoms = sum(1 for v in (poly_ua, pr_ya, lim_ua) if 0 < v <= CHEAP_THRESH)
            no_phantoms = sum(1 for v in (poly_da, pr_na, lim_da) if 0 < v <= CHEAP_THRESH)
            yes_phantom_count[yes_phantoms] += 1
            no_phantom_count[no_phantoms] += 1

            # Try YES-phantom direction (predict DOWN, buy opposite=NO)
            if yes_phantoms >= 2:
                no_asks = [v for v in (poly_da, pr_na, lim_da) if 0 < v < 1]
                if no_asks:
                    best_no = min(no_asks)
                    if OPP_MIN <= best_no <= OPP_MAX:
                        full_signal_yes += 1
                        if len(sample_signals) < 5:
                            sample_signals.append({
                                "side": "YES_phantom", "best_no": best_no,
                                "poly_ua": poly_ua, "pr_ya": pr_ya, "lim_ua": lim_ua,
                                "poly_da": poly_da, "pr_na": pr_na, "lim_da": lim_da,
                            })
                    else:
                        near_signal_yes += 1
                        if best_no < OPP_MIN:
                            blockers_yes["opp_too_low"] += 1
                        else:
                            blockers_yes["opp_too_high"] += 1
                else:
                    blockers_yes["no_opp_data"] += 1
            elif yes_phantoms == 1:
                # 1-platform signal — would need >= 2 to fire
                blockers_yes["only_1_agree"] += 1

            if no_phantoms >= 2:
                yes_asks = [v for v in (poly_ua, pr_ya, lim_ua) if 0 < v < 1]
                if yes_asks:
                    best_yes = min(yes_asks)
                    if OPP_MIN <= best_yes <= OPP_MAX:
                        full_signal_no += 1
                        if len(sample_signals) < 10:
                            sample_signals.append({
                                "side": "NO_phantom", "best_yes": best_yes,
                                "poly_ua": poly_ua, "pr_ya": pr_ya, "lim_ua": lim_ua,
                                "poly_da": poly_da, "pr_na": pr_na, "lim_da": lim_da,
                            })
                    else:
                        near_signal_no += 1
                        if best_yes < OPP_MIN:
                            blockers_no["opp_too_low"] += 1
                        else:
                            blockers_no["opp_too_high"] += 1
                else:
                    blockers_no["no_opp_data"] += 1
            elif no_phantoms == 1:
                blockers_no["only_1_agree"] += 1

    print(f"total SKIP_SIZE_OVER_CAP snapshots scanned: {rows_total}")
    print()
    print(f"YES phantom counts (how many platforms show YES <= 0.10):")
    for k in sorted(yes_phantom_count.keys()):
        n = yes_phantom_count[k]
        print(f"  {k} platforms: {n:>5} snapshots  ({n/rows_total*100:5.1f}%)")
    print(f"NO phantom counts:")
    for k in sorted(no_phantom_count.keys()):
        n = no_phantom_count[k]
        print(f"  {k} platforms: {n:>5} snapshots  ({n/rows_total*100:5.1f}%)")

    print()
    print(f"V8 signal counts (agreement>=2 AND opp_in_range):")
    print(f"  YES-phantom -> buy NO -> signal: {full_signal_yes}")
    print(f"  NO-phantom  -> buy YES -> signal: {full_signal_no}")
    print(f"  total V8 signals would have fired: {full_signal_yes + full_signal_no}")

    print()
    print(f"NEAR misses (agreement>=2 but opp out of [0.50, 0.80]):")
    print(f"  YES-side: {near_signal_yes}")
    for k, v in blockers_yes.items():
        print(f"    blocker {k}: {v}")
    print(f"  NO-side:  {near_signal_no}")
    for k, v in blockers_no.items():
        print(f"    blocker {k}: {v}")

    print()
    print(f"Sample SIGNAL snapshots (first {len(sample_signals)}):")
    for s in sample_signals:
        print(f"  {s}")


if __name__ == "__main__":
    main()
