#!/usr/bin/env python3
"""V9 deep review: for each (time_bucket, price_bucket) of a dip buy
during a 5-min market, what is win-rate and dollar PnL at $10/trade?

Hypothesis: in seconds 0-40 statistics are ~50/50 so 200%+ payout
covers losses. After 40s, signal stronger so price already higher.

Scans Poly + Predict + Limitless 5m recordings on Helsinki.
"""

import csv
import sys
import re
from collections import defaultdict
from datetime import datetime


WINDOW_SEC = 300
INVEST = 10.0
MIN_DEPTH_USD = 10.0  # must be able to fill our invest

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
PRED = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"
OUTCOMES_PRED = "/root/data_predict_btc_5m/market_outcomes.csv"


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_predict_outcomes():
    """Predict has clean outcomes file. Key by market_open_epoch -> winner side."""
    out = {}
    try:
        with open(OUTCOMES_PRED) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_open_epoch", 0))
                    w = r.get("winner_side", "")
                    if ep and w in ("UP", "DOWN", "YES", "NO"):
                        # Predict YES = UP, NO = DOWN
                        w = "UP" if w in ("UP", "YES") else "DOWN"
                        out[ep] = w
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return out


def reconstruct_outcomes_from_poly():
    """Build outcome dict from poly per-second.

    Window ep -> 'UP' or 'DOWN' based on whichever side resolved to 1.0
    at last seen tick of the window (or just before next window).
    """
    out = {}
    try:
        with open(POLY) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch", 0))
                    es = int(r.get("epoch_sec", 0))
                    if not ep:
                        continue
                    sec = es - ep
                    if sec < 240:  # only late ticks
                        continue
                    up_bid = fnum(r.get("up_bid"))
                    dn_bid = fnum(r.get("down_bid"))
                    if up_bid >= 0.95:
                        out[ep] = "UP"
                    elif dn_bid >= 0.95:
                        out[ep] = "DOWN"
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return out


def scan_poly_dips(outcomes):
    """Yield (ep, sec, side, price, depth, win) tuples for poly."""
    rows = []
    try:
        with open(POLY) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch", 0))
                    es = int(r.get("epoch_sec", 0))
                    if not ep:
                        continue
                    sec = es - ep
                    if sec < 0 or sec > 300:
                        continue
                    if ep not in outcomes:
                        continue
                    winner = outcomes[ep]
                    ya = fnum(r.get("up_ask")); yu = fnum(r.get("up_usd_best"))
                    na = fnum(r.get("down_ask")); nu = fnum(r.get("down_usd_best"))
                    if 0.05 <= ya <= 0.50 and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, yu, 1 if winner == "UP" else 0, "poly"))
                    if 0.05 <= na <= 0.50 and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if winner == "DOWN" else 0, "poly"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return rows


def scan_predict_dips(outcomes):
    rows = []
    try:
        with open(PRED) as f:
            for r in csv.DictReader(f):
                try:
                    ep_raw = r.get("market_open_epoch")
                    sec_raw = r.get("sec_from_open")
                    if not ep_raw or not sec_raw:
                        continue
                    ep = int(ep_raw); sec = int(sec_raw)
                    if not ep or sec < 0 or sec > 300:
                        continue
                    if ep not in outcomes:
                        continue
                    winner = outcomes[ep]
                    ya = fnum(r.get("yes_ask")); yu = fnum(r.get("yes_ask_usd"))
                    na = fnum(r.get("no_ask_implied")); nu = fnum(r.get("no_ask_usd_buyable"))
                    if 0.05 <= ya <= 0.50 and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, yu, 1 if winner == "UP" else 0, "predict"))
                    if 0.05 <= na <= 0.50 and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if winner == "DOWN" else 0, "predict"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return rows


def scan_lim_dips(outcomes):
    rows = []
    slug_re = re.compile(r"-5-min-(\d{10,13})$")
    try:
        with open(LIM) as f:
            for r in csv.DictReader(f):
                try:
                    slug = r.get("slug", "")
                    m = slug_re.search(slug)
                    if not m:
                        continue
                    lim_id = int(m.group(1))
                    if lim_id > 10**12:
                        lim_id = lim_id // 1000
                    ep = (lim_id // WINDOW_SEC) * WINDOW_SEC
                    if ep not in outcomes:
                        continue
                    winner = outcomes[ep]
                    es = int(r["epoch_sec"]); sec = es - ep
                    if sec < 0 or sec > 300:
                        continue
                    ya = fnum(r.get("best_ask")); yu = fnum(r.get("best_ask_size_usd"))
                    na = fnum(r.get("no_best_ask")); nu = fnum(r.get("no_best_ask_size_usd"))
                    if 0.05 <= ya <= 0.50 and yu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "UP", ya, yu, 1 if winner == "UP" else 0, "lim"))
                    if 0.05 <= na <= 0.50 and nu >= MIN_DEPTH_USD:
                        rows.append((ep, sec, "DOWN", na, nu, 1 if winner == "DOWN" else 0, "lim"))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return rows


def first_dip_per_window(rows, sec_min, sec_max, price_max):
    """For each (ep, side, plat) take only the FIRST qualifying dip."""
    seen = set()
    keep = []
    rows_sorted = sorted(rows, key=lambda x: (x[0], x[1]))
    for ep, sec, side, price, depth, win, plat in rows_sorted:
        if sec < sec_min or sec > sec_max:
            continue
        if price > price_max:
            continue
        key = (ep, side, plat)
        if key in seen:
            continue
        seen.add(key)
        keep.append((ep, sec, side, price, depth, win, plat))
    return keep


def pnl(filtered):
    """Sum dollar PnL at $INVEST per trade, hold to expiry."""
    total = 0.0
    wins = 0
    for ep, sec, side, price, depth, win, plat in filtered:
        if win:
            total += INVEST * (1 - price) / price
            wins += 1
        else:
            total -= INVEST
    return total, wins, len(filtered)


def main():
    print("loading outcomes...", file=sys.stderr)
    outcomes = reconstruct_outcomes_from_poly()
    print(f"poly-derived outcomes: {len(outcomes)}", file=sys.stderr)

    print("scanning poly...", file=sys.stderr)
    poly_rows = scan_poly_dips(outcomes)
    print(f"poly dip rows: {len(poly_rows)}", file=sys.stderr)

    print("scanning predict...", file=sys.stderr)
    pred_rows = scan_predict_dips(outcomes)
    print(f"predict dip rows: {len(pred_rows)}", file=sys.stderr)

    print("scanning limitless...", file=sys.stderr)
    lim_rows = scan_lim_dips(outcomes)
    print(f"limitless dip rows: {len(lim_rows)}", file=sys.stderr)

    all_rows = poly_rows + pred_rows + lim_rows
    print(f"total: {len(all_rows)} dip moments across windows", file=sys.stderr)

    if not all_rows:
        print("no data", file=sys.stderr)
        return

    period_hours = 0
    eps = sorted(set(r[0] for r in all_rows))
    if eps:
        period_hours = (eps[-1] - eps[0] + WINDOW_SEC) / 3600

    print()
    print(f"=== סקירה עמוקה V9 ===")
    print(f"תקופה: {period_hours:.0f} שעות, {period_hours/24:.1f} ימים")
    print(f"חלונות עם תוצאה: {len(eps)}")
    print(f"השקעה לעסקה: ${INVEST}")
    print(f"עומק מינ׳: ${MIN_DEPTH_USD}")
    print()

    print("=== חיתוך ראשי לפי טווח שניות, מחיר עד 0.30 ===")
    print("טווח 0-20  / 0-30 / 0-40 / 0-60 / 0-90 / 0-120")
    print()
    for sec_max in [20, 30, 40, 60, 90, 120]:
        filt = first_dip_per_window(all_rows, 0, sec_max, 0.30)
        p, w, n = pnl(filt)
        rate = (w / n * 100) if n > 0 else 0
        per_trade = (p / n) if n > 0 else 0
        per_day = p / (period_hours / 24) if period_hours > 0 else 0
        print(f"  שניות 0-{sec_max} מחיר עד 0.30: {n} עסקאות, {w} זכו, דיוק {rate:.0f}%, רווח ${p:+.0f}, לעסקה ${per_trade:+.2f}, יומי ${per_day:+.0f}")

    print()
    print("=== חיתוך לפי טווח מחיר, שניות 0-40 ===")
    print()
    for price_max, name in [(0.15, "עד 0.15"), (0.20, "עד 0.20"), (0.25, "עד 0.25"),
                              (0.30, "עד 0.30"), (0.35, "עד 0.35"), (0.40, "עד 0.40"),
                              (0.50, "עד 0.50")]:
        filt = first_dip_per_window(all_rows, 0, 40, price_max)
        p, w, n = pnl(filt)
        rate = (w / n * 100) if n > 0 else 0
        per_trade = (p / n) if n > 0 else 0
        per_day = p / (period_hours / 24) if period_hours > 0 else 0
        print(f"  מחיר {name}: {n} עסקאות, {w} זכו, דיוק {rate:.0f}%, רווח ${p:+.0f}, לעסקה ${per_trade:+.2f}, יומי ${per_day:+.0f}")

    print()
    print("=== מטריצה: זמן × מחיר ===")
    print()
    print(f"{'מחיר/זמן':<14}{'0-20':>10}{'0-40':>10}{'0-60':>10}{'0-90':>10}{'0-120':>10}")
    for price_max in [0.15, 0.20, 0.25, 0.30, 0.35]:
        row = [f"≤{price_max:.2f}".rjust(14)]
        for sec_max in [20, 40, 60, 90, 120]:
            filt = first_dip_per_window(all_rows, 0, sec_max, price_max)
            p, w, n = pnl(filt)
            cell = f"${p:+.0f}".rjust(10)
            row.append(cell)
        print("".join(row))

    print()
    print("=== מטריצת דיוק ===")
    print()
    print(f"{'מחיר/זמן':<14}{'0-20':>10}{'0-40':>10}{'0-60':>10}{'0-90':>10}{'0-120':>10}")
    for price_max in [0.15, 0.20, 0.25, 0.30, 0.35]:
        row = [f"≤{price_max:.2f}".rjust(14)]
        for sec_max in [20, 40, 60, 90, 120]:
            filt = first_dip_per_window(all_rows, 0, sec_max, price_max)
            p, w, n = pnl(filt)
            rate = (w / n * 100) if n > 0 else 0
            cell = f"{rate:.0f}% n={n}".rjust(10)
            row.append(cell)
        print("".join(row))

    print()
    print("=== חיתוך לעומק גבוה — האם אפשר להגיע ל-$50? ===")
    print()
    for min_d in [10, 20, 50, 100]:
        rows_d = [r for r in all_rows if r[4] >= min_d]
        filt = first_dip_per_window(rows_d, 0, 40, 0.30)
        p, w, n = pnl(filt)
        rate = (w / n * 100) if n > 0 else 0
        per_day = p / (period_hours / 24) if period_hours > 0 else 0
        print(f"  עומק מינ׳ ${min_d}: {n} עסקאות, דיוק {rate:.0f}%, רווח ${p:+.0f}, יומי ${per_day:+.0f}")

    print()
    print("=== פלטפורמה הכי טובה בשניות 0-40 מחיר עד 0.30 ===")
    print()
    for plat in ["poly", "predict", "lim"]:
        rows_p = [r for r in all_rows if r[6] == plat]
        filt = first_dip_per_window(rows_p, 0, 40, 0.30)
        p, w, n = pnl(filt)
        rate = (w / n * 100) if n > 0 else 0
        print(f"  {plat}: {n} עסקאות, דיוק {rate:.0f}%, רווח ${p:+.0f}")


if __name__ == "__main__":
    main()
