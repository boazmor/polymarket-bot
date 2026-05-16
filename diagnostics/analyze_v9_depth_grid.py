#!/usr/bin/env python3
"""V9 depth grid: where in (price, time) does 5-min market actually have
liquidity?

Scans all 3 platforms (poly, predict, lim) per-second data.
Records moments where ask is in [0.10, 0.50] with depth >= MIN_DEPTH.
Buckets by price (1¢ steps) and time-in-window (5s steps).

Output: counts per (price_bucket, time_bucket).
"""

import csv
import sys
import re
from collections import defaultdict


WINDOW_SEC = 300
MIN_DEPTH = 10.0


def fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def scan_poly():
    """Poly 5m on USA at /root/research/multi_coin/data_btc_5m_research/.
    Columns: ..., epoch_sec=2, market_epoch=4, up_ask=17, up_usd_best=20,
    down_ask=32, down_usd_best=35."""
    moments = []
    path = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
    try:
        with open(path) as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    ep_raw = r.get("market_epoch")
                    es_raw = r.get("epoch_sec")
                    if not ep_raw or not es_raw:
                        continue
                    ep = int(ep_raw); es = int(es_raw); sec = es - ep
                    if sec < 0 or sec > 300:
                        continue
                    ya = fnum(r.get("up_ask"))
                    yu = fnum(r.get("up_usd_best"))
                    na = fnum(r.get("down_ask"))
                    nu = fnum(r.get("down_usd_best"))
                    if 0.10 <= ya <= 0.50 and yu >= MIN_DEPTH:
                        moments.append(("poly", "yes", sec, ya, yu))
                    if 0.10 <= na <= 0.50 and nu >= MIN_DEPTH:
                        moments.append(("poly", "no", sec, na, nu))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return moments


def scan_predict():
    moments = []
    path = "/root/data_predict_btc_5m/combined_per_second.csv"
    try:
        with open(path) as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    ep_raw = r.get("market_open_epoch")
                    sec_raw = r.get("sec_from_open")
                    if not ep_raw or not sec_raw:
                        continue
                    sec = int(sec_raw)
                    if sec < 0 or sec > 300:
                        continue
                    ya = fnum(r.get("yes_ask"))
                    yu = fnum(r.get("yes_ask_usd"))
                    na = fnum(r.get("no_ask_implied"))
                    nu = fnum(r.get("no_ask_usd_buyable"))
                    if 0.10 <= ya <= 0.50 and yu >= MIN_DEPTH:
                        moments.append(("predict", "yes", sec, ya, yu))
                    if 0.10 <= na <= 0.50 and nu >= MIN_DEPTH:
                        moments.append(("predict", "no", sec, na, nu))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return moments


def scan_lim():
    moments = []
    path = "/root/data_limitless_btc_5m/combined_per_second.csv"
    slug_re = re.compile(r"-5-min-(\d{10,13})$")
    try:
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
                    es = int(r["epoch_sec"]); sec = es - ep
                    if sec < 0 or sec > 300:
                        continue
                    ya = fnum(r.get("best_ask"))
                    yu = fnum(r.get("best_ask_size_usd"))
                    na = fnum(r.get("no_best_ask"))
                    nu = fnum(r.get("no_best_ask_size_usd"))
                    if 0.10 <= ya <= 0.50 and yu >= MIN_DEPTH:
                        moments.append(("lim", "yes", sec, ya, yu))
                    if 0.10 <= na <= 0.50 and nu >= MIN_DEPTH:
                        moments.append(("lim", "no", sec, na, nu))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return moments


def main():
    print(f"scanning 5-min recorders for depth >= ${MIN_DEPTH}...", file=sys.stderr)
    all_moments = []
    all_moments.extend(scan_poly())
    all_moments.extend(scan_predict())
    all_moments.extend(scan_lim())
    print(f"total qualifying moments: {len(all_moments)}", file=sys.stderr)

    # Bucket by price (5-cent) and time (5-sec)
    grid = defaultdict(int)
    for plat, side, sec, price, depth in all_moments:
        price_bucket = int(price * 20) * 5  # round to nearest 5 cents
        time_bucket = (sec // 5) * 5
        grid[(price_bucket, time_bucket)] += 1

    # By time bucket only — when is liquidity richest?
    print()
    print(f"=== כמה רגעים עם עומק 10 דולר ומעלה, לפי שניה בחלון ===")
    by_sec = defaultdict(int)
    for (_, sec_b), n in grid.items():
        by_sec[sec_b] += n
    for sec_b in sorted(by_sec.keys()):
        print(f"  שניות {sec_b}-{sec_b+4}: {by_sec[sec_b]} רגעים")

    # By price bucket only
    print()
    print(f"=== כמה רגעים, לפי מחיר ===")
    by_price = defaultdict(int)
    for (price_b, _), n in grid.items():
        by_price[price_b] += n
    for price_b in sorted(by_price.keys()):
        print(f"  מחיר {price_b}-{price_b+4} סנט: {by_price[price_b]} רגעים")

    # First 60 seconds: most actionable for V9
    print()
    print(f"=== עומק מלא ב-60 שניות הראשונות, חיתוך מחיר ===")
    for price_min, price_max, name in [(10, 20, "10-20 סנט"), (20, 30, "20-30 סנט"),
                                         (30, 40, "30-40 סנט"), (40, 50, "40-50 סנט")]:
        bucket = [m for m in all_moments if 60 >= m[2] >= 0 and price_min/100 <= m[3] <= price_max/100]
        if not bucket:
            print(f"  {name}: 0 רגעים")
            continue
        avg_depth = sum(m[4] for m in bucket) / len(bucket)
        plats = defaultdict(int)
        for m in bucket:
            plats[m[0]] += 1
        plat_str = ", ".join(f"{p}={n}" for p, n in plats.items())
        print(f"  {name}: {len(bucket)} רגעים, עומק ממוצע ${avg_depth:.1f}, פלטפורמות: {plat_str}")


if __name__ == "__main__":
    main()
