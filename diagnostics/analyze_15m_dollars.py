#!/usr/bin/env python3
"""15-min dollar comparison: does strike filter actually improve dollars?

For each strategy variant, compute total profit assuming $2/trade,
hold-to-expiry, win pays $1 per share.
"""

import csv
import sys
import re
from collections import defaultdict
from datetime import datetime


WINDOW_SEC = 900
CHEAP_THRESH = 0.10
OPP_MIN = 0.50
OPP_MAX = 0.80
INVEST = 2.0


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
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"]); sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "poly", fnum(r.get("up_ask")), fnum(r.get("down_ask")),
                       fnum(r.get("target_chainlink_at_open")))
            except (KeyError, ValueError): continue

    with open("/root/data_predict_btc_15m/combined_per_second.csv") as f:
        for r in csv.DictReader(f):
            try:
                ep_raw = r.get("market_open_epoch"); es_raw = r.get("epoch_sec")
                if not ep_raw or not es_raw: continue
                ep = int(ep_raw); sec = int(es_raw) - ep
                update(ep, sec, "predict", fnum(r.get("yes_ask")), fnum(r.get("no_ask_implied")),
                       fnum(r.get("strike")))
            except (KeyError, ValueError): continue

    slug_re = re.compile(r"-15-min-(\d{10,13})$")
    with open("/root/data_limitless_btc_15m/combined_per_second.csv") as f:
        for r in csv.DictReader(f):
            try:
                m = slug_re.search(r.get("slug",""))
                if not m: continue
                lim_id = int(m.group(1))
                if lim_id > 10**12: lim_id = lim_id // 1000
                ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "lim", fnum(r.get("best_ask")), fnum(r.get("no_best_ask")))
            except (KeyError, ValueError): continue

    with open("/root/data_kalshi_btc_15m/combined_per_second.csv") as f:
        for r in csv.DictReader(f):
            try:
                ot = r.get("open_time", "")
                if not ot: continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "kalshi", fnum(r.get("yes_ask")), fnum(r.get("no_ask")),
                       fnum(r.get("floor_strike")))
            except (KeyError, ValueError): continue

    with open("/root/data_gemini_btc_15m/combined_per_second.csv") as f:
        for r in csv.DictReader(f):
            try:
                ot = r.get("open_time", "")
                if not ot: continue
                ep = (iso_to_epoch(ot) // WINDOW_SEC) * WINDOW_SEC
                sec = int(r["epoch_sec"]) - ep
                update(ep, sec, "gemini", fnum(r.get("yes_ask")), fnum(r.get("no_ask_implied")),
                       fnum(r.get("strike")))
            except (KeyError, ValueError): continue
    return data


def simulate(data, outcomes, min_agree, strike_threshold_usd=None):
    """For each window, find first signal. Buy $INVEST at opp price.
    Returns (n_signals, n_wins, n_losses, total_profit)."""
    all_plats = ["poly", "predict", "lim", "kalshi", "gemini"]
    n_signals = 0
    wins = 0
    losses = 0
    total_profit = 0.0
    for ep, w in data.items():
        snaps = w["snaps"]
        strikes = w["strikes"]
        median_strike = None
        if strike_threshold_usd is not None and len(strikes) >= 2:
            vals = sorted(strikes.values())
            median_strike = vals[len(vals)//2]
        yes_seen = set(); no_seen = set()
        first = None
        for sec in sorted(snaps.keys()):
            snap = snaps[sec]
            for p in all_plats:
                if 0 < snap.get(f"{p}_yes", 0) <= CHEAP_THRESH:
                    if strike_threshold_usd is None or p not in strikes or abs(strikes[p] - median_strike) <= strike_threshold_usd:
                        yes_seen.add(p)
                if 0 < snap.get(f"{p}_no", 0) <= CHEAP_THRESH:
                    if strike_threshold_usd is None or p not in strikes or abs(strikes[p] - median_strike) <= strike_threshold_usd:
                        no_seen.add(p)
            if yes_seen and no_seen:
                continue
            if len(yes_seen) >= min_agree:
                pred = "DOWN"; opp_side = "no"
            elif len(no_seen) >= min_agree:
                pred = "UP"; opp_side = "yes"
            else:
                continue
            best_opp = None
            for p in ("poly", "predict", "lim"):
                a = snap.get(f"{p}_{opp_side}", 0)
                if 0 < a < 1 and OPP_MIN <= a <= OPP_MAX:
                    if best_opp is None or a < best_opp:
                        best_opp = a
            if best_opp is None:
                continue
            first = (sec, pred, best_opp)
            break
        if not first:
            continue
        sec, pred, opp = first
        winner = outcomes.get(ep)
        if winner not in ("UP", "DOWN"):
            continue
        n_signals += 1
        if winner == pred:
            profit = INVEST * (1 - opp) / opp
            total_profit += profit
            wins += 1
        else:
            total_profit -= INVEST
            losses += 1
    return n_signals, wins, losses, total_profit


def main():
    print("loading...", file=sys.stderr)
    data = collect()
    outcomes = load_outcomes("/root/data_btc_15m_research/market_outcomes.csv")
    print(f"windows: {len(data)}, outcomes: {len(outcomes)}", file=sys.stderr)

    period_hours = 0
    if data:
        eps = sorted(data.keys())
        period_hours = (eps[-1] - eps[0] + WINDOW_SEC) / 3600

    print()
    print(f"תקופה: כ-{period_hours:.0f} שעות ({period_hours/24:.1f} ימים)")
    print(f"השקעה לעסקה: ${INVEST}")
    print()
    print("=== השוואת ביצועים בדולרים ===")
    print()

    scenarios = [
        ("ללא סינון טרגט, הסכמה 2+", 2, None),
        ("ללא סינון טרגט, הסכמה 3+", 3, None),
        ("טרגט עד 30, הסכמה 2+", 2, 30),
        ("טרגט עד 30, הסכמה 3+", 3, 30),
        ("טרגט עד 30, הסכמה 4+", 4, 30),
        ("טרגט עד 50, הסכמה 2+", 2, 50),
        ("טרגט עד 50, הסכמה 3+", 3, 50),
        ("טרגט עד 100, הסכמה 2+", 2, 100),
        ("טרגט עד 100, הסכמה 3+", 3, 100),
    ]

    for name, ma, thr in scenarios:
        n, w, l, p = simulate(data, outcomes, ma, thr)
        per_day = p / (period_hours/24) if period_hours > 0 else 0
        per_trade = (p / n) if n > 0 else 0
        rate = (w / (w+l) * 100) if (w+l) > 0 else 0
        print(f"  {name}:")
        print(f"    {n} עסקאות, {w} ניצחו, {l} הפסידו, דיוק {rate:.0f}%")
        print(f"    סך רווח: ${p:+.2f}  |  ${per_trade:+.3f} לעסקה  |  ${per_day:+.2f} ביום")
        print()


if __name__ == "__main__":
    main()
