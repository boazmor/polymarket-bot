#!/usr/bin/env python3
"""BTC backtest — slice PnL by price, seconds, distance, platform.

Strategy: at each second of each 5-min market, if best_ask <= 0.35
record a hypothetical $1 buy. Hold to expiry. Win pays $1, lose pays $0.
Aggregate per bucket.

Distance is Polymarket-only (its recorder is the only one with target/distance).
"""
import csv
import os
from collections import defaultdict

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PREDICT = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"

MAX_ASK = 0.35
INVEST = 1.0


def price_bucket(p):
    if p <= 0.05: return "0.00-0.05"
    if p <= 0.10: return "0.05-0.10"
    if p <= 0.15: return "0.10-0.15"
    if p <= 0.20: return "0.15-0.20"
    if p <= 0.25: return "0.20-0.25"
    if p <= 0.30: return "0.25-0.30"
    return "0.30-0.35"


def sec_bucket(s):
    if s <= 30: return "000-030"
    if s <= 60: return "030-060"
    if s <= 120: return "060-120"
    if s <= 180: return "120-180"
    if s <= 240: return "180-240"
    return "240-300"


def dist_bucket(d):
    a = abs(d)
    if a <= 20: return "0-20"
    if a <= 40: return "20-40"
    if a <= 60: return "40-60"
    if a <= 100: return "60-100"
    if a <= 200: return "100-200"
    return "200+"


def load_poly_outcomes():
    out = {}
    if not os.path.exists(POLY_OUT):
        return out
    with open(POLY_OUT) as f:
        for r in csv.DictReader(f):
            try:
                out[int(r["market_epoch"])] = r.get("winner_side", "")
            except (ValueError, KeyError):
                pass
    return out


def collect_poly_trades(outcomes):
    """Yields (price, sec, distance, won) per opportunity."""
    if not os.path.exists(POLY):
        return
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"])
                if ep not in outcomes:
                    continue
                winner = outcomes[ep]
                if winner not in ("UP", "DOWN"):
                    continue
                sec = int(r["sec_from_start"])
                if sec < 0 or sec > 300:
                    continue
                dist = float(r.get("distance_signed") or 0)
                ua = float(r.get("up_ask") or 0)
                da = float(r.get("down_ask") or 0)
                if 0 < ua <= MAX_ASK:
                    yield ("poly", ua, sec, dist, winner == "UP")
                if 0 < da <= MAX_ASK:
                    yield ("poly", da, sec, -dist, winner == "DOWN")
            except (ValueError, KeyError):
                continue


def derive_predict_outcome(rows):
    """Predict.fun outcome from last row or settled flag."""
    last = rows[-1]
    if last.get("settled_yes") in ("1", "true", "True"):
        return "YES"
    if last.get("settled_no") in ("1", "true", "True"):
        return "NO"
    return None


def collect_predict_trades():
    if not os.path.exists(PREDICT):
        return
    by_market = defaultdict(list)
    with open(PREDICT) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if len(rows) < 10:
            continue
        try:
            strike = float(rows[0].get("strike") or 0) or None
        except ValueError:
            strike = None
        try:
            close = float(rows[-1].get("binance_now") or 0) or None
        except ValueError:
            close = None
        if strike is None or close is None:
            continue
        winner = "YES" if close > strike else "NO"
        for r in rows:
            try:
                sec = int(r.get("sec_from_open") or -1)
                if sec < 0 or sec > 300:
                    continue
                dist = float(r.get("distance_signed") or 0)
                ya = float(r.get("yes_ask") or 0)
                na = float(r.get("no_ask_implied") or 0)
                if 0 < ya <= MAX_ASK:
                    yield ("predict", ya, sec, dist, winner == "YES")
                if 0 < na <= MAX_ASK:
                    yield ("predict", na, sec, -dist, winner == "NO")
            except (ValueError, KeyError):
                continue


def collect_lim_trades():
    if not os.path.exists(LIM):
        return
    by_market = defaultdict(list)
    with open(LIM) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if len(rows) < 10:
            continue
        try:
            first_ts = int(rows[0]["epoch_sec"])
        except (ValueError, KeyError):
            continue
        try:
            target = float(rows[0].get("target_price") or 0) or None
            close = float(rows[-1].get("binance_now") or 0) or None
        except ValueError:
            continue
        if not target or not close:
            continue
        winner = "YES" if close > target else "NO"
        for r in rows:
            try:
                sec = int(r["epoch_sec"]) - first_ts
                if sec < 0 or sec > 300:
                    continue
                dist = float(r.get("distance_signed") or 0)
                ya = float(r.get("best_ask") or 0)
                na = float(r.get("no_best_ask") or 0)
                if 0 < ya <= MAX_ASK:
                    yield ("lim", ya, sec, dist, winner == "YES")
                if 0 < na <= MAX_ASK:
                    yield ("lim", na, sec, -dist, winner == "NO")
            except (ValueError, KeyError):
                continue


def pnl(price, won):
    shares = INVEST / price
    return (shares - INVEST) if won else -INVEST


def aggregate(trades, key_fn):
    bucket = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "sum_price": 0.0})
    for t in trades:
        k = key_fn(t)
        if k is None:
            continue
        b = bucket[k]
        b["n"] += 1
        b["wins"] += 1 if t[4] else 0
        b["pnl"] += pnl(t[1], t[4])
        b["sum_price"] += t[1]
    return bucket


def print_cut(title, bucket, sort_key=None):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)
    print(f"{'BUCKET':<14} {'N':>7} {'WIN%':>6} {'AVG_P':>6} {'PNL$':>9} {'ROI%':>7}")
    print("-" * 60)
    keys = sorted(bucket.keys(), key=sort_key) if sort_key else sorted(bucket.keys())
    grand = {"n": 0, "wins": 0, "pnl": 0.0, "sum_price": 0.0}
    for k in keys:
        b = bucket[k]
        if b["n"] == 0:
            continue
        win_pct = 100 * b["wins"] / b["n"]
        avg_p = b["sum_price"] / b["n"]
        roi = 100 * b["pnl"] / (b["n"] * INVEST)
        print(f"{str(k):<14} {b['n']:>7} {win_pct:>5.1f}% {avg_p:>6.3f} {b['pnl']:>+9.2f} {roi:>+6.1f}%")
        for kk in grand:
            grand[kk] += b[kk]
    if grand["n"]:
        win_pct = 100 * grand["wins"] / grand["n"]
        avg_p = grand["sum_price"] / grand["n"]
        roi = 100 * grand["pnl"] / (grand["n"] * INVEST)
        print("-" * 60)
        print(f"{'TOTAL':<14} {grand['n']:>7} {win_pct:>5.1f}% {avg_p:>6.3f} {grand['pnl']:>+9.2f} {roi:>+6.1f}%")


def main():
    outcomes = load_poly_outcomes()
    print(f"Poly outcomes loaded: {len(outcomes)}")

    all_trades = []
    poly_trades = list(collect_poly_trades(outcomes))
    pred_trades = list(collect_predict_trades())
    lim_trades = list(collect_lim_trades())
    all_trades = poly_trades + pred_trades + lim_trades

    print(f"Poly opportunities: {len(poly_trades)}")
    print(f"Predict opportunities: {len(pred_trades)}")
    print(f"Limitless opportunities: {len(lim_trades)}")
    print(f"TOTAL opportunities: {len(all_trades)}")

    print_cut("CUT 1 — by PLATFORM", aggregate(all_trades, lambda t: t[0]))
    print_cut("CUT 2 — by PRICE bucket", aggregate(all_trades, lambda t: price_bucket(t[1])))
    print_cut("CUT 3 — by SECOND bucket", aggregate(all_trades, lambda t: sec_bucket(t[2])))
    print_cut("CUT 4 — by DISTANCE bucket",
              aggregate(all_trades, lambda t: dist_bucket(t[3]) if t[3] is not None else None))

    print()
    print("=" * 70)
    print("CROSS — PLATFORM x PRICE bucket (PnL$)")
    print("=" * 70)
    cross = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for t in all_trades:
        k = (t[0], price_bucket(t[1]))
        cross[k]["n"] += 1
        cross[k]["wins"] += 1 if t[4] else 0
        cross[k]["pnl"] += pnl(t[1], t[4])
    plats = ["poly", "predict", "lim"]
    buckets = ["0.00-0.05","0.05-0.10","0.10-0.15","0.15-0.20","0.20-0.25","0.25-0.30","0.30-0.35"]
    header = f"{'BUCKET':<12}" + "".join(f"{p:>14}" for p in plats)
    print(header)
    for b in buckets:
        line = f"{b:<12}"
        for p in plats:
            c = cross.get((p, b), {"n":0,"wins":0,"pnl":0.0})
            if c["n"]:
                roi = 100*c["pnl"]/c["n"]
                line += f" {c['n']:>5} {roi:>+6.1f}%"
            else:
                line += f"{'-':>14}"
        print(line)


if __name__ == "__main__":
    main()
