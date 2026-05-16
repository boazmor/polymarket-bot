#!/usr/bin/env python3
"""5-platform agreement analysis: poly + predict + lim + kalshi + gemini.

For each 15-min market window, find lowest YES and NO ask seen on each
platform. A platform 'signals' a side as losing if that side's ask drops
to <= CHEAP_THRESH at some point in the window.

Count platforms agreeing on YES (predicting DOWN) and NO (predicting UP).
Bucket by agreement strength (1/2/3/4/5 platforms).
Cross-reference with Polymarket outcome for accuracy.
"""

import csv
import re
import sys
from collections import defaultdict


CHEAP_THRESH = 0.10
WINDOW_SEC = 900


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


def update_min(w, key, val):
    if val is None or val <= 0:
        return
    if w[key] is None or val < w[key]:
        w[key] = val


def scan_poly(path, per_window):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                w = per_window[epoch]
                update_min(w, "poly_min_yes", fnum(r.get("up_ask")))
                update_min(w, "poly_min_no",  fnum(r.get("down_ask")))
            except (KeyError, ValueError):
                continue


def scan_predict(path, per_window):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                w = per_window[epoch]
                update_min(w, "pr_min_yes", fnum(r.get("yes_ask")))
                update_min(w, "pr_min_no",  fnum(r.get("no_ask_implied")))
            except (KeyError, ValueError):
                continue


def scan_lim(path, per_window):
    slug_re = re.compile(r"-15-min-(\d{10,13})$")
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                slug = r.get("slug", "")
                m = slug_re.search(slug)
                if not m:
                    continue
                lim_id = int(m.group(1))
                if lim_id > 10**12:
                    lim_id = lim_id // 1000
                epoch = (lim_id // WINDOW_SEC) * WINDOW_SEC
                w = per_window[epoch]
                update_min(w, "lim_min_yes", fnum(r.get("best_ask")))
                update_min(w, "lim_min_no",  fnum(r.get("no_best_ask")))
            except (KeyError, ValueError):
                continue


def scan_kalshi(path, per_window):
    """Kalshi columns: yes_ask, no_ask (direct).
    Market open time -> epoch via open_time field (ISO)."""
    from datetime import datetime as _dt
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                epoch = int(_dt.fromisoformat(ot.replace("Z", "+00:00")).timestamp())
                epoch = (epoch // WINDOW_SEC) * WINDOW_SEC
                w = per_window[epoch]
                update_min(w, "ks_min_yes", fnum(r.get("yes_ask")))
                update_min(w, "ks_min_no",  fnum(r.get("no_ask")))
            except (KeyError, ValueError):
                continue


def scan_gemini(path, per_window):
    """Gemini columns: yes_ask, no_ask_implied. open_time ISO."""
    from datetime import datetime as _dt
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                epoch = int(_dt.fromisoformat(ot.replace("Z", "+00:00")).timestamp())
                epoch = (epoch // WINDOW_SEC) * WINDOW_SEC
                w = per_window[epoch]
                update_min(w, "ge_min_yes", fnum(r.get("yes_ask")))
                update_min(w, "ge_min_no",  fnum(r.get("no_ask_implied")))
            except (KeyError, ValueError):
                continue


def new_window():
    return {
        "poly_min_yes": None, "poly_min_no": None,
        "pr_min_yes":   None, "pr_min_no":   None,
        "lim_min_yes":  None, "lim_min_no":  None,
        "ks_min_yes":   None, "ks_min_no":   None,
        "ge_min_yes":   None, "ge_min_no":   None,
    }


def classify(w):
    yes_signals = []
    no_signals = []
    yes_keys = ["poly_min_yes", "pr_min_yes", "lim_min_yes", "ks_min_yes", "ge_min_yes"]
    no_keys  = ["poly_min_no",  "pr_min_no",  "lim_min_no",  "ks_min_no",  "ge_min_no"]
    plats   = ["poly", "predict", "lim", "kalshi", "gemini"]
    for k, p in zip(yes_keys, plats):
        if w[k] is not None and w[k] <= CHEAP_THRESH:
            yes_signals.append(p)
    for k, p in zip(no_keys, plats):
        if w[k] is not None and w[k] <= CHEAP_THRESH:
            no_signals.append(p)
    if yes_signals and no_signals:
        return None, max(len(yes_signals), len(no_signals)), "CONTRADICT", None, None
    if yes_signals:
        # opposite side = NO; find best NO ask across all 5
        no_asks = []
        for k, p in zip(no_keys, plats):
            if w[k] is not None and 0 < w[k] < 1:
                no_asks.append((w[k], p))
        if not no_asks:
            return None, len(yes_signals), "NO_OPP", yes_signals, None
        no_asks.sort()
        return "DOWN", len(yes_signals), None, yes_signals, no_asks[0]
    if no_signals:
        yes_asks = []
        for k, p in zip(yes_keys, plats):
            if w[k] is not None and 0 < w[k] < 1:
                yes_asks.append((w[k], p))
        if not yes_asks:
            return None, len(no_signals), "NO_OPP", no_signals, None
        yes_asks.sort()
        return "UP", len(no_signals), None, no_signals, yes_asks[0]
    return None, 0, "NO_SIGNAL", None, None


def main():
    per_window = defaultdict(new_window)
    print("scanning poly..."); scan_poly("/root/data_btc_15m_research/combined_per_second.csv", per_window)
    print("scanning predict..."); scan_predict("/root/data_predict_btc_15m/combined_per_second.csv", per_window)
    print("scanning lim..."); scan_lim("/root/data_limitless_btc_15m/combined_per_second.csv", per_window)
    print("scanning kalshi..."); scan_kalshi("/root/data_kalshi_btc_15m/combined_per_second.csv", per_window)
    print("scanning gemini..."); scan_gemini("/root/data_gemini_btc_15m/combined_per_second.csv", per_window)
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(per_window)}, outcomes: {len(outcomes)}\n")

    rows = []
    for ep, w in per_window.items():
        predicted, agree, blocker, plats, best_opp = classify(w)
        winner = outcomes.get(ep)
        if predicted is None:
            continue
        opp_ask, opp_plat = best_opp
        profit_pct = (1 - opp_ask) / opp_ask * 100 if opp_ask > 0 else 0
        rows.append({
            "epoch": ep, "predicted": predicted, "agree": agree,
            "phantom_plats": plats, "best_opp_ask": opp_ask, "best_opp_plat": opp_plat,
            "profit_pct": profit_pct, "winner": winner,
            "correct": (winner == predicted) if winner in ("UP", "DOWN") else None,
        })

    print("=== Per-window: agreement level vs accuracy ===")
    print(f"{'level':<10}{'n':<6}{'wins':<6}{'losses':<8}{'rate':<10}{'med_profit':<12}")
    for level in (1, 2, 3, 4, 5):
        bucket = [r for r in rows if r["agree"] == level]
        if not bucket:
            print(f"  {level}-plat   n=0")
            continue
        resolved = [r for r in bucket if r["correct"] is not None]
        wins = sum(1 for r in resolved if r["correct"])
        losses = sum(1 for r in resolved if r["correct"] is False)
        rate = (wins / len(resolved) * 100) if resolved else 0
        profits = sorted(r["profit_pct"] for r in bucket)
        med_profit = profits[len(profits)//2] if profits else 0
        print(f"  {level}-plat   {len(bucket):<6}{wins:<6}{losses:<8}{rate:<10.1f}{med_profit:<12.1f}")

    # >= 2 agreement gold zone (profit 25-100)
    print("\n=== GOLD: agree>=2 AND profit 25-100% ===")
    gold = [r for r in rows if r["agree"] >= 2 and 25 <= r["profit_pct"] < 100]
    resolved = [r for r in gold if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    print(f"  n={len(gold)} resolved={len(resolved)} wins={wins} rate={wins/max(len(resolved),1)*100:.1f}%")

    # Broader 2/5: profit any positive
    print("\n=== BROAD: agree>=2 AND profit>=10% ===")
    broad = [r for r in rows if r["agree"] >= 2 and r["profit_pct"] >= 10]
    resolved = [r for r in broad if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    avg_profit = sum(r["profit_pct"] for r in broad) / len(broad) if broad else 0
    print(f"  n={len(broad)} resolved={len(resolved)} wins={wins} rate={wins/max(len(resolved),1)*100:.1f}% avg_profit={avg_profit:.1f}%")


if __name__ == "__main__":
    main()
