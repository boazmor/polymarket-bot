#!/usr/bin/env python3
"""5-min strike spread between Poly (Chainlink) and Predict (Binance).
Run on USA server which has poly 5m + predict 5m data.

Outcomes computed offline doesn't work (no binance ticks on USA). So we
use poly's market_outcomes.csv if available.
"""

import csv
import sys
from collections import defaultdict


WINDOW_SEC = 300


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_poly_outcomes():
    out = {}
    try:
        with open("/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv") as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    out[int(r["market_epoch"])] = r["winner_side"]
                except (KeyError, ValueError):
                    pass
    except FileNotFoundError:
        pass
    return out


def load_poly_strikes():
    seen = {}
    try:
        with open("/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv") as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    ep_raw = r.get("market_epoch")
                    if not ep_raw:
                        continue
                    ep = int(ep_raw)
                    if ep in seen:
                        continue
                    v = fnum(r.get("target_chainlink_at_open"))
                    if v > 0:
                        seen[ep] = v
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return seen


def load_predict_strikes():
    seen = {}
    try:
        with open("/root/data_predict_btc_5m/combined_per_second.csv") as f:
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
    except FileNotFoundError:
        pass
    return seen


def main():
    print("loading 5m strikes and outcomes...", file=sys.stderr)
    p = load_poly_strikes()
    pr = load_predict_strikes()
    out = load_poly_outcomes()
    print(f"poly: {len(p)}, predict: {len(pr)}, outcomes: {len(out)}", file=sys.stderr)

    # Join
    common = []
    for ep, pv in p.items():
        if ep in pr:
            common.append((ep, pv, pr[ep]))
    print(f"common windows: {len(common)}")
    print()

    # Spread distribution
    spreads = [(ep, pv - prv) for ep, pv, prv in common]
    diff_only = sorted([d for _, d in spreads])
    n = len(diff_only)
    if n > 0:
        print(f"=== הפרשי טרגט פולי פחות פרדיקט ===")
        print(f"  סך הכל: {n}")
        print(f"  מינ: {diff_only[0]:.2f} דולר")
        print(f"  חציון: {diff_only[n//2]:.2f} דולר")
        print(f"  מקס: {diff_only[-1]:.2f} דולר")

    # Buckets by abs spread
    print()
    print(f"=== חלוקה לפי גודל פער מוחלט ===")
    for lo, hi, name in [(0, 10, "0-10 דולר"),
                          (10, 30, "10-30 דולר"),
                          (30, 100, "30-100 דולר"),
                          (100, 99999, "מעל 100 דולר")]:
        bucket = [(ep, d) for ep, d in spreads if lo <= abs(d) < hi]
        with_outcome = [(ep, d) for ep, d in bucket if out.get(ep) in ("UP", "DOWN")]
        n_up = sum(1 for ep, _ in with_outcome if out[ep] == "UP")
        n_down = sum(1 for ep, _ in with_outcome if out[ep] == "DOWN")
        print(f"  {name}: {len(bucket)} חלונות, {n_up} UP, {n_down} DOWN")

    # Hypothesis: poly strike > predict by X -> poly_chainlink ends DOWN more
    print()
    print(f"=== טסט: פולי חורג גבוה -> דאון? ===")
    for thr_usd in (10, 30, 50, 100):
        higher_p = [(ep, d) for ep, d in spreads if d > thr_usd and out.get(ep) in ("UP", "DOWN")]
        lower_p  = [(ep, d) for ep, d in spreads if d < -thr_usd and out.get(ep) in ("UP", "DOWN")]
        down_when_high = sum(1 for ep, _ in higher_p if out[ep] == "DOWN")
        up_when_low = sum(1 for ep, _ in lower_p if out[ep] == "UP")
        if higher_p:
            print(f"  פולי גבוה ב-{thr_usd}+: {down_when_high}/{len(higher_p)} ({down_when_high/len(higher_p)*100:.0f}% DOWN)")
        if lower_p:
            print(f"  פולי נמוך ב-{thr_usd}+: {up_when_low}/{len(lower_p)} ({up_when_low/len(lower_p)*100:.0f}% UP)")


if __name__ == "__main__":
    main()
