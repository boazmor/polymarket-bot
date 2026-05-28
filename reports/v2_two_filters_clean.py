#!/usr/bin/env python3
"""Clean test: just two filters ג€” consensus and distance. No hour filter."""
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


def pick(row, side):
    poly = row['poly']; pred = row['pred']
    pp = poly.get('up') if side == 'UP' else poly.get('down')
    yp = pred.get('up') if side == 'UP' else pred.get('down')
    if pp <= yp: return 'poly', pp
    return 'predict', yp


def dist_blocked(row):
    d = row['poly'].get('dist')
    return d is not None and 50 <= abs(d) <= 100


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
    print(f"  {name:<60} fires={fires:<4} win%={wr:5.1f}%  total ${pnl_sum:+7.2f}  per-trade ${per:+.3f}")


def main():
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'\nTotal windows in dataset: {len(windows)}\n')

    # Build all trades by FIRST applying consensus (since without consensus we have no side)
    # Then optionally apply distance filter
    all_consensus_trades = []
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        plat, price = pick(r, side)
        all_consensus_trades.append((r, side, plat, price))

    print('='*90)
    print('STEP 1 ג€” run with NO filters (require consensus to know the side, but no distance filter)')
    print('='*90)
    evaluate(all_consensus_trades, 'Consensus only ג€” all distances allowed')

    print()
    print('='*90)
    print('STEP 2 ג€” split the consensus trades by distance zone')
    print('='*90)
    bad_zone = [t for t in all_consensus_trades if dist_blocked(t[0])]
    good_zone = [t for t in all_consensus_trades if not dist_blocked(t[0])]
    evaluate(bad_zone,  'BLOCKED zone   |distance| 50-100')
    evaluate(good_zone, 'ALLOWED zone   |distance| <50 or >100')

    print()
    print('='*90)
    print('STEP 3 ג€” final: V2 without hour filter = consensus + distance filter')
    print('='*90)
    evaluate(good_zone, 'V2 (no hour) = consensus + distance filter')


if __name__ == '__main__':
    main()
