#!/usr/bin/env python3
"""Test how filter ORDER affects each filter's apparent effectiveness.
For each filter, isolate the rows it would block, then check win-rate of those blocked rows
under different combinations of the OTHER filters."""
import sys
sys.path.insert(0, '/root/reports')
from backtest_v1_historical import (
    load_poly_outcomes, build_poly_snapshots, build_predict_snapshots,
    build_lim_snapshots, build_windows, pnl, vote_classify, decide_row, THR
)
from collections import defaultdict
from datetime import datetime, timezone, timedelta


BAD_HOURS = {3, 9, 11, 14}


def consensus_pick(row):
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
    poly_price = pu if side == 'UP' else pd
    pred_price = yu if side == 'UP' else yd
    if poly_price <= pred_price: plat, price = 'poly', poly_price
    else: plat, price = 'predict', pred_price
    return side, plat, price


def loose_pick(row):
    d = decide_row(row, THR, 2, False)
    if not d: return None
    side, plat, price, _, _ = d
    return side, plat, price


def nyc_hr(row):
    return (datetime.fromtimestamp(row['ep'], tz=timezone.utc) - timedelta(hours=4)).hour


def summarize(label, picks):
    fires = wins = losses = 0
    pnl_sum = 0.0
    for side, plat, price, r in picks:
        fires += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: wins += 1
        else: losses += 1
        pnl_sum += p
    res = wins + losses
    wr = (100*wins/res) if res else 0
    per = (pnl_sum/res) if res else 0
    print(f"  {label:<55} fires={fires:<4} win%={wr:5.1f}%  PnL=${pnl_sum:+7.2f}  ${per:+.3f}/trade")


def main():
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'\nTotal windows: {len(windows)}\n')

    # =========================================================================
    print('='*88)
    print('TEST 1 ג€” "BAD HOURS" {3,9,11,14} ג€” what happens to them under each prior filter')
    print('='*88)
    # 1a: Loose baseline (any 2 of 3 platforms agree)
    picks = []
    for r in windows:
        if nyc_hr(r) not in BAD_HOURS: continue
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('1a bad-hours, LOOSE baseline (≥2/3 incl. Limitless)', picks)

    # 1b: + require Poly+Pred consensus
    picks = []
    for r in windows:
        if nyc_hr(r) not in BAD_HOURS: continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('1b bad-hours + require Poly+Pred consensus', picks)

    # 1c: + consensus + distance filter
    picks = []
    for r in windows:
        if nyc_hr(r) not in BAD_HOURS: continue
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('1c bad-hours + consensus + distance filter (V2 if no hr filter)', picks)

    # =========================================================================
    print()
    print('='*88)
    print('TEST 2 ג€” "BAD DISTANCE" 50≤|d|≤100 ג€” what happens to it under each prior filter')
    print('='*88)
    # 2a: Loose baseline
    picks = []
    for r in windows:
        d = r['poly'].get('dist')
        if d is None or not (50 <= abs(d) <= 100): continue
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('2a bad-dist, LOOSE baseline', picks)

    # 2b: + Poly+Pred consensus
    picks = []
    for r in windows:
        d = r['poly'].get('dist')
        if d is None or not (50 <= abs(d) <= 100): continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('2b bad-dist + require Poly+Pred consensus', picks)

    # 2c: + consensus + skip BAD_HOURS
    picks = []
    for r in windows:
        d = r['poly'].get('dist')
        if d is None or not (50 <= abs(d) <= 100): continue
        if nyc_hr(r) in BAD_HOURS: continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('2c bad-dist + consensus + hour filter', picks)

    # =========================================================================
    print()
    print('='*88)
    print('TEST 3 ג€” "NON-CONSENSUS" (Poly and Pred disagree or silent) ג€” loose pick alone')
    print('='*88)
    # 3a: rows where consensus FAILS but loose ≥2/3 picks something
    picks = []
    for r in windows:
        c = consensus_pick(r)
        if c is not None: continue  # only rows blocked by consensus
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('3a no-poly-pred-consensus, loose ≥2/3 still fires', picks)

    # 3b: those same rows, with distance filter
    picks = []
    for r in windows:
        c = consensus_pick(r)
        if c is not None: continue
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('3b same + distance filter applied', picks)

    # 3c: those same rows, with distance + hour filter
    picks = []
    for r in windows:
        c = consensus_pick(r)
        if c is not None: continue
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        if nyc_hr(r) in BAD_HOURS: continue
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('3c same + distance + hour filter', picks)

    # =========================================================================
    print()
    print('='*88)
    print('REFERENCE ג€” the actual ALLOWED set under each combination')
    print('='*88)
    # ref A: V1 loose baseline (all 24 hr, all dist, ≥2/3)
    picks = []
    for r in windows:
        p = loose_pick(r)
        if p: picks.append((*p, r))
    summarize('refA  V1 loose (≥2/3 incl. Lim, no dist, no hr filter)', picks)

    # ref B: Poly+Pred consensus only
    picks = []
    for r in windows:
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('refB  Poly+Pred consensus ONLY (no dist, no hr)', picks)

    # ref C: consensus + distance
    picks = []
    for r in windows:
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('refC  consensus + distance (no hr) ג€” drops hour filter', picks)

    # ref D: V2 full
    picks = []
    for r in windows:
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        if nyc_hr(r) in BAD_HOURS: continue
        p = consensus_pick(r)
        if p: picks.append((*p, r))
    summarize('refD  V2 FULL (consensus + dist + hour)', picks)


if __name__ == '__main__':
    main()
