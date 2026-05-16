#!/usr/bin/env python3
"""3-platform consensus analysis using RECORDER data (not just bot's SKIP log).

Walks the per-second recorder files for poly + predict + limitless. For each
15-min market window, finds whether any platform showed a "phantom" cheap ask
(<= PHANTOM_THRESH) on the YES (=UP) side, and classifies:

  CONS_DOWN_2 : both lim AND poly showed YES <= 0.05 in this window
                -> consensus says DOWN wins
  CONS_DOWN_1 : only lim showed YES <= 0.05
  CONS_UP_2   : both lim AND poly showed NO <= 0.05
                -> consensus says UP wins
  CONS_UP_1   : only one showed NO <= 0.05
  CONTRADICT  : lim YES phantom AND poly NO phantom (contradiction)
  NO_PHANTOM  : no phantom seen

Then computes how often the predicted side actually won.
"""

import csv
import sys
from collections import defaultdict


POLY_FILE    = "/root/data_btc_15m_research/combined_per_second.csv"
PREDICT_FILE = "/root/data_predict_btc_15m/combined_per_second.csv"
LIM_FILE     = "/root/data_limitless_btc_15m/combined_per_second.csv"
OUTCOMES_FILE = "/root/data_btc_15m_research/market_outcomes.csv"

PHANTOM_THRESH = 0.05  # ask <= this counts as a phantom cheap offer


def fnum(s, default=0.0):
    try:
        v = float(s)
        return v
    except (TypeError, ValueError):
        return default


def load_outcomes():
    out = {}
    with open(OUTCOMES_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                out[int(r["market_epoch"])] = r["winner_side"]
            except (KeyError, ValueError):
                pass
    return out


def scan_poly(per_window):
    """For each window, did poly UP/DOWN ask ever go <= PHANTOM_THRESH?"""
    with open(POLY_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                up_ask = fnum(r.get("up_ask"))
                down_ask = fnum(r.get("down_ask"))
                w = per_window[epoch]
                if 0 < up_ask <= PHANTOM_THRESH:
                    w["poly_up_phantom"] = True
                if 0 < down_ask <= PHANTOM_THRESH:
                    w["poly_down_phantom"] = True
            except (KeyError, ValueError):
                continue


def scan_predict(per_window):
    """For each window, did predict YES/NO ever go <= PHANTOM_THRESH?
    NO ask is implied = 1 - yes_bid."""
    with open(PREDICT_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask_implied"))
                w = per_window[epoch]
                if 0 < yes_ask <= PHANTOM_THRESH:
                    w["predict_yes_phantom"] = True
                if 0 < no_ask <= PHANTOM_THRESH:
                    w["predict_no_phantom"] = True
            except (KeyError, ValueError):
                continue


def scan_limitless(per_window):
    """Limitless slug -> epoch is the embedded number in the slug."""
    import re
    with open(LIM_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                slug = r.get("slug", "")
                m = re.search(r"-15-min-(\d{10,13})$", slug)
                if not m:
                    continue
                lim_id = int(m.group(1))
                # Limitless slug ID is the underlying timestamp in ms or sec;
                # we need to find the matching poly market_epoch (rounded to
                # the nearest 15-min boundary at or before this ID).
                if lim_id > 10**12:
                    lim_id_sec = lim_id // 1000
                else:
                    lim_id_sec = lim_id
                window_epoch = (lim_id_sec // 900) * 900
                up_ask = fnum(r.get("best_ask"))      # YES side
                down_ask = fnum(r.get("no_best_ask")) # NO side
                w = per_window[window_epoch]
                if 0 < up_ask <= PHANTOM_THRESH:
                    w["lim_yes_phantom"] = True
                if 0 < down_ask <= PHANTOM_THRESH:
                    w["lim_no_phantom"] = True
            except (KeyError, ValueError):
                continue


def main():
    print("loading outcomes...", file=sys.stderr)
    outcomes = load_outcomes()
    print(f"  {len(outcomes)} outcomes", file=sys.stderr)

    per_window = defaultdict(lambda: {
        "poly_up_phantom": False, "poly_down_phantom": False,
        "predict_yes_phantom": False, "predict_no_phantom": False,
        "lim_yes_phantom": False, "lim_no_phantom": False,
    })

    print("scanning poly per-second...", file=sys.stderr)
    scan_poly(per_window)
    print("scanning predict per-second...", file=sys.stderr)
    scan_predict(per_window)
    print("scanning limitless per-second...", file=sys.stderr)
    scan_limitless(per_window)

    print(f"\ntotal windows seen across all 3 feeds: {len(per_window)}")

    # Classify each window
    results = defaultdict(lambda: {"n": 0, "down_wins": 0, "up_wins": 0, "unknown": 0})
    for epoch, w in per_window.items():
        # Predicted "DOWN wins" if YES-side phantom detected (cheap YES = YES will lose)
        # Predicted "UP wins"   if NO-side phantom detected  (cheap NO = NO will lose)
        yes_phantoms = sum([w["poly_up_phantom"], w["predict_yes_phantom"], w["lim_yes_phantom"]])
        no_phantoms = sum([w["poly_down_phantom"], w["predict_no_phantom"], w["lim_no_phantom"]])

        if yes_phantoms >= 1 and no_phantoms >= 1:
            label = "CONTRADICT"
            predicted = None
        elif yes_phantoms >= 2:
            label = "CONS_DOWN_2"
            predicted = "DOWN"
        elif yes_phantoms == 1:
            label = "CONS_DOWN_1"
            predicted = "DOWN"
        elif no_phantoms >= 2:
            label = "CONS_UP_2"
            predicted = "UP"
        elif no_phantoms == 1:
            label = "CONS_UP_1"
            predicted = "UP"
        else:
            label = "NO_PHANTOM"
            predicted = None

        winner = outcomes.get(epoch, "UNKNOWN")
        b = results[label]
        b["n"] += 1
        if winner == "DOWN":
            b["down_wins"] += 1
        elif winner == "UP":
            b["up_wins"] += 1
        else:
            b["unknown"] += 1

        if predicted == "DOWN":
            b.setdefault("predicted_hits", 0)
            if winner == "DOWN":
                b["predicted_hits"] += 1
        elif predicted == "UP":
            b.setdefault("predicted_hits", 0)
            if winner == "UP":
                b["predicted_hits"] += 1

    print("\n=== Per-window classification + hit rates ===")
    order = ["CONS_DOWN_2", "CONS_DOWN_1", "CONS_UP_2", "CONS_UP_1", "CONTRADICT", "NO_PHANTOM"]
    for label in order:
        b = results.get(label, {"n": 0, "down_wins": 0, "up_wins": 0, "unknown": 0})
        n = b["n"]
        if n == 0:
            print(f"  {label:<13}  n=0")
            continue
        resolved = b["down_wins"] + b["up_wins"]
        hits = b.get("predicted_hits", 0)
        hit_rate = (hits / resolved * 100) if resolved else 0
        print(f"  {label:<13}  n={n:>3}  resolved={resolved:>3}  "
              f"DOWN_won={b['down_wins']:>3} UP_won={b['up_wins']:>3} unknown={b['unknown']:>3}  "
              f"predicted_hits={hits}/{resolved} ({hit_rate:.1f}%)")


if __name__ == "__main__":
    main()
