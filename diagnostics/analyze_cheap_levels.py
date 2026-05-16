#!/usr/bin/env python3
"""Per-window analysis of cheap-side prices and corresponding other-side prices.

For each market window:
  - Find the LOWEST ask seen across all 3 platforms (either YES or NO side).
  - Determine which side that cheap was on (YES => predict DOWN wins,
                                              NO  => predict UP wins).
  - Find the BEST (lowest) ask on the OPPOSITE side across all 3 platforms,
    at the same approximate moment.
  - Compute profit margin if we bought the opposite side and won.

Bucket by cheap level (1, 2, 3, 4 cents).
Run for 15-min and 1-hour separately.
"""

import csv
import re
import sys
from collections import defaultdict


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


def load_poly(path):
    """epoch -> list of (sec_in_window, up_ask, down_ask)."""
    data = defaultdict(list)
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                es = int(r["epoch_sec"])
                up_ask = fnum(r.get("up_ask"))
                down_ask = fnum(r.get("down_ask"))
                data[epoch].append((es - epoch, up_ask, down_ask))
            except (KeyError, ValueError):
                continue
    return data


def load_predict(path):
    """epoch -> list of (sec_in_window, yes_ask, no_ask_implied)."""
    data = defaultdict(list)
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask_implied"))
                data[epoch].append((es - epoch, yes_ask, no_ask))
            except (KeyError, ValueError):
                continue
    return data


def load_lim(path, slug_regex, window_sec):
    """epoch -> list of (sec_in_window, yes_ask, no_ask)."""
    data = defaultdict(list)
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
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("best_ask"))
                no_ask = fnum(r.get("no_best_ask"))
                data[epoch].append((es - epoch, yes_ask, no_ask))
            except (KeyError, ValueError):
                continue
    return data


def analyze_window(epoch, poly_pts, pr_pts, lim_pts, outcomes):
    """Find the lowest ask anywhere in the window (any platform, any side).
    Then find the best opposite-side ask near that moment.
    Returns dict or None."""
    # Build per-second snapshot
    snap = {}
    for sec, up_ask, down_ask in poly_pts:
        s = snap.setdefault(sec, {})
        s["poly_yes"] = up_ask
        s["poly_no"] = down_ask
    for sec, yes_ask, no_ask in pr_pts:
        s = snap.setdefault(sec, {})
        s["pr_yes"] = yes_ask
        s["pr_no"] = no_ask
    for sec, yes_ask, no_ask in lim_pts:
        s = snap.setdefault(sec, {})
        s["lim_yes"] = yes_ask
        s["lim_no"] = no_ask

    # Find lowest ask across the window
    best_cheap = None
    for sec, s in snap.items():
        for side in ("yes", "no"):
            for plat in ("poly", "pr", "lim"):
                key = f"{plat}_{side}"
                v = s.get(key, 0)
                if 0 < v <= 0.05:
                    if best_cheap is None or v < best_cheap[0]:
                        best_cheap = (v, sec, side, plat)
                    elif v == best_cheap[0] and sec < best_cheap[1]:
                        best_cheap = (v, sec, side, plat)
    if best_cheap is None:
        return None

    cheap_price, cheap_sec, cheap_side, cheap_plat = best_cheap
    opposite_side = "no" if cheap_side == "yes" else "yes"
    predicted_winner = "DOWN" if cheap_side == "yes" else "UP"

    # Find best (lowest) ask on opposite side, looking at any time in window
    # AFTER the cheap was first seen (since we'd buy after detection).
    best_other = None
    for sec, s in snap.items():
        if sec < cheap_sec:
            continue
        for plat in ("poly", "pr", "lim"):
            v = s.get(f"{plat}_{opposite_side}", 0)
            if 0 < v < 1.0:
                if best_other is None or v < best_other[0]:
                    best_other = (v, sec, plat)
    if best_other is None:
        return None

    other_price, other_sec, other_plat = best_other
    profit_pct = (1.0 - other_price) / other_price * 100 if other_price > 0 else 0
    winner = outcomes.get(epoch, "UNKNOWN")

    return {
        "epoch": epoch,
        "cheap_price": cheap_price,
        "cheap_sec": cheap_sec,
        "cheap_side": cheap_side,
        "cheap_plat": cheap_plat,
        "other_price": other_price,
        "other_sec": other_sec,
        "other_plat": other_plat,
        "profit_pct": profit_pct,
        "predicted_winner": predicted_winner,
        "actual_winner": winner,
        "predicted_correct": (winner == predicted_winner) if winner in ("UP", "DOWN") else None,
    }


def run(label, window_sec, poly_path, predict_path, lim_path, outcomes_path, slug_regex):
    print(f"\n========== {label}: window {window_sec}s ==========")
    outcomes = load_outcomes(outcomes_path)
    poly = load_poly(poly_path)
    pr = load_predict(predict_path)
    lim = load_lim(lim_path, slug_regex, window_sec)
    all_epochs = set(poly) | set(pr) | set(lim)
    print(f"  windows: {len(all_epochs)}, outcomes: {len(outcomes)}")

    rows = []
    for ep in sorted(all_epochs):
        r = analyze_window(ep, poly.get(ep, []), pr.get(ep, []), lim.get(ep, []), outcomes)
        if r:
            rows.append(r)

    # Bucket by cheap price level
    print(f"\n  buckets by cheap-side price (one row per window):")
    print(f"  {'cheap_p':<10}{'count':<8}{'wins':<8}{'losses':<8}{'rate':<10}{'med_other':<12}{'med_profit_pct':<16}{'>=10pct':<10}")
    for cheap_target in (0.01, 0.02, 0.03, 0.04):
        bucket = [r for r in rows if abs(r["cheap_price"] - cheap_target) < 1e-9]
        if not bucket:
            print(f"  {cheap_target:<10.3f}n=0")
            continue
        resolved = [r for r in bucket if r["predicted_correct"] is not None]
        wins = sum(1 for r in resolved if r["predicted_correct"])
        losses = sum(1 for r in resolved if r["predicted_correct"] is False)
        rate = (wins / len(resolved) * 100) if resolved else 0
        other_prices = sorted([r["other_price"] for r in bucket])
        med_other = other_prices[len(other_prices) // 2] if other_prices else 0
        profits = sorted([r["profit_pct"] for r in bucket])
        med_profit = profits[len(profits) // 2] if profits else 0
        ge_10 = sum(1 for r in bucket if r["profit_pct"] >= 10)
        ge_10_pct = ge_10 / len(bucket) * 100
        print(f"  {cheap_target:<10.3f}{len(bucket):<8}{wins:<8}{losses:<8}{rate:<10.1f}{med_other:<12.3f}{med_profit:<16.1f}{ge_10:<5}({ge_10_pct:.0f}%)")

    # Overall: how many windows had cheap <= 0.04?
    print(f"\n  total windows with cheap <= 4 cents: {len(rows)}")
    resolved_all = [r for r in rows if r["predicted_correct"] is not None]
    wins_all = sum(1 for r in resolved_all if r["predicted_correct"])
    print(f"  resolved: {len(resolved_all)}, predicted correctly: {wins_all} ({wins_all/max(len(resolved_all),1)*100:.1f}%)")

    # Profit margin distribution
    print(f"\n  profit margin distribution if we buy the opposite side:")
    for lo, hi, label2 in [(0, 5, "0-5%"), (5, 10, "5-10%"), (10, 20, "10-20%"),
                           (20, 50, "20-50%"), (50, 100, "50-100%"), (100, 9999, ">100%")]:
        in_bucket = [r for r in rows if lo <= r["profit_pct"] < hi]
        resolved2 = [r for r in in_bucket if r["predicted_correct"] is not None]
        wins2 = sum(1 for r in resolved2 if r["predicted_correct"])
        rate2 = (wins2 / len(resolved2) * 100) if resolved2 else 0
        print(f"    {label2:<10} n={len(in_bucket):>3}  resolved={len(resolved2):>3}  wins={wins2:>3} ({rate2:.1f}%)")


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
