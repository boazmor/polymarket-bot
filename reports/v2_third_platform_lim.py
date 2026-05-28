#!/usr/bin/env python3
"""Stage 1: take V2 (consensus + distance), split each trade by what Limitless said.
Does requiring Lim agreement improve PnL? What about blocking on Lim dissent?"""
import sys
sys.path.insert(0, '/root/reports')
from backtest_v1_historical import (
    load_poly_outcomes, build_poly_snapshots, build_predict_snapshots,
    build_lim_snapshots, build_windows, pnl, THR
)


def has_consensus(row):
    poly = row['poly']; pred = row['pred']
    pu, pd = poly.get('up'), poly.get('down')
    yu, yd = pred.get('up'), pred.get('down')
    pu_ok = pu is not None and pu >= THR
    pd_ok = pd is not None and pd >= THR
    yu_ok = yu is not None and yu >= THR
    yd_ok = yd is not None and yd >= THR
    if pu_ok and yu_ok: return 'UP'
    if pd_ok and yd_ok: return 'DOWN'
    return None


def cheap_pick(row, side):
    pp = row['poly'].get('up') if side == 'UP' else row['poly'].get('down')
    yp = row['pred'].get('up') if side == 'UP' else row['pred'].get('down')
    return ('poly', pp) if pp <= yp else ('predict', yp)


def lim_vote(row):
    lim = row.get('lim') or {}
    yu = lim.get('up'); yd = lim.get('down')
    if yu is None and yd is None: return 'no_data'
    yu_ok = yu is not None and yu >= THR
    yd_ok = yd is not None and yd >= THR
    if yu_ok and not yd_ok: return 'UP'
    if yd_ok and not yu_ok: return 'DOWN'
    return 'silent'


def evaluate(rows, name):
    fires = wins = losses = 0
    pnl_sum = 0.0
    for r, side, plat, price in rows:
        fires += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: wins += 1
        else: losses += 1
        pnl_sum += p
    res = wins + losses
    wr = (100*wins/res) if res else 0
    per = (pnl_sum/res) if res else 0
    print(f"  {name:<48} fires={fires:<4} win%={wr:5.1f}%  total ${pnl_sum:+7.2f}  per-trade ${per:+.3f}")
    return fires, wins, losses, pnl_sum


def main():
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'\nTotal windows: {len(windows)}\n')

    # build V2 trades (consensus + distance, no hour)
    v2_trades = []
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        plat, price = cheap_pick(r, side)
        v2_trades.append((r, side, plat, price))

    print('='*90)
    print('BASELINE V2 ג€” consensus + distance, no hour filter')
    print('='*90)
    evaluate(v2_trades, 'V2 baseline')

    print()
    print('='*90)
    print('Stage 1: split V2 trades by what LIMITLESS said')
    print('='*90)
    agree    = [t for t in v2_trades if lim_vote(t[0]) == t[1]]
    disagree = [t for t in v2_trades if lim_vote(t[0]) in ('UP','DOWN') and lim_vote(t[0]) != t[1]]
    silent   = [t for t in v2_trades if lim_vote(t[0]) == 'silent']
    nodata   = [t for t in v2_trades if lim_vote(t[0]) == 'no_data']

    evaluate(agree,    'Lim AGREES with V2')
    evaluate(disagree, 'Lim DISAGREES with V2')
    evaluate(silent,   'Lim SILENT (both < 0.60)')
    evaluate(nodata,   'Lim NO DATA at sec 90')

    print()
    print('='*90)
    print('What-if rules layered on V2:')
    print('='*90)
    # Rule A: REQUIRE lim agreement
    a = agree
    evaluate(a, 'A. REQUIRE Lim agree (block silent/dissent/no-data)')
    # Rule B: BLOCK lim dissent only (keep agree/silent/no-data)
    b = agree + silent + nodata
    evaluate(b, 'B. BLOCK Lim dissent only (keep silent/no-data)')
    # Rule C: BLOCK lim dissent + silent (keep agree + no-data)
    c = agree + nodata
    evaluate(c, 'C. BLOCK dissent + silent (keep agree + no-data)')


if __name__ == '__main__':
    main()
