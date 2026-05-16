#!/usr/bin/env python3
"""Recorder-based phantom-signal analysis. Direction-agnostic.

For each window (15-min or 1-hour) and each platform:
- find the FIRST second where YES or NO side ask dropped to <= PHANTOM_THRESH

Classify per window:
- predicted_winner = "DOWN" if more YES-side phantoms detected,
                     "UP"   if more NO-side phantoms detected,
                     None   if equal/none
- agreement_strength = how many of the 3 platforms agree on the side

Then check:
- accuracy of prediction (predicted_winner vs actual winner)
- by agreement strength bucket
- by time-of-first-phantom bucket

Run for 15m and 1h separately.
"""

import csv
import re
import sys
from collections import defaultdict


PHANTOM_THRESH = 0.05
OUTCOMES_FILE_15M = "/root/data_btc_15m_research/market_outcomes.csv"
OUTCOMES_FILE_1H  = "/root/data_btc_1h_research/market_outcomes.csv"


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


def scan_poly(path, per_window, window_sec, epoch_col="market_epoch", second_col="epoch_sec"):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r[epoch_col])
                es = int(r[second_col])
                up_ask = fnum(r.get("up_ask"))
                down_ask = fnum(r.get("down_ask"))
                w = per_window[epoch]
                rel_sec = es - epoch
                if 0 < up_ask <= PHANTOM_THRESH:
                    if w["poly_yes_first_sec"] is None or rel_sec < w["poly_yes_first_sec"]:
                        w["poly_yes_first_sec"] = rel_sec
                if 0 < down_ask <= PHANTOM_THRESH:
                    if w["poly_no_first_sec"] is None or rel_sec < w["poly_no_first_sec"]:
                        w["poly_no_first_sec"] = rel_sec
            except (KeyError, ValueError):
                continue


def scan_predict(path, per_window, window_sec):
    with open(path) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_open_epoch"])
                es = int(r["epoch_sec"])
                yes_ask = fnum(r.get("yes_ask"))
                no_ask = fnum(r.get("no_ask_implied"))
                w = per_window[epoch]
                rel_sec = es - epoch
                if 0 < yes_ask <= PHANTOM_THRESH:
                    if w["pr_yes_first_sec"] is None or rel_sec < w["pr_yes_first_sec"]:
                        w["pr_yes_first_sec"] = rel_sec
                if 0 < no_ask <= PHANTOM_THRESH:
                    if w["pr_no_first_sec"] is None or rel_sec < w["pr_no_first_sec"]:
                        w["pr_no_first_sec"] = rel_sec
            except (KeyError, ValueError):
                continue


def scan_lim(path, per_window, window_sec, slug_regex):
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
                w = per_window[epoch]
                rel_sec = es - epoch
                if 0 < yes_ask <= PHANTOM_THRESH:
                    if w["lim_yes_first_sec"] is None or rel_sec < w["lim_yes_first_sec"]:
                        w["lim_yes_first_sec"] = rel_sec
                if 0 < no_ask <= PHANTOM_THRESH:
                    if w["lim_no_first_sec"] is None or rel_sec < w["lim_no_first_sec"]:
                        w["lim_no_first_sec"] = rel_sec
            except (KeyError, ValueError):
                continue


def new_window():
    return {
        "poly_yes_first_sec": None, "poly_no_first_sec": None,
        "pr_yes_first_sec": None, "pr_no_first_sec": None,
        "lim_yes_first_sec": None, "lim_no_first_sec": None,
    }


def classify(w):
    yes_count = sum(1 for k in ("poly_yes_first_sec", "pr_yes_first_sec", "lim_yes_first_sec") if w[k] is not None)
    no_count  = sum(1 for k in ("poly_no_first_sec",  "pr_no_first_sec",  "lim_no_first_sec")  if w[k] is not None)
    if yes_count == 0 and no_count == 0:
        return None, 0, None
    if yes_count > 0 and no_count > 0:
        return None, max(yes_count, no_count), "CONTRADICT"
    if yes_count > 0:
        firsts = [w["poly_yes_first_sec"], w["pr_yes_first_sec"], w["lim_yes_first_sec"]]
        earliest = min(s for s in firsts if s is not None)
        return "DOWN", yes_count, earliest
    firsts = [w["poly_no_first_sec"], w["pr_no_first_sec"], w["lim_no_first_sec"]]
    earliest = min(s for s in firsts if s is not None)
    return "UP", no_count, earliest


def run_analysis(label, window_sec, poly_path, predict_path, lim_path, outcomes_path, slug_regex):
    print(f"\n========== {label}: window {window_sec}s ==========")
    outcomes = load_outcomes(outcomes_path)
    per_window = defaultdict(new_window)
    print(f"  loaded {len(outcomes)} outcomes")
    scan_poly(poly_path, per_window, window_sec)
    scan_predict(predict_path, per_window, window_sec)
    scan_lim(lim_path, per_window, window_sec, slug_regex)
    print(f"  windows seen: {len(per_window)}")

    by_strength = defaultdict(lambda: {"n": 0, "correct": 0, "wrong": 0, "no_outcome": 0})
    contradict = {"n": 0, "no_signal": 0}
    by_time_bucket = defaultdict(lambda: {"n": 0, "correct": 0})
    earliest_secs = []

    for epoch, w in per_window.items():
        predicted, strength, extra = classify(w)
        winner = outcomes.get(epoch, "UNKNOWN")
        if predicted is None:
            if extra == "CONTRADICT":
                contradict["n"] += 1
            else:
                contradict["no_signal"] += 1
            continue
        bucket = by_strength[strength]
        bucket["n"] += 1
        if winner == predicted:
            bucket["correct"] += 1
        elif winner in ("UP", "DOWN"):
            bucket["wrong"] += 1
        else:
            bucket["no_outcome"] += 1
        earliest_secs.append(extra)

        if winner in ("UP", "DOWN"):
            tb = (extra // 60) * 60  # 60-sec buckets
            tb_label = f"{tb}-{tb+60}"
            by_time_bucket[tb_label]["n"] += 1
            if winner == predicted:
                by_time_bucket[tb_label]["correct"] += 1

    print(f"\n  by-strength results:")
    for k in sorted(by_strength.keys()):
        b = by_strength[k]
        resolved = b["correct"] + b["wrong"]
        rate = (b["correct"] / resolved * 100) if resolved else 0
        print(f"    {k}-platform agreement   n={b['n']:>3}  correct={b['correct']:>3}  wrong={b['wrong']:>3}  rate={rate:.1f}%  no_outcome={b['no_outcome']}")
    print(f"  contradictions: {contradict['n']}")
    print(f"  no signal:      {contradict['no_signal']}")

    print(f"\n  hit rate by first-phantom appearance time:")
    for tb in sorted(by_time_bucket.keys(), key=lambda s: int(s.split("-")[0])):
        b = by_time_bucket[tb]
        rate = (b["correct"] / b["n"] * 100) if b["n"] else 0
        print(f"    sec {tb:<10}  n={b['n']:>3}  correct={b['correct']:>3}  rate={rate:.1f}%")


if __name__ == "__main__":
    run_analysis(
        "15-min", 900,
        "/root/data_btc_15m_research/combined_per_second.csv",
        "/root/data_predict_btc_15m/combined_per_second.csv",
        "/root/data_limitless_btc_15m/combined_per_second.csv",
        OUTCOMES_FILE_15M,
        re.compile(r"-15-min-(\d{10,13})$"),
    )
    run_analysis(
        "1-hour", 3600,
        "/root/data_btc_1h_research/combined_per_second.csv",
        "/root/data_predict_btc_1h/combined_per_second.csv",
        "/root/data_limitless_btc_1h/combined_per_second.csv",
        OUTCOMES_FILE_1H,
        re.compile(r"-hourly-(\d{10,13})$"),
    )
