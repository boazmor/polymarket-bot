#!/usr/bin/env python3
"""For V8 4-platform analysis: accuracy by TIME of first agreement.
Check hypothesis: signals later in window have higher win rate.

Run on both 15-min and 1-hour data.
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime


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


def scan_lim(path, per_window_per_sec, slug_pattern, window_sec):
    slug_re = re.compile(slug_pattern)
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
                ep = (lim_id // window_sec) * window_sec
                es = int(r["epoch_sec"])
                ya = fnum(r.get("best_ask"))
                na = fnum(r.get("no_best_ask"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["lim_yes"] = ya
                s["lim_no"] = na
            except (KeyError, ValueError):
                continue


def scan_kalshi(path, per_window_per_sec, window_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // window_sec) * window_sec
                es = int(r["epoch_sec"])
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["ks_yes"] = ya
                s["ks_no"] = na
            except (KeyError, ValueError):
                continue


def scan_gemini(path, per_window_per_sec, window_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot:
                    continue
                ep = (iso_to_epoch(ot) // window_sec) * window_sec
                es = int(r["epoch_sec"])
                ya = fnum(r.get("yes_ask"))
                na = fnum(r.get("no_ask_implied"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["ge_yes"] = ya
                s["ge_no"] = na
            except (KeyError, ValueError):
                continue


def analyze(label, window_sec, poly_path, predict_path, lim_path, kalshi_path, gemini_path,
             outcomes_path, slug_pattern, include_lim=True, include_kalshi=True, include_gemini=True):
    print(f"\n========== {label} ==========")
    per_window = defaultdict(dict)
    scan_poly(poly_path, per_window)
    scan_predict(predict_path, per_window)
    if include_lim:
        scan_lim(lim_path, per_window, slug_pattern, window_sec)
    if include_kalshi:
        scan_kalshi(kalshi_path, per_window, window_sec)
    if include_gemini:
        scan_gemini(gemini_path, per_window, window_sec)
    outcomes = load_outcomes(outcomes_path)
    print(f"  windows: {len(per_window)}, outcomes: {len(outcomes)}")

    yes_keys = []
    no_keys = []
    if True:
        yes_keys.append("poly_yes"); no_keys.append("poly_no")
        yes_keys.append("pr_yes"); no_keys.append("pr_no")
    if include_lim:
        yes_keys.append("lim_yes"); no_keys.append("lim_no")
    if include_kalshi:
        yes_keys.append("ks_yes"); no_keys.append("ks_no")
    if include_gemini:
        yes_keys.append("ge_yes"); no_keys.append("ge_no")

    # For each window with agreement >=3, find first second when it formed
    # and check outcome
    minute_bucket_data = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0})

    for ep, snaps in per_window.items():
        yes_seen = set()
        no_seen = set()
        first_3_sec = None
        first_3_predicted = None
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
            # Check agreement of 3+ first time
            if first_3_sec is None:
                if len(yes_seen) >= 3:
                    first_3_sec = sec - ep
                    first_3_predicted = "DOWN"
                elif len(no_seen) >= 3:
                    first_3_sec = sec - ep
                    first_3_predicted = "UP"
        if first_3_sec is None:
            continue
        winner = outcomes.get(ep)
        # Bucket by minute (60s buckets)
        bucket = first_3_sec // 60
        bucket_data = minute_bucket_data[bucket]
        bucket_data["n"] += 1
        if winner == first_3_predicted:
            bucket_data["wins"] += 1
        elif winner in ("UP", "DOWN"):
            bucket_data["losses"] += 1

    print()
    print(f"  הסכמה של 3 פלטפורמות, דיוק לפי דקה של הופעה ראשונה")
    total_minutes = window_sec // 60
    for bucket in range(total_minutes):
        d = minute_bucket_data.get(bucket, {"n": 0, "wins": 0, "losses": 0})
        if d["n"] == 0:
            continue
        resolved = d["wins"] + d["losses"]
        rate = (d["wins"] / resolved * 100) if resolved else 0
        print(f"    דקה {bucket}-{bucket+1}: {d['n']} חלונות, {d['wins']} ניצחו, {d['losses']} הפסידו, דיוק {rate:.0f}%")


if __name__ == "__main__":
    analyze(
        "15 דקות, 4 פלטפורמות", 900,
        "/root/data_btc_15m_research/combined_per_second.csv",
        "/root/data_predict_btc_15m/combined_per_second.csv",
        "/root/data_limitless_btc_15m/combined_per_second.csv",
        "/root/data_kalshi_btc_15m/combined_per_second.csv",
        "/root/data_gemini_btc_15m/combined_per_second.csv",
        "/root/data_btc_15m_research/market_outcomes.csv",
        r"-15-min-(\d{10,13})$",
        include_lim=False,  # exclude Lim from V8 since it's broken
    )
    analyze(
        "שעה, 3 פלטפורמות", 3600,
        "/root/data_btc_1h_research/combined_per_second.csv",
        "/root/data_predict_btc_1h/combined_per_second.csv",
        "/root/data_limitless_btc_1h/combined_per_second.csv",
        "/root/data_kalshi_btc_15m/combined_per_second.csv",  # only 15m exists
        "/root/data_gemini_btc_15m/combined_per_second.csv",
        "/root/data_btc_1h_research/market_outcomes.csv",
        r"-hourly-(\d{10,13})$",
        include_kalshi=False, include_gemini=False, include_lim=True,
    )
