#!/usr/bin/env python3
"""5-platform liquidity-aware opportunity analysis.

For each 15-min window where all 5 platforms have data:
  - Identify per-second phantom signals (ask <= 0.05 on YES or NO)
  - Find the moment when 2+ platforms agree on one side as losing
  - At that moment, find the lowest opposite-side ask AND its depth
  - Require: profit >= 10%, depth >= $1 (real, not noise)
  - Bucket by depth >= $2, $10, $50, $100

Output describes:
  - Recording period start/end and total hours
  - Number of opportunities at various depth thresholds
  - Estimated profit potential
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime


CHEAP_THRESH = 0.05  # tight to filter noise
WINDOW_SEC = 900
MIN_AGREEMENT = 2


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


def scan_poly(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                es = int(r["epoch_sec"])
                up_ask = fnum(r.get("up_ask"))
                down_ask = fnum(r.get("down_ask"))
                up_usd = fnum(r.get("up_usd_best"))
                down_usd = fnum(r.get("down_usd_best"))
                d = per_window_per_sec[epoch].setdefault(es, {})
                d["poly_yes_ask"] = up_ask
                d["poly_no_ask"] = down_ask
                d["poly_yes_usd"] = up_usd
                d["poly_no_usd"] = down_usd
            except (KeyError, ValueError):
                continue


def scan_predict(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask_implied"))
                yes_size = fnum(r.get("yes_ask_size"))
                yes_usd = yes_ask * yes_size if yes_ask > 0 else 0
                no_usd = fnum(r.get("no_ask_usd"))
                d = per_window_per_sec[epoch].setdefault(es, {})
                d["predict_yes_ask"] = yes_ask
                d["predict_no_ask"] = no_ask
                d["predict_yes_usd"] = yes_usd
                d["predict_no_usd"] = no_usd
            except (KeyError, ValueError):
                continue


def scan_lim(path, per_window_per_sec):
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
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("best_ask"))
                no_ask = fnum(r.get("no_best_ask"))
                yes_usd = fnum(r.get("best_ask_size_usd"))
                no_usd = fnum(r.get("no_best_ask_size_usd"))
                d = per_window_per_sec[epoch].setdefault(es, {})
                d["lim_yes_ask"] = yes_ask
                d["lim_no_ask"] = no_ask
                d["lim_yes_usd"] = yes_usd
                d["lim_no_usd"] = no_usd
            except (KeyError, ValueError):
                continue


def scan_kalshi(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                epoch = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask"))
                yes_size = fnum(r.get("yes_ask_size"))
                no_size = fnum(r.get("no_ask_size"))
                yes_usd = yes_ask * yes_size
                no_usd = no_ask * no_size
                d = per_window_per_sec[epoch].setdefault(es, {})
                d["kalshi_yes_ask"] = yes_ask
                d["kalshi_no_ask"] = no_ask
                d["kalshi_yes_usd"] = yes_usd
                d["kalshi_no_usd"] = no_usd
            except (KeyError, ValueError):
                continue


def scan_gemini(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                epoch = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask_implied"))
                yes_usd = fnum(r.get("yes_ask_usd"))
                no_usd_buyable = fnum(r.get("no_ask_usd_buyable"))
                d = per_window_per_sec[epoch].setdefault(es, {})
                d["gemini_yes_ask"] = yes_ask
                d["gemini_no_ask"] = no_ask
                d["gemini_yes_usd"] = yes_usd
                d["gemini_no_usd"] = no_usd_buyable
            except (KeyError, ValueError):
                continue


def analyze_window(epoch, snaps_per_sec, outcomes):
    """For each second in the window, evaluate if there's a valid signal.
    Returns the BEST signal-moment found (highest depth where profit >= 10%)."""
    plats = ["poly", "predict", "lim", "kalshi", "gemini"]
    best_signal = None
    for sec in sorted(snaps_per_sec.keys()):
        snap = snaps_per_sec[sec]
        yes_phantoms = [p for p in plats if 0 < snap.get(f"{p}_yes_ask", 0) <= CHEAP_THRESH]
        no_phantoms  = [p for p in plats if 0 < snap.get(f"{p}_no_ask", 0) <= CHEAP_THRESH]
        if yes_phantoms and no_phantoms:
            continue  # contradiction
        if len(yes_phantoms) >= MIN_AGREEMENT:
            opp_side = "no"
            predicted = "DOWN"
            agreement = len(yes_phantoms)
        elif len(no_phantoms) >= MIN_AGREEMENT:
            opp_side = "yes"
            predicted = "UP"
            agreement = len(no_phantoms)
        else:
            continue

        # find best opp ask + depth with profit >= 10%
        for p in plats:
            ask = snap.get(f"{p}_{opp_side}_ask", 0)
            depth = snap.get(f"{p}_{opp_side}_usd", 0)
            if ask <= 0 or ask >= 1:
                continue
            profit_pct = (1 - ask) / ask * 100
            if profit_pct < 10:
                continue
            if depth < 1:
                continue  # filter noise: must have at least $1 of real depth
            score = depth  # rank by depth available
            if best_signal is None or score > best_signal["depth"]:
                best_signal = {
                    "epoch": epoch, "sec_in_window": sec - epoch,
                    "agreement": agreement, "predicted": predicted,
                    "opp_plat": p, "opp_side": opp_side,
                    "ask": ask, "depth": depth,
                    "profit_pct": profit_pct,
                    "winner": outcomes.get(epoch, "UNKNOWN"),
                }
    return best_signal


def main():
    print("loading 5 recorders...", file=sys.stderr)
    per_window = defaultdict(dict)
    scan_poly("/root/data_btc_15m_research/combined_per_second.csv", per_window)
    scan_predict("/root/data_predict_btc_15m/combined_per_second.csv", per_window)
    scan_lim("/root/data_limitless_btc_15m/combined_per_second.csv", per_window)
    scan_kalshi("/root/data_kalshi_btc_15m/combined_per_second.csv", per_window)
    scan_gemini("/root/data_gemini_btc_15m/combined_per_second.csv", per_window)
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"  total windows seen: {len(per_window)}", file=sys.stderr)
    print(f"  outcomes: {len(outcomes)}", file=sys.stderr)

    # Recording period
    all_epochs = sorted(per_window.keys())
    if all_epochs:
        start_iso = datetime.utcfromtimestamp(all_epochs[0]).isoformat()
        end_iso = datetime.utcfromtimestamp(all_epochs[-1] + WINDOW_SEC).isoformat()
        total_hours = (all_epochs[-1] + WINDOW_SEC - all_epochs[0]) / 3600
    else:
        start_iso = "?"
        end_iso = "?"
        total_hours = 0

    # Analyze each window
    signals = []
    for ep, snaps in per_window.items():
        sig = analyze_window(ep, snaps, outcomes)
        if sig:
            signals.append(sig)

    print()
    print(f"=== Recording period ===")
    print(f"  start (UTC): {start_iso}")
    print(f"  end   (UTC): {end_iso}")
    print(f"  total hours: {total_hours:.1f}h  ({total_hours/24:.2f} days)")
    print(f"  total windows scanned: {len(per_window)}")

    print(f"\n=== Signal counts (agreement>=2, profit>=10%, depth>=$1) ===")
    print(f"  total signals: {len(signals)}")
    if not signals:
        return

    resolved = [s for s in signals if s["winner"] in ("UP", "DOWN")]
    wins = sum(1 for s in resolved if s["winner"] == s["predicted"])
    print(f"  resolved: {len(resolved)}, correct: {wins} ({wins/max(len(resolved),1)*100:.1f}%)")

    print(f"\n=== Depth distribution ===")
    for thresh in (1, 2, 5, 10, 25, 50, 100, 250, 500):
        n = sum(1 for s in signals if s["depth"] >= thresh)
        nres = [s for s in signals if s["depth"] >= thresh and s["winner"] in ("UP", "DOWN")]
        nwins = sum(1 for s in nres if s["winner"] == s["predicted"])
        rate = (nwins / len(nres) * 100) if nres else 0
        print(f"  depth >= ${thresh:>3}:  {n:>4} signals  resolved={len(nres):>4}  wins={nwins:>4} ({rate:.1f}%)")

    print(f"\n=== Profit margin distribution ===")
    for lo, hi, name in [(10,25,"10-25%"), (25,50,"25-50%"), (50,100,"50-100%"),
                         (100,200,"100-200%"), (200,500,"200-500%"), (500,99999,">500%")]:
        bucket = [s for s in signals if lo <= s["profit_pct"] < hi]
        res = [s for s in bucket if s["winner"] in ("UP", "DOWN")]
        wins = sum(1 for s in res if s["winner"] == s["predicted"])
        rate = (wins / len(res) * 100) if res else 0
        avg_depth = sum(s["depth"] for s in bucket) / len(bucket) if bucket else 0
        print(f"  {name:<10} n={len(bucket):>4}  resolved={len(res):>4}  wins={wins:>4} ({rate:.1f}%)  avg_depth=${avg_depth:.1f}")

    # Theoretical profit if we'd actually traded at depth >= $X
    print(f"\n=== Profit potential at different trade sizes ===")
    for size in (2, 10, 50, 100):
        # eligible: depth >= size, profit_pct between [10, 200] (avoid noise above 200%)
        elig = [s for s in signals if s["depth"] >= size and 10 <= s["profit_pct"] < 200
                and s["winner"] in ("UP", "DOWN")]
        if not elig:
            print(f"  size ${size}: 0 trades possible")
            continue
        total_profit = 0
        for s in elig:
            invested = size  # spend $size at price s["ask"]
            shares = invested / s["ask"]
            if s["winner"] == s["predicted"]:
                profit = shares * 1.0 - invested  # win pays $1 per share
            else:
                profit = -invested  # lose all
            total_profit += profit
        win_count = sum(1 for s in elig if s["winner"] == s["predicted"])
        days = total_hours / 24 if total_hours > 0 else 1
        per_day = total_profit / days if days > 0 else 0
        print(f"  ${size:>3}/trade: {len(elig):>4} eligible  wins={win_count:>4}  "
              f"total profit ${total_profit:>+8.2f}  per_day ${per_day:>+8.2f}")


if __name__ == "__main__":
    main()
