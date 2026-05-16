#!/usr/bin/env python3
"""Calendar spread across platforms and window pairs.

For each platform with both windows recorded:
  - Find common close moments
  - At T-30 sec, snapshot YES asks and strikes on both markets
  - Apply strike-monotonic rule: lower-strike YES should be >= higher-strike YES
  - Count consistent vs violations
  - Also collect Binance reference price at T

Platforms differ in column schemas, so per-platform loaders are defined.
Run with CLI arg "15m_1h" or "5m_15m" or "all" depending on server.
"""

import csv
import sys
import re
from collections import defaultdict
from datetime import datetime


WINDOW_5M = 300
WINDOW_15M = 900
WINDOW_1H = 3600


def fnum(s):
    try:
        return float(s) if s not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def iso_to_epoch(iso):
    try:
        if " " in iso:
            iso = iso.replace(" ", "T") + "+00:00"
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return 0


# ----- per-platform loaders -----
# Each returns dict: (ep, sec_from_start) -> dict with up_ask, down_ask, strike, btc


def load_poly_5m(path):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch") or 0)
                    es = int(r.get("epoch_sec") or 0)
                    if not ep: continue
                    sec = es - ep
                    if sec < 0 or sec > WINDOW_5M: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("up_ask")),
                        "down_ask": fnum(r.get("down_ask")),
                        "strike": fnum(r.get("target_chainlink_at_open")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_poly_research(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch") or 0)
                    es = int(r.get("epoch_sec") or 0)
                    if not ep: continue
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("up_ask")),
                        "down_ask": fnum(r.get("down_ask")),
                        "strike": fnum(r.get("target_chainlink_at_open")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_predict(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep_raw = r.get("market_open_epoch")
                    if not ep_raw: continue
                    ep = int(ep_raw)
                    sec_raw = r.get("sec_from_open")
                    if not sec_raw: continue
                    sec = int(sec_raw)
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("yes_ask")),
                        "down_ask": fnum(r.get("no_ask_implied")),
                        "strike": fnum(r.get("strike")),
                        "btc": fnum(r.get("binance_now")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_kalshi(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ot = r.get("open_time", "")
                    if not ot: continue
                    ep = iso_to_epoch(ot)
                    if not ep: continue
                    # align to canonical window
                    ep = (ep // win_sec) * win_sec
                    es = int(r.get("epoch_sec") or 0)
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("yes_ask")),
                        "down_ask": fnum(r.get("no_ask")),
                        "strike": fnum(r.get("floor_strike")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_gemini(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ot = r.get("open_time", "")
                    if not ot: continue
                    ep = iso_to_epoch(ot)
                    if not ep: continue
                    ep = (ep // win_sec) * win_sec
                    es = int(r.get("epoch_sec") or 0)
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("yes_ask")),
                        "down_ask": fnum(r.get("no_ask_implied")),
                        "strike": fnum(r.get("strike")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_limitless(path, win_sec):
    """Limitless uses slug -> embedded epoch."""
    out = {}
    slug_re = re.compile(rf"-{int(win_sec/60)}-min-(\d{{10,13}})$") if win_sec < 3600 else \
              re.compile(rf"-1-hour-(\d{{10,13}})$")
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    slug = r.get("slug", "")
                    m = slug_re.search(slug)
                    if not m: continue
                    lim_id = int(m.group(1))
                    if lim_id > 10**12: lim_id = lim_id // 1000
                    ep = (lim_id // win_sec) * win_sec
                    es = int(r.get("epoch_sec") or 0)
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "up_ask": fnum(r.get("best_ask")),
                        "down_ask": fnum(r.get("no_best_ask")),
                        "strike": 0,  # not in recorder; will skip strike check
                        "btc": 0,
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


# ----- analysis -----


def analyze_pair(data_short, win_short, data_long, win_long, common_period, snap_offset=30):
    """For each common close T (T % common_period == 0):
       short market opened at T - win_short, long at T - win_long.
       Snapshot both at sec = (win - snap_offset)."""
    # Any market_epoch present means there's a closing time at ep + win
    short_eps = {ep for (ep, sec) in data_short}
    common_T = {ep + win_short for ep in short_eps}
    common_T = {t for t in common_T if t % common_period == 0}
    common_T = sorted(common_T)

    stats = {"total": 0, "both_present": 0, "both_liquid": 0,
             "consistent": 0, "violations": 0, "examples": []}
    LIQUID_LO, LIQUID_HI = 0.03, 0.97
    GAP_MIN = 0.05

    for T in common_T:
        stats["total"] += 1
        ep_s = T - win_short
        ep_l = T - win_long
        snap_s = data_short.get((ep_s, win_short - snap_offset))
        snap_l = data_long.get((ep_l, win_long - snap_offset))
        if not snap_s or not snap_l:
            continue
        stats["both_present"] += 1
        ys, yl = snap_s["up_ask"], snap_l["up_ask"]
        if not (LIQUID_LO <= ys <= LIQUID_HI): continue
        if not (LIQUID_LO <= yl <= LIQUID_HI): continue
        stats["both_liquid"] += 1
        ss, sl = snap_s["strike"], snap_l["strike"]
        if ss <= 0 or sl <= 0:
            # if both strikes missing (limitless), we can still compare
            # the prices directly assuming same outcome, but skip for now
            continue
        if abs(ss - sl) > 500: continue
        # strike-monotonic rule
        if ss < sl:
            low_yes, high_yes = ys, yl
        elif ss > sl:
            low_yes, high_yes = yl, ys
        else:
            low_yes = high_yes = ys
        gap = low_yes - high_yes
        if gap < -GAP_MIN:
            stats["violations"] += 1
            stats["examples"].append({
                "T": T, "ss": ss, "sl": sl, "ys": ys, "yl": yl,
                "gap": gap, "btc": snap_s.get("btc", 0),
            })
        else:
            stats["consistent"] += 1
    return stats


def report(name, stats):
    print(f"  {name}:")
    print(f"    סגירות משותפות: {stats['total']}")
    print(f"    שני השווקים נוכחים: {stats['both_present']}")
    print(f"    שניהם נזילים: {stats['both_liquid']}")
    print(f"    עקבי: {stats['consistent']}")
    print(f"    הפרות > 5¢: {stats['violations']}")
    if stats["violations"]:
        stats["examples"].sort(key=lambda r: r["gap"])
        print(f"    --- עד 3 הפרות גדולות ---")
        for v in stats["examples"][:3]:
            print(f"      T={v['T']} BTC={v['btc']:.0f} | strikes {v['ss']:.0f}/{v['sl']:.0f} | YES {v['ys']:.2f}/{v['yl']:.2f} | פער {v['gap']:+.2f}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("15m_1h", "all"):
        print("=== 15 דקות מול שעה — Hetzner ===")
        print()

        poly_15m = load_poly_research("/root/data_btc_15m_research/combined_per_second.csv", WINDOW_15M)
        poly_1h  = load_poly_research("/root/data_btc_1h_research/combined_per_second.csv", WINDOW_1H)
        if poly_15m and poly_1h:
            st = analyze_pair(poly_15m, WINDOW_15M, poly_1h, WINDOW_1H, common_period=WINDOW_1H)
            report("Polymarket", st)

        pred_15m = load_predict("/root/data_predict_btc_15m/combined_per_second.csv", WINDOW_15M)
        pred_1h  = load_predict("/root/data_predict_btc_1h/combined_per_second.csv", WINDOW_1H)
        if pred_15m and pred_1h:
            st = analyze_pair(pred_15m, WINDOW_15M, pred_1h, WINDOW_1H, common_period=WINDOW_1H)
            report("Predict.fun", st)

        ks_15m = load_kalshi("/root/data_kalshi_btc_15m/combined_per_second.csv", WINDOW_15M)
        ks_1h  = load_kalshi("/root/data_kalshi_btc_1h/combined_per_second.csv", WINDOW_1H)
        if ks_15m and ks_1h:
            st = analyze_pair(ks_15m, WINDOW_15M, ks_1h, WINDOW_1H, common_period=WINDOW_1H)
            report("Kalshi", st)

        lim_15m = load_limitless("/root/data_limitless_btc_15m/combined_per_second.csv", WINDOW_15M)
        lim_1h  = load_limitless("/root/data_limitless_btc_1h/combined_per_second.csv", WINDOW_1H)
        if lim_15m and lim_1h:
            st = analyze_pair(lim_15m, WINDOW_15M, lim_1h, WINDOW_1H, common_period=WINDOW_1H)
            report("Limitless (ללא טרגט בקובץ)", st)

    if mode in ("5m_15m", "all"):
        print()
        print("=== 5 דקות מול 15 דקות — Helsinki ===")
        print()

        poly_5m = load_poly_research("/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv", WINDOW_5M)
        poly_15m_h = load_poly_research("/root/data_btc_15m_research/combined_per_second.csv", WINDOW_15M)
        if poly_5m and poly_15m_h:
            st = analyze_pair(poly_5m, WINDOW_5M, poly_15m_h, WINDOW_15M, common_period=WINDOW_15M)
            report("Polymarket 5דק/15דק", st)
        else:
            print("  Polymarket: חסר 15דק לוקאלי")

        lim_5m = load_limitless("/root/data_limitless_btc_5m/combined_per_second.csv", WINDOW_5M)
        lim_15m_h = load_limitless("/root/data_limitless_btc_15m/combined_per_second.csv", WINDOW_15M)
        if lim_5m and lim_15m_h:
            st = analyze_pair(lim_5m, WINDOW_5M, lim_15m_h, WINDOW_15M, common_period=WINDOW_15M)
            report("Limitless 5דק/15דק", st)


if __name__ == "__main__":
    main()
