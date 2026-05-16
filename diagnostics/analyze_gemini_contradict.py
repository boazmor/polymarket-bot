#!/usr/bin/env python3
"""When Gemini contradicts the other 4 platforms, who wins?

For each window:
  - Track which platforms saw YES phantom and which saw NO phantom during the window
  - Identify windows where Gemini's signal contradicts the majority of others
  - Check actual outcome

Hypothesis: Gemini is slower (Kaiko oracle). When it disagrees with majority,
the majority is right.
"""

import csv
import sys
import re
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


def collect():
    """Returns dict: ep -> {yes_seen: set of plats, no_seen: set of plats}"""
    out = defaultdict(lambda: {"yes_seen": set(), "no_seen": set()})

    def scan(path, plat, ya_col, na_col, ep_col=None, ot_col=None):
        with open(path) as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    if ep_col:
                        ep_raw = r.get(ep_col)
                        if not ep_raw: continue
                        ep = int(ep_raw)
                    elif ot_col:
                        ot = r.get(ot_col, "")
                        if not ot: continue
                        ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                    else:
                        continue
                    ya = fnum(r.get(ya_col))
                    na = fnum(r.get(na_col))
                    if 0 < ya <= CHEAP_THRESH:
                        out[ep]["yes_seen"].add(plat)
                    if 0 < na <= CHEAP_THRESH:
                        out[ep]["no_seen"].add(plat)
                except (KeyError, ValueError):
                    continue

    scan("/root/data_btc_15m_research/combined_per_second.csv", "poly", "up_ask", "down_ask", ep_col="market_epoch")
    scan("/root/data_predict_btc_15m/combined_per_second.csv", "predict", "yes_ask", "no_ask_implied", ep_col="market_open_epoch")

    slug_re = re.compile(r"-15-min-(\d{10,13})$")
    with open("/root/data_limitless_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                slug = r.get("slug","")
                m = slug_re.search(slug)
                if not m: continue
                lim_id = int(m.group(1))
                if lim_id > 10**12: lim_id = lim_id // 1000
                ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                ya = fnum(r.get("best_ask"))
                na = fnum(r.get("no_best_ask"))
                if 0 < ya <= CHEAP_THRESH:
                    out[ep]["yes_seen"].add("lim")
                if 0 < na <= CHEAP_THRESH:
                    out[ep]["no_seen"].add("lim")
            except (KeyError, ValueError):
                continue

    scan("/root/data_kalshi_btc_15m/combined_per_second.csv", "kalshi", "yes_ask", "no_ask", ot_col="open_time")
    scan("/root/data_gemini_btc_15m/combined_per_second.csv", "gemini", "yes_ask", "no_ask_implied", ot_col="open_time")
    return out


def main():
    print("loading...", file=sys.stderr)
    data = collect()
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(data)}, outcomes: {len(outcomes)}", file=sys.stderr)

    OTHERS = ["poly", "predict", "lim", "kalshi"]
    print()
    print("=== מקרים שגמיני סותר את הרוב של 4 האחרות ===")
    print()

    gemini_says_down_others_say_up = []  # gemini yes phantom, others no phantom
    gemini_says_up_others_say_down = []  # gemini no phantom, others yes phantom

    for ep, w in data.items():
        others_yes = w["yes_seen"] & set(OTHERS)
        others_no = w["no_seen"] & set(OTHERS)
        gemini_yes = "gemini" in w["yes_seen"]
        gemini_no = "gemini" in w["no_seen"]

        # case 1: gemini says yes (predicts DOWN), others (3+) say no (predict UP)
        if gemini_yes and len(others_no) >= 2 and not gemini_no:
            gemini_says_down_others_say_up.append((ep, len(others_no)))
        # case 2: gemini says no (predicts UP), others (3+) say yes (predict DOWN)
        if gemini_no and len(others_yes) >= 2 and not gemini_yes:
            gemini_says_up_others_say_down.append((ep, len(others_yes)))

    def report(name, cases, others_predict):
        if not cases:
            print(f"  {name}: 0 חלונות")
            return
        resolved = [(ep, n) for ep, n in cases if outcomes.get(ep) in ("UP", "DOWN")]
        majority_right = sum(1 for ep, _ in resolved if outcomes[ep] == others_predict)
        gemini_right = sum(1 for ep, _ in resolved if outcomes[ep] != others_predict)
        print(f"  {name}: {len(cases)} חלונות, {len(resolved)} עם תוצאה")
        print(f"    הרוב צדק ({others_predict}): {majority_right}")
        print(f"    גמיני צדק (ההפך): {gemini_right}")
        if resolved:
            pct = majority_right / len(resolved) * 100
            print(f"    דיוק הרוב: {pct:.0f}%")

    report("גמיני YES_פנטום נגד 2+ אחרים עם NO_פנטום", gemini_says_down_others_say_up, "UP")
    print()
    report("גמיני NO_פנטום נגד 2+ אחרים עם YES_פנטום", gemini_says_up_others_say_down, "DOWN")


if __name__ == "__main__":
    main()
