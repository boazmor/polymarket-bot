#!/usr/bin/env python3
"""Combined strike+agreement analysis for 15-min.

Hypothesis: agreement between platforms with SIMILAR strikes is stronger
than agreement that includes a platform with an outlier strike.

For each window:
  1. Collect strikes from poly + predict + kalshi + gemini
  2. Compute median strike
  3. For each platform, mark as 'CLOSE' (within $X of median) or 'OUTLIER'
  4. Count agreement among CLOSE platforms only
  5. Cross-reference with outcome
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime


WINDOW_SEC = 900
CHEAP_THRESH = 0.10


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


def collect_data():
    """Returns per_window dict: ep -> {strikes: {plat: strike},
    asks_history: {plat: list of (sec, yes_ask, no_ask)}}"""
    data = defaultdict(lambda: {"strikes": {}, "asks": defaultdict(list)})

    # Poly
    with open("/root/data_btc_15m_research/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_epoch"])
                es = int(r["epoch_sec"])
                sec = es - ep
                strike = fnum(r.get("target_chainlink_at_open"))
                if strike > 0:
                    data[ep]["strikes"]["poly"] = strike
                ya = fnum(r.get("up_ask"))
                na = fnum(r.get("down_ask"))
                if ya > 0 or na > 0:
                    data[ep]["asks"]["poly"].append((sec, ya, na))
            except (KeyError, ValueError):
                continue

    # Predict
    with open("/root/data_predict_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep_raw = r.get("market_open_epoch")
                es_raw = r.get("epoch_sec")
                if not ep_raw or not es_raw:
                    continue
                ep = int(ep_raw)
                es = int(es_raw)
                sec = es - ep
                strike = fnum(r.get("strike"))
                if strike > 0:
                    data[ep]["strikes"]["predict"] = strike
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask_implied"))
                if ya > 0 or na > 0:
                    data[ep]["asks"]["predict"].append((sec, ya, na))
            except (KeyError, ValueError):
                continue

    # Kalshi
    with open("/root/data_kalshi_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                sec = es - ep
                strike = fnum(r.get("floor_strike"))
                if strike > 0:
                    data[ep]["strikes"]["kalshi"] = strike
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask"))
                if ya > 0 or na > 0:
                    data[ep]["asks"]["kalshi"].append((sec, ya, na))
            except (KeyError, ValueError):
                continue

    # Gemini
    with open("/root/data_gemini_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                sec = es - ep
                strike = fnum(r.get("strike"))
                if strike > 0:
                    data[ep]["strikes"]["gemini"] = strike
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask_implied"))
                if ya > 0 or na > 0:
                    data[ep]["asks"]["gemini"].append((sec, ya, na))
            except (KeyError, ValueError):
                continue

    return data


def main():
    print("loading data...", file=sys.stderr)
    data = collect_data()
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(data)}", file=sys.stderr)

    # For each window, identify CLOSE platforms (within X of median strike)
    # Then count agreement among CLOSE platforms only

    def analyze(close_threshold_usd, min_agree, label):
        n_signals = 0
        n_resolved = 0
        n_correct = 0
        for ep, w in data.items():
            strikes = w["strikes"]
            if len(strikes) < 3:
                continue
            vals = sorted(strikes.values())
            median = vals[len(vals)//2]
            close_plats = [p for p, s in strikes.items() if abs(s - median) <= close_threshold_usd]
            if len(close_plats) < min_agree:
                continue
            # Check agreement on YES or NO side among close_plats during window
            yes_seen = set()
            no_seen = set()
            for plat in close_plats:
                for sec, ya, na in w["asks"][plat]:
                    if 0 < ya <= CHEAP_THRESH:
                        yes_seen.add(plat)
                    if 0 < na <= CHEAP_THRESH:
                        no_seen.add(plat)
            if yes_seen and no_seen:
                continue
            if len(yes_seen) >= min_agree:
                predicted = "DOWN"
            elif len(no_seen) >= min_agree:
                predicted = "UP"
            else:
                continue
            n_signals += 1
            winner = outcomes.get(ep)
            if winner in ("UP", "DOWN"):
                n_resolved += 1
                if winner == predicted:
                    n_correct += 1
        rate = (n_correct / n_resolved * 100) if n_resolved else 0
        print(f"  {label}: {n_signals} סיגנלים, {n_correct}/{n_resolved} נכונים, {rate:.0f}%")

    print()
    print("=== הסכמה בין פלטפורמות עם טרגט קרוב ===")
    print()
    for close_thresh in (10, 30, 50, 100):
        print(f"-- פלטפורמות עם טרגט פחות מ-{close_thresh} דולר מהחציון --")
        for ma in (2, 3, 4):
            analyze(close_thresh, ma, f"הסכמה של {ma}")
        print()


if __name__ == "__main__":
    main()
