#!/usr/bin/env python3
"""For each window in the GOLD ZONE (agreement>=2, profit 25-100%), check
how much USD depth was available on the opposite side at the favourable
price. This tells us if a $100 trade is realistic.

We need:
- Polymarket per-second has depth columns: up_usd_le_029 etc. We'll use the
  closest matching depth bucket for the trade price.
- Predict.fun per-second has yes_ask_size, no_bid_size, etc.
- Limitless per-second has best_ask_size_usd, no_best_ask_size_usd.
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


def scan_poly(path, per_window):
    """Track min ask on each side + corresponding USD-best depth at that
    moment, plus depth at price levels <= 0.35 (a generous opposite-side
    ceiling for the bot)."""
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                w = per_window[epoch]
                # YES side
                up_ask = fnum(r.get("up_ask"))
                up_usd_best = fnum(r.get("up_usd_best"))
                up_usd_le_035 = fnum(r.get("up_usd_le_035"))
                if 0 < up_ask:
                    if w["poly_yes_min"] is None or up_ask < w["poly_yes_min"]:
                        w["poly_yes_min"] = up_ask
                        w["poly_yes_min_depth"] = up_usd_best
                    if w["poly_yes_max_depth_le35"] < up_usd_le_035:
                        w["poly_yes_max_depth_le35"] = up_usd_le_035
                # NO side
                down_ask = fnum(r.get("down_ask"))
                down_usd_best = fnum(r.get("down_usd_best"))
                down_usd_le_035 = fnum(r.get("down_usd_le_035"))
                if 0 < down_ask:
                    if w["poly_no_min"] is None or down_ask < w["poly_no_min"]:
                        w["poly_no_min"] = down_ask
                        w["poly_no_min_depth"] = down_usd_best
                    if w["poly_no_max_depth_le35"] < down_usd_le_035:
                        w["poly_no_max_depth_le35"] = down_usd_le_035
            except (KeyError, ValueError):
                continue


def scan_predict(path, per_window):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                w = per_window[epoch]
                yes_ask = fnum(r.get("yes_ask"))
                yes_ask_size = fnum(r.get("yes_ask_size"))
                yes_ask_usd = yes_ask * yes_ask_size
                if 0 < yes_ask:
                    if w["pr_yes_min"] is None or yes_ask < w["pr_yes_min"]:
                        w["pr_yes_min"] = yes_ask
                        w["pr_yes_min_depth"] = yes_ask_usd
                    if yes_ask <= 0.35:
                        w["pr_yes_max_depth_le35"] = max(w["pr_yes_max_depth_le35"], yes_ask_usd)
                # NO side: implied = 1 - yes_bid; depth equals yes_bid USD
                no_ask = fnum(r.get("no_ask_implied"))
                no_ask_usd = fnum(r.get("no_ask_usd"))  # depth for NO ask
                if 0 < no_ask:
                    if w["pr_no_min"] is None or no_ask < w["pr_no_min"]:
                        w["pr_no_min"] = no_ask
                        w["pr_no_min_depth"] = no_ask_usd
                    if no_ask <= 0.35:
                        w["pr_no_max_depth_le35"] = max(w["pr_no_max_depth_le35"], no_ask_usd)
            except (KeyError, ValueError):
                continue


def scan_lim(path, per_window, slug_regex, window_sec):
    with open(path) as f:
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
                yes_ask = fnum(r.get("best_ask"))
                yes_ask_usd = fnum(r.get("best_ask_size_usd"))
                if 0 < yes_ask:
                    if w["lim_yes_min"] is None or yes_ask < w["lim_yes_min"]:
                        w["lim_yes_min"] = yes_ask
                        w["lim_yes_min_depth"] = yes_ask_usd
                    if yes_ask <= 0.35:
                        w["lim_yes_max_depth_le35"] = max(w["lim_yes_max_depth_le35"], yes_ask_usd)
                no_ask = fnum(r.get("no_best_ask"))
                no_ask_usd = fnum(r.get("no_best_ask_size_usd"))
                if 0 < no_ask:
                    if w["lim_no_min"] is None or no_ask < w["lim_no_min"]:
                        w["lim_no_min"] = no_ask
                        w["lim_no_min_depth"] = no_ask_usd
                    if no_ask <= 0.35:
                        w["lim_no_max_depth_le35"] = max(w["lim_no_max_depth_le35"], no_ask_usd)
            except (KeyError, ValueError):
                continue


def new_window():
    return {
        "poly_yes_min": None, "poly_yes_min_depth": 0, "poly_yes_max_depth_le35": 0,
        "poly_no_min":  None, "poly_no_min_depth":  0, "poly_no_max_depth_le35":  0,
        "pr_yes_min":   None, "pr_yes_min_depth":   0, "pr_yes_max_depth_le35":   0,
        "pr_no_min":    None, "pr_no_min_depth":    0, "pr_no_max_depth_le35":    0,
        "lim_yes_min":  None, "lim_yes_min_depth":  0, "lim_yes_max_depth_le35":  0,
        "lim_no_min":   None, "lim_no_min_depth":   0, "lim_no_max_depth_le35":   0,
    }


def run(label, window_sec, poly_path, predict_path, lim_path, outcomes_path, slug_regex):
    print(f"\n========== {label}: window {window_sec}s ==========")
    outcomes = load_outcomes(outcomes_path)
    per_window = defaultdict(new_window)
    scan_poly(poly_path, per_window)
    scan_predict(predict_path, per_window)
    scan_lim(lim_path, per_window, slug_regex, window_sec)
    print(f"  windows: {len(per_window)}, outcomes: {len(outcomes)}")

    rows = []
    for ep, w in per_window.items():
        yes_signals = sum(1 for k in ("poly_yes_min", "pr_yes_min", "lim_yes_min")
                          if w[k] is not None and w[k] <= CHEAP_THRESH)
        no_signals = sum(1 for k in ("poly_no_min", "pr_no_min", "lim_no_min")
                         if w[k] is not None and w[k] <= CHEAP_THRESH)
        if yes_signals == 0 and no_signals == 0:
            continue
        if yes_signals > 0 and no_signals > 0:
            continue
        if yes_signals >= 2:
            predicted = "DOWN"
            agreement = yes_signals
            opp_side = "no"
        elif no_signals >= 2:
            predicted = "UP"
            agreement = no_signals
            opp_side = "yes"
        else:
            continue  # require >=2 agreement for gold

        winner = outcomes.get(ep, "UNKNOWN")
        # Aggregate opposite-side depth at <= 0.35 across the 3 platforms
        opp_depth_le35_per_platform = [
            w[f"poly_{opp_side}_max_depth_le35"],
            w[f"pr_{opp_side}_max_depth_le35"],
            w[f"lim_{opp_side}_max_depth_le35"],
        ]
        total_le35 = sum(opp_depth_le35_per_platform)
        best_per_plat = max(opp_depth_le35_per_platform)

        # Min opposite-side ask and its depth (any platform)
        opp_min_per_platform = [
            (w[f"poly_{opp_side}_min"], w[f"poly_{opp_side}_min_depth"]),
            (w[f"pr_{opp_side}_min"],   w[f"pr_{opp_side}_min_depth"]),
            (w[f"lim_{opp_side}_min"],  w[f"lim_{opp_side}_min_depth"]),
        ]
        valid = [(a, d) for a, d in opp_min_per_platform if a is not None and 0 < a < 1]
        if not valid:
            continue
        best_opp_ask, best_opp_depth = min(valid, key=lambda x: x[0])
        profit_pct = (1.0 - best_opp_ask) / best_opp_ask * 100

        rows.append({
            "epoch": ep, "predicted": predicted, "agreement": agreement,
            "best_opp_ask": best_opp_ask, "best_opp_depth_usd": best_opp_depth,
            "total_depth_le35": total_le35, "best_plat_depth_le35": best_per_plat,
            "profit_pct": profit_pct, "winner": winner,
            "correct": (winner == predicted) if winner in ("UP", "DOWN") else None,
        })

    # GOLD zone: agreement>=2, profit 25-100%
    print(f"\n  GOLD zone analysis: agreement>=2, profit 25-100%")
    gold = [r for r in rows if 25 <= r["profit_pct"] < 100]
    resolved = [r for r in gold if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    print(f"    n={len(gold)} resolved={len(resolved)} wins={wins} ({wins/max(len(resolved),1)*100:.1f}%)")
    if gold:
        # Depth stats
        depths_best = sorted([r["best_opp_depth_usd"] for r in gold])
        depths_total = sorted([r["total_depth_le35"] for r in gold])
        ge_100 = sum(1 for r in gold if r["total_depth_le35"] >= 100)
        ge_50 = sum(1 for r in gold if r["total_depth_le35"] >= 50)
        ge_10 = sum(1 for r in gold if r["total_depth_le35"] >= 10)
        ge_2 = sum(1 for r in gold if r["total_depth_le35"] >= 2)
        n = len(gold)
        print(f"    depth at best opp ask (USD): "
              f"min={depths_best[0]:.2f} med={depths_best[n//2]:.2f} max={depths_best[-1]:.2f}")
        print(f"    total depth at <=0.35 (sum 3 plats): "
              f"min={depths_total[0]:.2f} med={depths_total[n//2]:.2f} max={depths_total[-1]:.2f}")
        print(f"    windows with depth >= $2:   {ge_2}/{n} ({ge_2/n*100:.0f}%)")
        print(f"    windows with depth >= $10:  {ge_10}/{n} ({ge_10/n*100:.0f}%)")
        print(f"    windows with depth >= $50:  {ge_50}/{n} ({ge_50/n*100:.0f}%)")
        print(f"    windows with depth >= $100: {ge_100}/{n} ({ge_100/n*100:.0f}%)")

    # Broader zone
    print(f"\n  BROADER zone analysis: agreement>=2, profit 10-200%")
    broad = [r for r in rows if 10 <= r["profit_pct"] < 200]
    resolved = [r for r in broad if r["correct"] is not None]
    wins = sum(1 for r in resolved if r["correct"])
    print(f"    n={len(broad)} resolved={len(resolved)} wins={wins} ({wins/max(len(resolved),1)*100:.1f}%)")
    if broad:
        ge_100 = sum(1 for r in broad if r["total_depth_le35"] >= 100)
        ge_50 = sum(1 for r in broad if r["total_depth_le35"] >= 50)
        ge_10 = sum(1 for r in broad if r["total_depth_le35"] >= 10)
        ge_2 = sum(1 for r in broad if r["total_depth_le35"] >= 2)
        n = len(broad)
        print(f"    windows with depth >= $2:   {ge_2}/{n} ({ge_2/n*100:.0f}%)")
        print(f"    windows with depth >= $10:  {ge_10}/{n} ({ge_10/n*100:.0f}%)")
        print(f"    windows with depth >= $50:  {ge_50}/{n} ({ge_50/n*100:.0f}%)")
        print(f"    windows with depth >= $100: {ge_100}/{n} ({ge_100/n*100:.0f}%)")


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
