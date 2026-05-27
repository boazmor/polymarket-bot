#!/usr/bin/env python3
"""Poly+Predict consensus strategy.

When Poly and Predict both price the SAME direction as likely (>0.55) at a
reference second, buy that side on the CHEAPER platform. Hold to expiry.
Grade each trade by the platform's own oracle outcome.

Compare to baseline: blind buy at the same second.
"""
import csv
import os
from collections import defaultdict

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PRED = "/root/data_predict_btc_5m/combined_per_second.csv"

INVEST = 1.0
REF_SECS = [30, 60, 90, 120, 180, 240]
WIN_HALF = 10  # median over a window
THRESHOLDS = [0.55, 0.60, 0.70, 0.80]


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def load_poly_snapshots():
    """dict[(window_ep, ref_sec)] -> (up_ask, down_ask)."""
    rows_by_ep = defaultdict(list)
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"])
                sec = int(r["sec_from_start"])
                ua = float(r.get("up_ask") or 0)
                da = float(r.get("down_ask") or 0)
                rows_by_ep[ep].append((sec, ua, da))
            except (ValueError, KeyError):
                continue
    snaps = {}
    for ep, rows in rows_by_ep.items():
        for ref in REF_SECS:
            ua_samples = [ua for s, ua, da in rows if abs(s - ref) <= WIN_HALF and ua > 0]
            da_samples = [da for s, ua, da in rows if abs(s - ref) <= WIN_HALF and da > 0]
            if ua_samples and da_samples:
                snaps[(ep, ref)] = (median(ua_samples), median(da_samples))
    return snaps


def load_predict_snapshots():
    """dict[(window_ep, ref_sec)] -> (yes_ask, no_ask)."""
    by_market = defaultdict(list)
    with open(PRED) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    snaps = {}
    for mid, rows in by_market.items():
        try:
            ep = int(rows[0].get("market_open_epoch") or 0)
        except ValueError:
            continue
        if not ep:
            continue
        for ref in REF_SECS:
            ya_samples = []
            na_samples = []
            for r in rows:
                try:
                    sec = int(r.get("sec_from_open") or -1)
                    if abs(sec - ref) > WIN_HALF:
                        continue
                    ya = float(r.get("yes_ask") or 0)
                    na = float(r.get("no_ask_implied") or 0)
                    if ya > 0: ya_samples.append(ya)
                    if na > 0: na_samples.append(na)
                except (ValueError, KeyError):
                    continue
            if ya_samples and na_samples:
                snaps[(ep, ref)] = (median(ya_samples), median(na_samples))
    return snaps


def load_poly_outcomes():
    """dict[window_ep] -> 'UP'/'DOWN' from Chainlink."""
    out = {}
    with open(POLY_OUT) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"])
                w = r.get("winner_side", "")
                if w in ("UP", "DOWN"):
                    out[ep] = w
            except (ValueError, KeyError):
                pass
    return out


def load_predict_outcomes():
    out = {}
    by_market = defaultdict(list)
    with open(PRED) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if len(rows) < 10:
            continue
        try:
            ep = int(rows[0].get("market_open_epoch") or 0)
            strike = float(rows[0].get("strike") or 0)
        except (ValueError, TypeError):
            continue
        if not ep or not strike:
            continue
        last_binance = None
        for r in reversed(rows):
            try:
                v = float(r.get("binance_now") or 0)
                if v > 0:
                    last_binance = v; break
            except (ValueError, TypeError):
                continue
        if last_binance is not None:
            out[ep] = "UP" if last_binance > strike else "DOWN"
    return out


def pnl(price, won):
    return ((INVEST / price) - INVEST) if won else -INVEST


def main():
    print("Loading...")
    poly_snaps = load_poly_snapshots()
    pred_snaps = load_predict_snapshots()
    poly_out = load_poly_outcomes()
    pred_out = load_predict_outcomes()

    print(f"  Poly snapshots: {len(poly_snaps)}")
    print(f"  Predict snapshots: {len(pred_snaps)}")
    print(f"  Poly outcomes: {len(poly_out)}")
    print(f"  Predict outcomes: {len(pred_out)}")
    print()

    common_eps = set(poly_out) & set(pred_out)
    print(f"Windows with both outcomes: {len(common_eps)}")
    print()

    # For each ref_sec and threshold, run strategy
    print("=" * 90)
    print("STRATEGY: when Poly_UP > THR AND Predict_UP > THR  -> buy UP on cheaper platform")
    print("STRATEGY: when Poly_DN > THR AND Predict_DN > THR  -> buy DOWN on cheaper platform")
    print("=" * 90)

    for ref in REF_SECS:
        print(f"\n--- Reference sec = {ref} ---")
        print(f"{'THR':<6} {'N':>4} {'UP_pick':>8} {'DN_pick':>8} {'WIN%':>6} {'AVG_P':>6} {'PNL$':>8} {'ROI%':>7} {'POLY%':>7} {'PRED%':>7}")
        for thr in THRESHOLDS:
            trades = []
            for ep in common_eps:
                if (ep, ref) not in poly_snaps or (ep, ref) not in pred_snaps:
                    continue
                pua, pda = poly_snaps[(ep, ref)]
                yua, yda = pred_snaps[(ep, ref)]
                # Implied UP probability is the ask of UP (~prob of UP winning)
                # Strategy: agree on side if both think same side likely
                # "Likely" means ask >= thr (market prices it as >= thr to buy)
                if pua >= thr and yua >= thr:
                    # both think UP likely -> buy UP on cheaper
                    if pua <= yua:
                        platform = "poly"; price = pua; side = "UP"
                        won = poly_out[ep] == "UP"
                    else:
                        platform = "pred"; price = yua; side = "UP"
                        won = pred_out[ep] == "UP"
                    trades.append((platform, side, price, won))
                elif pda >= thr and yda >= thr:
                    # both think DOWN likely -> buy DOWN on cheaper
                    if pda <= yda:
                        platform = "poly"; price = pda; side = "DOWN"
                        won = poly_out[ep] == "DOWN"
                    else:
                        platform = "pred"; price = yda; side = "DOWN"
                        won = pred_out[ep] == "DOWN"
                    trades.append((platform, side, price, won))
            if not trades:
                continue
            n = len(trades)
            up_n = sum(1 for t in trades if t[1] == "UP")
            dn_n = n - up_n
            wins = sum(1 for t in trades if t[3])
            tot_pnl = sum(pnl(t[2], t[3]) for t in trades)
            avg_p = sum(t[2] for t in trades) / n
            poly_n = sum(1 for t in trades if t[0] == "poly")
            pred_n = n - poly_n
            print(f"{thr:<6.2f} {n:>4} {up_n:>8} {dn_n:>8} {100*wins/n:>5.1f}% {avg_p:>6.3f} {tot_pnl:>+8.2f} {100*tot_pnl/n:>+6.1f}% {100*poly_n/n:>6.1f}% {100*pred_n/n:>6.1f}%")

    # ===== ALSO: opportunity sizing =====
    print()
    print("=" * 90)
    print("OPPORTUNITY MAGNITUDE — how often is one platform notably cheaper at sec=60")
    print("=" * 90)
    print("(side picked by Poly+Predict consensus, price gap shown)")
    ref = 60
    n_consensus = 0
    big_gaps = []
    for ep in common_eps:
        if (ep, ref) not in poly_snaps or (ep, ref) not in pred_snaps:
            continue
        pua, pda = poly_snaps[(ep, ref)]
        yua, yda = pred_snaps[(ep, ref)]
        if pua >= 0.55 and yua >= 0.55:
            n_consensus += 1
            gap = abs(pua - yua)
            cheaper = "poly" if pua <= yua else "pred"
            big_gaps.append((gap, ep, "UP", pua, yua, cheaper))
        elif pda >= 0.55 and yda >= 0.55:
            n_consensus += 1
            gap = abs(pda - yda)
            cheaper = "poly" if pda <= yda else "pred"
            big_gaps.append((gap, ep, "DOWN", pda, yda, cheaper))
    big_gaps.sort(reverse=True)
    print(f"  Consensus windows at sec=60: {n_consensus}")
    print(f"  Top 15 widest gaps:")
    print(f"  {'gap':>5}  {'side':<5}  {'poly':>5}  {'pred':>5}  cheaper")
    for gap, ep, side, p, y, ch in big_gaps[:15]:
        print(f"  {gap:>5.3f}  {side:<5}  {p:>5.3f}  {y:>5.3f}  {ch}")

    gaps = [g[0] for g in big_gaps]
    if gaps:
        gaps_sorted = sorted(gaps)
        print(f"\n  Gap distribution:")
        print(f"    median: {gaps_sorted[len(gaps_sorted)//2]:.3f}")
        print(f"    p75:    {gaps_sorted[int(len(gaps_sorted)*0.75)]:.3f}")
        print(f"    p90:    {gaps_sorted[int(len(gaps_sorted)*0.9)]:.3f}")
        print(f"    max:    {gaps_sorted[-1]:.3f}")


if __name__ == "__main__":
    main()
