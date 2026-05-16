#!/usr/bin/env python3
"""Multi-platform agreement analysis with broader phantom threshold.

For each market window, look at lowest YES and NO ask seen ANYWHERE in the
window on each platform. A platform 'signals' a side as losing if that side's
ask dropped to <= CHEAP_THRESH at some point.

Then classify per window by how many platforms (1, 2, or 3) signal the SAME
side as losing. For each agreement level, report:
  - count of windows
  - win rate
  - distribution of best opposite-side ask (the price we'd pay)
  - distribution of profit margin

CHEAP_THRESH = 0.10 (broader than before to capture more opportunities)
"""

import csv
import re
import sys
from collections import defaultdict


CHEAP_THRESH = 0.10


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


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


def scan_window_mins(poly_path, predict_path, lim_path, slug_regex, window_sec):
    """Per window, find min YES ask and min NO ask per platform, plus the
    best (lowest) ask on each side across all 3 platforms."""
    per_window = defaultdict(lambda: {
        "poly_min_yes": None, "poly_min_no": None,
        "pr_min_yes": None,   "pr_min_no": None,
        "lim_min_yes": None,  "lim_min_no": None,
    })

    def update_min(d, key, val):
        if val is None or val <= 0:
            return
        if d[key] is None or val < d[key]:
            d[key] = val

    with open(poly_path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                w = per_window[epoch]
                update_min(w, "poly_min_yes", fnum(r.get("up_ask")))
                update_min(w, "poly_min_no", fnum(r.get("down_ask")))
            except (KeyError, ValueError):
                continue

    with open(predict_path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                w = per_window[epoch]
                update_min(w, "pr_min_yes", fnum(r.get("yes_ask")))
                update_min(w, "pr_min_no", fnum(r.get("no_ask_implied")))
            except (KeyError, ValueError):
                continue

    with open(lim_path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                slug = r.get("slug", "")
                m = slug_regex.search(slug)
                if not m:
                    continue
                lim_id = int(m.group(1))
                if lim_id > 10**12:
                    lim_id = lim_id // 1000
                epoch = (lim_id // window_sec) * window_sec
                w = per_window[epoch]
                update_min(w, "lim_min_yes", fnum(r.get("best_ask")))
                update_min(w, "lim_min_no", fnum(r.get("no_best_ask")))
            except (KeyError, ValueError):
                continue
    return per_window


def classify(w):
    """Return (predicted_winner, agreement_count, best_opposite_ask)."""
    # Per platform: did its YES drop below CHEAP_THRESH? did its NO?
    yes_signals = sum(1 for k in ("poly_min_yes", "pr_min_yes", "lim_min_yes")
                      if w[k] is not None and w[k] <= CHEAP_THRESH)
    no_signals = sum(1 for k in ("poly_min_no", "pr_min_no", "lim_min_no")
                     if w[k] is not None and w[k] <= CHEAP_THRESH)

    if yes_signals == 0 and no_signals == 0:
        return None, 0, None, None
    if yes_signals > 0 and no_signals > 0:
        return None, max(yes_signals, no_signals), None, "CONTRADICT"

    if yes_signals > 0:
        predicted = "DOWN"
        agreement = yes_signals
        # opposite side = NO; find best (lowest) NO ask across platforms
        no_asks = [w[k] for k in ("poly_min_no", "pr_min_no", "lim_min_no") if w[k] is not None and w[k] > 0]
        best_opp = min(no_asks) if no_asks else None
        cheap_side_min = min([w[k] for k in ("poly_min_yes", "pr_min_yes", "lim_min_yes") if w[k] is not None and w[k] > 0])
        return predicted, agreement, best_opp, cheap_side_min
    else:
        predicted = "UP"
        agreement = no_signals
        yes_asks = [w[k] for k in ("poly_min_yes", "pr_min_yes", "lim_min_yes") if w[k] is not None and w[k] > 0]
        best_opp = min(yes_asks) if yes_asks else None
        cheap_side_min = min([w[k] for k in ("poly_min_no", "pr_min_no", "lim_min_no") if w[k] is not None and w[k] > 0])
        return predicted, agreement, best_opp, cheap_side_min


def run(label, window_sec, poly_path, predict_path, lim_path, outcomes_path, slug_regex):
    print(f"\n========== {label}: window {window_sec}s, cheap threshold {CHEAP_THRESH} ==========")
    outcomes = load_outcomes(outcomes_path)
    per_window = scan_window_mins(poly_path, predict_path, lim_path, slug_regex, window_sec)
    print(f"  windows: {len(per_window)}, outcomes: {len(outcomes)}")

    rows = []
    for epoch, w in per_window.items():
        predicted, agreement, best_opp, cheap_min = classify(w)
        winner = outcomes.get(epoch, "UNKNOWN")
        if predicted is None or best_opp is None:
            continue
        profit_pct = (1.0 - best_opp) / best_opp * 100 if best_opp > 0 else 0
        correct = (winner == predicted) if winner in ("UP", "DOWN") else None
        rows.append({
            "epoch": epoch, "predicted": predicted, "agreement": agreement,
            "cheap_min": cheap_min, "best_opp": best_opp, "profit_pct": profit_pct,
            "winner": winner, "correct": correct,
        })

    print(f"  windows with clear signal: {len(rows)}")

    # Breakdown by agreement count
    print(f"\n  by-agreement breakdown:")
    print(f"  {'agreement':<12}{'n':<6}{'wins':<6}{'losses':<8}{'rate':<10}{'med_opp':<10}{'med_profit':<12}")
    for ag in (1, 2, 3):
        bucket = [r for r in rows if r["agreement"] == ag]
        if not bucket:
            print(f"  {ag}-platform   n=0")
            continue
        resolved = [r for r in bucket if r["correct"] is not None]
        wins = sum(1 for r in resolved if r["correct"])
        losses = sum(1 for r in resolved if r["correct"] is False)
        rate = (wins / len(resolved) * 100) if resolved else 0
        opps = sorted([r["best_opp"] for r in bucket])
        med_opp = opps[len(opps) // 2] if opps else 0
        profits = sorted([r["profit_pct"] for r in bucket])
        med_profit = profits[len(profits) // 2] if profits else 0
        print(f"  {ag}-platform   {len(bucket):<6}{wins:<6}{losses:<8}{rate:<10.1f}{med_opp:<10.3f}{med_profit:<12.1f}")

    # Bucket by profit margin (more important than agreement)
    print(f"\n  by profit margin (filter quality):")
    profit_buckets = [
        ("0-5%",    0,    5),
        ("5-10%",   5,   10),
        ("10-25%", 10,   25),
        ("25-50%", 25,   50),
        ("50-100%",50, 100),
        (">100%", 100, 99999),
    ]
    for name, lo, hi in profit_buckets:
        bucket = [r for r in rows if lo <= r["profit_pct"] < hi]
        resolved = [r for r in bucket if r["correct"] is not None]
        wins = sum(1 for r in resolved if r["correct"])
        rate = (wins / len(resolved) * 100) if resolved else 0
        # Sub-bucket: how many had >=2 platforms agree
        strong_agree = [r for r in bucket if r["agreement"] >= 2]
        strong_res = [r for r in strong_agree if r["correct"] is not None]
        strong_wins = sum(1 for r in strong_res if r["correct"])
        strong_rate = (strong_wins / len(strong_res) * 100) if strong_res else 0
        print(f"    {name:<8}  n={len(bucket):>3}  resolved={len(resolved):>3}  rate={rate:>5.1f}%  "
              f"|  with>=2 agree: n={len(strong_agree):>3} rate={strong_rate:.1f}%")

    # Top quality bucket: agreement>=2 AND profit>=25%
    print(f"\n  GOLD ZONE: agreement>=2 AND profit_pct in [25, 100]:")
    gold = [r for r in rows if r["agreement"] >= 2 and 25 <= r["profit_pct"] < 100]
    resolved = [r for r in gold if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    rate = (wins / len(resolved) * 100) if resolved else 0
    avg_profit = sum(r["profit_pct"] for r in gold) / len(gold) if gold else 0
    print(f"    n={len(gold)}  resolved={len(resolved)}  wins={wins}  rate={rate:.1f}%  avg_profit_pct={avg_profit:.1f}%")

    # Also: agreement>=1 (any signal) profit>=10
    print(f"\n  BROADER: agreement>=1 AND profit_pct >= 10:")
    broad = [r for r in rows if r["agreement"] >= 1 and r["profit_pct"] >= 10]
    resolved = [r for r in broad if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    rate = (wins / len(resolved) * 100) if resolved else 0
    avg_profit = sum(r["profit_pct"] for r in broad) / len(broad) if broad else 0
    print(f"    n={len(broad)}  resolved={len(resolved)}  wins={wins}  rate={rate:.1f}%  avg_profit_pct={avg_profit:.1f}%")


if __name__ == "__main__":
    run(
        "15-min", 900,
        "/root/data_btc_15m_research/combined_per_second.csv",
        "/root/data_predict_btc_15m/combined_per_second.csv",
        "/root/data_limitless_btc_15m/combined_per_second.csv",
        "/root/data_btc_15m_research/market_outcomes.csv",
        re.compile(r"-15-min-(\d{10,13})$"),
    )
    run(
        "1-hour", 3600,
        "/root/data_btc_1h_research/combined_per_second.csv",
        "/root/data_predict_btc_1h/combined_per_second.csv",
        "/root/data_limitless_btc_1h/combined_per_second.csv",
        "/root/data_btc_1h_research/market_outcomes.csv",
        re.compile(r"-hourly-(\d{10,13})$"),
    )
