#!/usr/bin/env python3
"""1-hour V8 analysis with time filtering.

Question: if we only buy when agreement (>=2 or >=3) forms AFTER minute 30,
how much profit do we lose vs unrestricted?

Data: poly + predict + lim recorders on 1h windows.
"""

import csv
import re
import sys
from collections import defaultdict


CHEAP_THRESH = 0.10
OPP_MIN = 0.50
OPP_MAX = 0.80
WINDOW_SEC = 3600


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
                yu = fnum(r.get("yes_ask_usd"))
                nu = fnum(r.get("no_ask_usd_buyable"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["pr_yes"] = ya
                s["pr_no"] = na
                s["pr_yes_usd"] = yu
                s["pr_no_usd"] = nu
            except (KeyError, ValueError):
                continue


def scan_lim(path, per_window_per_sec):
    slug_re = re.compile(r"-hourly-(\d{10,13})$")
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
                ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                es = int(r["epoch_sec"])
                ya = fnum(r.get("best_ask"))
                na = fnum(r.get("no_best_ask"))
                yu = fnum(r.get("best_ask_size_usd"))
                nu = fnum(r.get("no_best_ask_size_usd"))
                s = per_window_per_sec[ep].setdefault(es, {})
                s["lim_yes"] = ya
                s["lim_no"] = na
                s["lim_yes_usd"] = yu
                s["lim_no_usd"] = nu
            except (KeyError, ValueError):
                continue


def main():
    per_window = defaultdict(dict)
    scan_poly("/root/data_btc_1h_research/combined_per_second.csv", per_window)
    scan_predict("/root/data_predict_btc_1h/combined_per_second.csv", per_window)
    scan_lim("/root/data_limitless_btc_1h/combined_per_second.csv", per_window)
    outcomes = load_outcomes("/root/data_btc_1h_research/market_outcomes.csv")
    print(f"חלונות: {len(per_window)}, תוצאות ידועות: {len(outcomes)}")
    print()

    yes_keys = ["poly_yes", "pr_yes", "lim_yes"]
    no_keys  = ["poly_no",  "pr_no",  "lim_no"]
    OPP_KEYS = ["poly", "pr", "lim"]  # only these 3, all tradable

    # For each window, find first sec when agreement >=2 and >=3 forms
    signals = []  # (epoch, first_2_sec, first_3_sec, predicted, opp_ask_at_that_sec, winner)
    for ep, snaps in per_window.items():
        yes_seen = set()
        no_seen = set()
        first_2 = None  # (sec, predicted, opp_ask)
        first_3 = None
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
            # Pick best agreement direction
            if yes_seen and no_seen:
                continue  # contradiction
            for level in (2, 3):
                pred = None
                if len(yes_seen) >= level:
                    pred = "DOWN"
                    opp_side = "no"
                elif len(no_seen) >= level:
                    pred = "UP"
                    opp_side = "yes"
                if pred is None:
                    continue
                # Find best opp ask now
                best_opp = None
                for p in OPP_KEYS:
                    a = snap.get(f"{p}_{opp_side}", 0)
                    d = snap.get(f"{p}_{opp_side}_usd", 0)
                    if 0 < a < 1 and OPP_MIN <= a <= OPP_MAX:
                        if best_opp is None or a < best_opp[0]:
                            best_opp = (a, p, d)
                if best_opp is None:
                    continue
                if level == 2 and first_2 is None:
                    first_2 = (sec - ep, pred, best_opp[0], best_opp[2])
                if level == 3 and first_3 is None:
                    first_3 = (sec - ep, pred, best_opp[0], best_opp[2])
        winner = outcomes.get(ep)
        signals.append((ep, first_2, first_3, winner))

    def report(name, signals, level_idx, min_time_sec=0):
        sigs = []
        for ep, f2, f3, winner in signals:
            sig = f2 if level_idx == 2 else f3
            if sig is None:
                continue
            sec, pred, opp_ask, opp_depth = sig
            if sec < min_time_sec:
                continue
            sigs.append((ep, sec, pred, opp_ask, opp_depth, winner))
        resolved = [s for s in sigs if s[5] in ("UP", "DOWN")]
        wins = sum(1 for s in resolved if s[5] == s[2])
        rate = (wins / len(resolved) * 100) if resolved else 0
        total_ret = 0.0
        for s in resolved:
            if s[5] == s[2]:
                total_ret += (1 - s[3]) / s[3]
            else:
                total_ret -= 1.0
        avg_per_dollar = (total_ret / len(resolved) * 100) if resolved else 0
        print(f"  {name}: {len(sigs)} סיגנלים, {wins} ניצחו, {len(resolved)-wins} הפסידו, דיוק {rate:.0f}%, רווח ממוצע {avg_per_dollar:+.1f}% לעסקה, סכום נטו {total_ret:+.2f} דולר לדולר")

    print("חלון שעה, הסכמה של 2 פלטפורמות מתוך 3")
    report("ללא סינון זמן", signals, 2, 0)
    report("רק אחרי דקה 30", signals, 2, 30 * 60)
    report("רק אחרי דקה 45", signals, 2, 45 * 60)
    print()
    print("חלון שעה, הסכמה של 3 פלטפורמות מתוך 3")
    report("ללא סינון זמן", signals, 3, 0)
    report("רק אחרי דקה 30", signals, 3, 30 * 60)
    report("רק אחרי דקה 45", signals, 3, 45 * 60)


if __name__ == "__main__":
    main()
