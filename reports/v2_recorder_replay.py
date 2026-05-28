#!/usr/bin/env python3
"""Replay V2 logic on the LAST 6h 12min of recorder data and report parallel stats to the live bot."""
import sys, csv, statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/root/reports')
from backtest_v1_historical import (
    load_poly_outcomes, build_poly_snapshots, build_predict_snapshots,
    build_lim_snapshots, build_windows, THR
)

WINDOW_SEC = 6 * 3600 + 12 * 60
INVEST = 2.0


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


def main():
    print('Loading recorder data ...', flush=True)
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'  total recorder windows: {len(windows)}')

    if not windows:
        print('NO WINDOWS')
        return

    latest_ep = max(w['ep'] for w in windows)
    cutoff_ep = latest_ep - WINDOW_SEC
    recent = [w for w in windows if w['ep'] >= cutoff_ep]

    first_ts = datetime.fromtimestamp(min(w['ep'] for w in recent), tz=timezone.utc)
    last_ts = datetime.fromtimestamp(max(w['ep'] for w in recent), tz=timezone.utc)
    span_sec = max(w['ep'] for w in recent) - min(w['ep'] for w in recent)
    print(f'  windows in last 6h12m: {len(recent)}')
    print(f'  range: {first_ts.isoformat()} -> {last_ts.isoformat()}')
    print(f'  span: {span_sec/3600:.2f}h')
    print()

    # walk each window through V2 logic
    consensus_count = 0
    consensus_then_dist_blocked = 0
    fired = 0
    reasons = {'no_consensus': 0, 'dist_blocked': 0, 'FIRED': 0}
    trades = []  # (ep, side, plat, price, outcome)
    nyc_fired = []
    formerly_bad_seen = 0
    formerly_bad_fires = 0
    bad_set = {3, 9, 11, 14}

    for r in recent:
        nyc = (datetime.fromtimestamp(r['ep'], tz=timezone.utc) - timedelta(hours=4)).hour
        if nyc in bad_set:
            formerly_bad_seen += 1
        side = has_consensus(r)
        if not side:
            reasons['no_consensus'] += 1
            continue
        consensus_count += 1
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100:
            reasons['dist_blocked'] += 1
            consensus_then_dist_blocked += 1
            continue
        plat, price = cheap_pick(r, side)
        outcome = r.get(f'{plat}_out')
        if outcome is None: continue
        reasons['FIRED'] += 1
        fired += 1
        nyc_fired.append(nyc)
        if nyc in bad_set: formerly_bad_fires += 1
        trades.append((r['ep'], side, plat, price, outcome))

    wins = losses = 0
    total_pnl = 0.0
    win_pnl = 0.0
    loss_pnl = 0.0
    sides = {'UP': 0, 'DOWN': 0}
    prices = []
    for ep, side, plat, price, outcome in trades:
        sides[side] += 1
        prices.append(price)
        if outcome == side:
            pnl = INVEST/price - INVEST
            wins += 1; win_pnl += pnl
        else:
            pnl = -INVEST
            losses += 1; loss_pnl += pnl
        total_pnl += pnl

    print('=== V2 REPLAY on recorder data, last 6h 12min ===')
    print(f'decisions evaluated:       {len(recent)}')
    for k in ('no_consensus','dist_blocked','FIRED'):
        v = reasons[k]; pct = (100*v/len(recent)) if recent else 0
        print(f'  {k:18s}  {v:4d}  ({pct:.1f}%)')
    print()
    print(f'trades fired:              {fired}')
    print(f'  side mix:                UP={sides["UP"]} DOWN={sides["DOWN"]}')
    if prices:
        print(f'  avg price per share:     {sum(prices)/len(prices):.3f}')
    res = wins + losses
    print(f'resolved:                  {res}')
    print(f'wins:                      {wins}')
    print(f'losses:                    {losses}')
    if res:
        print(f'win rate:                  {100*wins/res:.1f}%')
    print(f'total PnL:                 ${total_pnl:+.2f}  (winners +${win_pnl:.2f}  losers -${abs(loss_pnl):.2f})')
    if res:
        print(f'per trade:                 ${total_pnl/res:+.3f}')
    print()
    print(f'formerly-blocked-hour windows seen: {formerly_bad_seen}')
    print(f'formerly-blocked-hour fires:        {formerly_bad_fires}')
    print()
    print('=== fires by NYC hour ===')
    from collections import Counter
    c = Counter(nyc_fired)
    for h in sorted(c.keys()):
        flag = ' (was BLOCKED)' if h in bad_set else ''
        print(f'  hour {h:2d}: {c[h]} fires{flag}')


if __name__ == '__main__':
    main()
