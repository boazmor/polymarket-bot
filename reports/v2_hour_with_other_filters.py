#!/usr/bin/env python3
"""Tests user's question: if we apply the OTHER V2 filters (Poly+Pred consensus + distance filter)
to hours 3/9/11/14, do those hours still look bad? Or did they only look bad in the loose baseline?"""
import sys
sys.path.insert(0, '/root/reports')
from backtest_v1_historical import (
    load_poly_outcomes, build_poly_snapshots, build_predict_snapshots,
    build_lim_snapshots, build_windows, pnl, THR, INVEST
)
from collections import defaultdict
from datetime import datetime, timezone, timedelta


def decide_v2(row, apply_hour_filter):
    poly = row['poly']; pred = row['pred']
    pu, pd = poly.get('up'), poly.get('down')
    yu, yd = pred.get('up'), pred.get('down')
    poly_up = pu is not None and pu >= THR
    poly_dn = pd is not None and pd >= THR
    pred_up = yu is not None and yu >= THR
    pred_dn = yd is not None and yd >= THR
    if poly_up and pred_up: side = 'UP'
    elif poly_dn and pred_dn: side = 'DOWN'
    else: return None
    dist = poly.get('dist')
    if dist is not None and 50 <= abs(dist) <= 100:
        return None
    nyc_hr = (datetime.fromtimestamp(row['ep'], tz=timezone.utc) - timedelta(hours=4)).hour
    if apply_hour_filter and nyc_hr in {3, 9, 11, 14}:
        return None
    poly_price = pu if side == 'UP' else pd
    pred_price = yu if side == 'UP' else yd
    if poly_price <= pred_price: plat, price = 'poly', poly_price
    else: plat, price = 'predict', pred_price
    return side, plat, price, nyc_hr, dist


def main():
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'\nTotal windows: {len(windows)}\n')

    by_hour_v2_no_hourfilter = defaultdict(lambda: {'fires':0,'wins':0,'losses':0,'pnl':0.0})
    bad_hours = {3, 9, 11, 14}
    for r in windows:
        d = decide_v2(r, apply_hour_filter=False)
        if not d: continue
        side, plat, price, nyc_hr, dist = d
        b = by_hour_v2_no_hourfilter[nyc_hr]
        b['fires'] += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: b['wins'] += 1
        else: b['losses'] += 1
        b['pnl'] += p

    print('='*78)
    print('PER-HOUR results AFTER applying Poly+Pred consensus + distance filter')
    print('(no hour filter ג€” shows whether bad hours are still bad)')
    print('='*78)
    print(f"  {'hr_NYC':<7} {'flag':<5} {'fires':<6} {'wins':<5} {'lose':<5} {'win%':<7} {'PnL$':<9}")
    tot_bad = {'fires':0,'wins':0,'losses':0,'pnl':0.0}
    tot_good = {'fires':0,'wins':0,'losses':0,'pnl':0.0}
    for h in range(24):
        b = by_hour_v2_no_hourfilter.get(h, {'fires':0,'wins':0,'losses':0,'pnl':0.0})
        res = b['wins'] + b['losses']
        wr = (100*b['wins']/res) if res else 0
        flag = 'BAD' if h in bad_hours else ''
        print(f"  {h:<7} {flag:<5} {b['fires']:<6} {b['wins']:<5} {b['losses']:<5} {wr:<6.1f}% {b['pnl']:+.2f}")
        tgt = tot_bad if h in bad_hours else tot_good
        for k in ('fires','wins','losses','pnl'): tgt[k] += b[k]

    print()
    print('='*78)
    print('AGGREGATE comparison (V2 with consensus+distance, no hour filter)')
    print('='*78)
    for label, t in [('BAD hours {3,9,11,14}', tot_bad), ('OTHER 20 hours', tot_good)]:
        res = t['wins'] + t['losses']
        wr = (100*t['wins']/res) if res else 0
        per_trade = (t['pnl']/res) if res else 0
        print(f"  {label:<25} fires={t['fires']} wins={t['wins']} losses={t['losses']} "
              f"win%={wr:.1f}% PnL=${t['pnl']:+.2f} ${per_trade:+.3f}/trade")


if __name__ == '__main__':
    main()
