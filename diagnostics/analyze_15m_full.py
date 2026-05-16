#!/usr/bin/env python3
"""Comprehensive 15-min agreement statistics for V8 review.

Reports for each combination:
  - agreement count (2, 3, 4, 5)
  - max strike spread among AGREEING platforms (no platform filter)
  - profit margin bucket
  - timing bucket within window

Goal: give the user the full picture to choose threshold values.
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime
import re


WINDOW_SEC = 900
CHEAP_THRESH = 0.10
OPP_MIN = 0.50
OPP_MAX = 0.80


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
    data = defaultdict(lambda: {"strikes": {}, "snaps": {}})

    def update(ep, sec, plat, ya, na, strike=None):
        if strike is not None and strike > 0:
            data[ep]["strikes"][plat] = strike
        s = data[ep]["snaps"].setdefault(sec, {})
        s[f"{plat}_yes"] = ya
        s[f"{plat}_no"] = na

    with open("/root/data_btc_15m_research/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep = int(r["market_epoch"])
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "poly", fnum(r.get("up_ask")), fnum(r.get("down_ask")),
                       fnum(r.get("target_chainlink_at_open")))
            except (KeyError, ValueError):
                continue

    with open("/root/data_predict_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ep_raw = r.get("market_open_epoch"); es_raw = r.get("epoch_sec")
                if not ep_raw or not es_raw: continue
                ep = int(ep_raw); sec = int(es_raw) - ep
                update(ep, sec, "predict", fnum(r.get("yes_ask")), fnum(r.get("no_ask_implied")),
                       fnum(r.get("strike")))
            except (KeyError, ValueError):
                continue

    slug_re = re.compile(r"-15-min-(\d{10,13})$")
    with open("/root/data_limitless_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                slug = r.get("slug", "")
                m = slug_re.search(slug)
                if not m: continue
                lim_id = int(m.group(1))
                if lim_id > 10**12: lim_id = lim_id // 1000
                ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "lim", fnum(r.get("best_ask")), fnum(r.get("no_best_ask")))
            except (KeyError, ValueError):
                continue

    with open("/root/data_kalshi_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot: continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "kalshi", fnum(r.get("yes_ask")), fnum(r.get("no_ask")),
                       fnum(r.get("floor_strike")))
            except (KeyError, ValueError):
                continue

    with open("/root/data_gemini_btc_15m/combined_per_second.csv") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                ot = r.get("open_time", "")
                if not ot: continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "gemini", fnum(r.get("yes_ask")), fnum(r.get("no_ask_implied")),
                       fnum(r.get("strike")))
            except (KeyError, ValueError):
                continue
    return data


def main():
    print("loading...", file=sys.stderr)
    data = collect()
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(data)}, outcomes: {len(outcomes)}", file=sys.stderr)
    all_plats = ["poly", "predict", "lim", "kalshi", "gemini"]

    # For each window, find FIRST sec when agreement of N forms, plus the
    # max strike spread among AGREEING platforms.
    summary = []  # (ep, sec, agreement, predicted, agreeing_plats, strike_spread, opp_best, winner)
    for ep, w in data.items():
        snaps = w["snaps"]
        strikes = w["strikes"]
        yes_seen = set()
        no_seen = set()
        first = None
        for sec in sorted(snaps.keys()):
            snap = snaps[sec]
            for p in all_plats:
                if snap.get(f"{p}_yes", 0) and 0 < snap[f"{p}_yes"] <= CHEAP_THRESH:
                    yes_seen.add(p)
                if snap.get(f"{p}_no", 0) and 0 < snap[f"{p}_no"] <= CHEAP_THRESH:
                    no_seen.add(p)
            if yes_seen and no_seen:
                continue
            if len(yes_seen) >= 2 or len(no_seen) >= 2:
                if yes_seen and len(yes_seen) >= len(no_seen):
                    pred = "DOWN"; agreeing = yes_seen
                else:
                    pred = "UP"; agreeing = no_seen
                # find best opp ask in tradable plats now
                opp_side = "no" if pred == "DOWN" else "yes"
                best_opp = None
                for p in ("poly", "predict", "lim"):
                    a = snap.get(f"{p}_{opp_side}", 0)
                    if 0 < a < 1 and OPP_MIN <= a <= OPP_MAX:
                        if best_opp is None or a < best_opp:
                            best_opp = a
                if best_opp is None:
                    continue
                # spread among agreeing
                ag_strikes = [strikes[p] for p in agreeing if p in strikes]
                spread = (max(ag_strikes) - min(ag_strikes)) if len(ag_strikes) >= 2 else 0
                first = (sec, pred, agreeing, spread, best_opp)
                break
        if first:
            summary.append((ep, *first))

    print(f"signals: {len(summary)}")
    print()

    def rep(label, sigs):
        resolved = [s for s in sigs if outcomes.get(s[0]) in ("UP", "DOWN")]
        wins = sum(1 for s in resolved if outcomes[s[0]] == s[2])
        rate = (wins/len(resolved)*100) if resolved else 0
        print(f"    {label}: {len(sigs)} סיגנלים, {wins}/{len(resolved)} = {rate:.0f}% דיוק")

    # By agreement count
    print("=== חתך לפי כמות פלטפורמות מסכימות ===")
    for n in (2, 3, 4, 5):
        bucket = [s for s in summary if len(s[3]) == n]
        rep(f"בדיוק {n} פלטפורמות", bucket)
    print()

    # By strike spread among agreeing platforms
    print("=== חתך לפי פער טרגט בין הפלטפורמות המסכימות ===")
    for lo, hi, name in [(0, 30, "0-30 דולר, קרובים"),
                          (30, 100, "30-100 דולר"),
                          (100, 300, "100-300 דולר"),
                          (300, 99999, "מעל 300 דולר, רחב")]:
        bucket = [s for s in summary if lo <= s[4] < hi]
        rep(name, bucket)
    print()

    # By agreement count AND tight strike (≤30)
    print("=== חתך משולב: כמות הסכמה ופער טרגט עד 30 דולר ===")
    for n in (2, 3, 4, 5):
        bucket = [s for s in summary if len(s[3]) == n and s[4] <= 30]
        rep(f"{n} פלטפורמות עם טרגט פחות מ-30", bucket)
    print()

    # By profit margin
    print("=== חתך לפי אחוז רווח אפשרי ===")
    for lo, hi, name in [(10, 25, "10-25 אחוז"),
                          (25, 50, "25-50 אחוז"),
                          (50, 100, "50-100 אחוז"),
                          (100, 200, "100-200 אחוז")]:
        bucket = []
        for s in summary:
            opp = s[5]
            if opp > 0:
                profit = (1 - opp) / opp * 100
                if lo <= profit < hi:
                    bucket.append(s)
        rep(name, bucket)
    print()

    # By minute of first signal
    print("=== חתך לפי דקה של הופעת הסיגנל ===")
    for m_lo, m_hi in [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15)]:
        bucket = [s for s in summary if m_lo*60 <= s[1] < m_hi*60]
        rep(f"דקה {m_lo}-{m_hi}", bucket)


if __name__ == "__main__":
    main()
