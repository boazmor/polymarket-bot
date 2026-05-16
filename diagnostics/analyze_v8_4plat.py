#!/usr/bin/env python3
"""V8 analysis assuming Limitless is missing. Use 4 platforms:
poly + predict + kalshi + gemini. Compute 2/3/4 platform agreement.
Show timing distribution within window.
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


def scan_poly(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_epoch"])
                es = int(r["epoch_sec"])
                ya = fnum(r.get("up_ask"))
                na = fnum(r.get("down_ask"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["poly_yes"] = ya
                s["poly_no"] = na
            except (KeyError, ValueError):
                continue


def scan_predict(path, per_window_per_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep_raw = r.get("market_open_epoch")
                es_raw = r.get("epoch_sec")
                if not ep_raw or not es_raw:
                    continue
                ep = int(ep_raw)
                es = int(es_raw)
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask_implied"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["pr_yes"] = ya
                s["pr_no"] = na
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
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["ks_yes"] = ya
                s["ks_no"] = na
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
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask_implied"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["ge_yes"] = ya
                s["ge_no"] = na
            except (KeyError, ValueError):
                continue


def main():
    print("loading 4 recorders...", file=sys.stderr)
    per_window = defaultdict(dict)
    scan_poly("/root/data_btc_15m_research/combined_per_second.csv", per_window)
    scan_predict("/root/data_predict_btc_15m/combined_per_second.csv", per_window)
    scan_kalshi("/root/data_kalshi_btc_15m/combined_per_second.csv", per_window)
    scan_gemini("/root/data_gemini_btc_15m/combined_per_second.csv", per_window)
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(per_window)}, outcomes: {len(outcomes)}", file=sys.stderr)

    yes_keys = ["poly_yes", "pr_yes", "ks_yes", "ge_yes"]
    no_keys  = ["poly_no",  "pr_no",  "ks_no",  "ge_no"]

    # For each window, find FIRST second when agreement of N forms
    # Track per agreement level (2, 3, 4)
    agreement_first_seen = defaultdict(dict)  # agreement_level -> ep -> first_sec_relative

    for ep, snaps in per_window.items():
        yes_seen = set()
        no_seen = set()
        first_at_level = {}
        for sec in sorted(snaps.keys()):
            snap = snaps[sec]
            for k in yes_keys:
                v = snap.get(k, 0)
                if 0 < v <= CHEAP_THRESH:
                    yes_seen.add(k)
            for k in no_keys:
                v = snap.get(k, 0)
                if 0 < v <= CHEAP_THRESH:
                    no_seen.add(k)
            n_yes = len(yes_seen)
            n_no = len(no_seen)
            n = max(n_yes, n_no)
            for level in (2, 3, 4):
                if n >= level and ep not in agreement_first_seen[level]:
                    rel = sec - ep
                    agreement_first_seen[level][ep] = (rel, "DOWN" if n_yes >= level else "UP")

    print()
    print("=== חלונות שהגיעו להסכמה ולמתי ===")
    print(f"  סך כל החלונות עם נתונים: {len(per_window)}")
    print()
    for level in (2, 3, 4):
        d = agreement_first_seen[level]
        if not d:
            print(f"  הסכמה {level}: 0 חלונות")
            continue
        # Distribution of first-seen times in 60-sec buckets
        buckets = defaultdict(int)
        outcomes_by_pred = {"UP": [0, 0, 0], "DOWN": [0, 0, 0]}  # [count, wins, losses]
        for ep, (rel_sec, predicted) in d.items():
            bucket = (rel_sec // 60) * 60
            buckets[bucket] += 1
            winner = outcomes.get(ep)
            if winner in ("UP", "DOWN"):
                outcomes_by_pred[predicted][0] += 1
                if winner == predicted:
                    outcomes_by_pred[predicted][1] += 1
                else:
                    outcomes_by_pred[predicted][2] += 1
        total_count = sum(c for c, _, _ in outcomes_by_pred.values())
        total_wins = sum(w for _, w, _ in outcomes_by_pred.values())
        rate = (total_wins / total_count * 100) if total_count else 0
        print(f"  הסכמה {level} פלטפורמות: {len(d)} חלונות, דיוק {rate:.0f}% על {total_count} עם תוצאה")
        for bucket in sorted(buckets.keys())[:12]:
            n = buckets[bucket]
            print(f"    הופעה ראשונה בדקה {bucket//60}-{bucket//60+1}: {n} חלונות")
        print()


if __name__ == "__main__":
    main()
