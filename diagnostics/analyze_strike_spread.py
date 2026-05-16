#!/usr/bin/env python3
"""Strike-spread analysis: do extreme strikes predict outcomes?

For each 15-min window, gather strikes from 4 platforms with available data:
  - Polymarket (target_chainlink_at_open, col 13)
  - Predict.fun (strike, col 7)
  - Kalshi (floor_strike, col 6)
  - Gemini (strike, col 6)
Limitless 15m has no per-second strike column.

For each window:
  - Compute mean strike and spread (max-min)
  - Identify outlier platforms (strike >$ from mean)
  - For each platform, check outcome:
    - if strike is HIGH outlier -> does that platform's market end DOWN more?
    - if strike is LOW outlier -> end UP more?
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime


WINDOW_SEC = 900


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def iso_to_epoch(iso):
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return 0


def load_outcomes(path):
    out = {}
    try:
        with open(path) as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    out[int(r["market_epoch"])] = r["winner_side"]
                except (KeyError, ValueError):
                    pass
    except FileNotFoundError:
        pass
    return out


def collect_strikes_poly(path, strikes):
    # Take FIRST seen target per window
    seen = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_epoch"])
                if ep in seen:
                    continue
                v = fnum(r.get("target_chainlink_at_open"))
                if v > 0:
                    seen[ep] = v
            except (KeyError, ValueError):
                continue
    for ep, v in seen.items():
        strikes[ep]["poly"] = v


def collect_strikes_predict(path, strikes):
    seen = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep_raw = r.get("market_open_epoch")
                if not ep_raw:
                    continue
                ep = int(ep_raw)
                if ep in seen:
                    continue
                v = fnum(r.get("strike"))
                if v > 0:
                    seen[ep] = v
            except (KeyError, ValueError):
                continue
    for ep, v in seen.items():
        strikes[ep]["predict"] = v


def collect_strikes_kalshi(path, strikes):
    seen = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                if ep in seen:
                    continue
                v = fnum(r.get("floor_strike"))
                if v > 0:
                    seen[ep] = v
            except (KeyError, ValueError):
                continue
    for ep, v in seen.items():
        strikes[ep]["kalshi"] = v


def collect_strikes_gemini(path, strikes):
    seen = {}
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                if ep in seen:
                    continue
                v = fnum(r.get("strike"))
                if v > 0:
                    seen[ep] = v
            except (KeyError, ValueError):
                continue
    for ep, v in seen.items():
        strikes[ep]["gemini"] = v


def main():
    print("loading strikes from 4 platforms...", file=sys.stderr)
    strikes = defaultdict(dict)
    collect_strikes_poly("/root/data_btc_15m_research/combined_per_second.csv", strikes)
    collect_strikes_predict("/root/data_predict_btc_15m/combined_per_second.csv", strikes)
    collect_strikes_kalshi("/root/data_kalshi_btc_15m/combined_per_second.csv", strikes)
    collect_strikes_gemini("/root/data_gemini_btc_15m/combined_per_second.csv", strikes)
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows with strikes: {len(strikes)}, outcomes: {len(outcomes)}", file=sys.stderr)

    # Compute spread for each window
    spreads = []
    for ep, sd in strikes.items():
        vals = list(sd.values())
        if len(vals) < 2:
            continue
        spread = max(vals) - min(vals)
        mean = sum(vals) / len(vals)
        spreads.append((ep, spread, mean, sd))

    print()
    print(f"=== חלוקת פערי טרגט בין הפלטפורמות ===")
    spread_only = sorted([s[1] for s in spreads])
    if spread_only:
        n = len(spread_only)
        print(f"  סך החלונות: {n}")
        print(f"  פער מינימלי: {spread_only[0]:.2f} דולר")
        print(f"  פער חציוני: {spread_only[n//2]:.2f} דולר")
        print(f"  פער ממוצע: {sum(spread_only)/n:.2f} דולר")
        print(f"  פער מקסימלי: {spread_only[-1]:.2f} דולר")

    # Bucket by spread
    print()
    print(f"=== חלוקה לקבוצות פער טרגט ===")
    for lo, hi, name in [(0, 50, "פער 0-50 דולר, טרגטים זהים"),
                          (50, 150, "פער 50-150 דולר, קרובים"),
                          (150, 300, "פער 150-300 דולר"),
                          (300, 1000, "פער 300-1000 דולר, רחב"),
                          (1000, 99999, "פער גדול מ-1000 דולר, קיצוני")]:
        bucket = [s for s in spreads if lo <= s[1] < hi]
        resolved = [s for s in bucket if outcomes.get(s[0]) in ("UP", "DOWN")]
        n_up = sum(1 for s in resolved if outcomes[s[0]] == "UP")
        n_down = sum(1 for s in resolved if outcomes[s[0]] == "DOWN")
        print(f"  {name}: {len(bucket)} חלונות, {n_up} UP, {n_down} DOWN")

    # Outlier hypothesis: if a platform's strike is HIGH outlier, that platform
    # ends DOWN more often (because BTC has to go further to be above strike)
    print()
    print(f"=== טסט היפותזה: טרגט גבוה חורג -> דאון ניצח? ===")
    OUTLIER_USD = 30  # platform is outlier if > $30 away from mean
    high_correct = 0
    high_wrong = 0
    low_correct = 0
    low_wrong = 0
    for ep, spread, mean, sd in spreads:
        winner = outcomes.get(ep)
        if winner not in ("UP", "DOWN"):
            continue
        for plat, strike in sd.items():
            diff = strike - mean
            if diff > OUTLIER_USD:
                # platform's strike is high outlier -> we predict DOWN
                if winner == "DOWN":
                    high_correct += 1
                else:
                    high_wrong += 1
            elif diff < -OUTLIER_USD:
                # low outlier -> predict UP
                if winner == "UP":
                    low_correct += 1
                else:
                    low_wrong += 1
    print(f"  טרגט חורג גבוה > +$30 מהממוצע -> דיוק DOWN: {high_correct}/{high_correct+high_wrong} ({high_correct/max(high_correct+high_wrong,1)*100:.0f}%)")
    print(f"  טרגט חורג נמוך > -$30 מהממוצע -> דיוק UP: {low_correct}/{low_correct+low_wrong} ({low_correct/max(low_correct+low_wrong,1)*100:.0f}%)")

    # Bigger outlier
    OUTLIER_USD = 100
    high_correct = 0
    high_wrong = 0
    low_correct = 0
    low_wrong = 0
    for ep, spread, mean, sd in spreads:
        winner = outcomes.get(ep)
        if winner not in ("UP", "DOWN"):
            continue
        for plat, strike in sd.items():
            diff = strike - mean
            if diff > OUTLIER_USD:
                if winner == "DOWN":
                    high_correct += 1
                else:
                    high_wrong += 1
            elif diff < -OUTLIER_USD:
                if winner == "UP":
                    low_correct += 1
                else:
                    low_wrong += 1
    print()
    print(f"  טרגט חורג גבוה > +$100 -> דיוק DOWN: {high_correct}/{high_correct+high_wrong} ({high_correct/max(high_correct+high_wrong,1)*100:.0f}%)")
    print(f"  טרגט חורג נמוך > -$100 -> דיוק UP: {low_correct}/{low_correct+low_wrong} ({low_correct/max(low_correct+low_wrong,1)*100:.0f}%)")


if __name__ == "__main__":
    main()
