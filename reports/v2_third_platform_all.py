#!/usr/bin/env python3
"""Stages 2-4: split V2 trades by Gemini, by Kalshi, then combine all three."""
import sys, csv, statistics
from collections import defaultdict
sys.path.insert(0, '/root/reports')
from backtest_v1_historical import (
    load_poly_outcomes, build_poly_snapshots, build_predict_snapshots,
    build_lim_snapshots, build_windows, pnl, THR
)

GEM = '/root/data_gemini_btc_5m/combined_per_second.csv'
KAL = '/root/data_kalshi_btc_15m/combined_per_second.csv'


def f(v):
    if v in (None, '', 'None'): return None
    try: return float(v)
    except: return None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def build_gem_snapshots():
    bins = [30, 60, 90, 120, 180, 240]
    by_ep = defaultdict(lambda: defaultdict(lambda: {'up':[], 'down':[]}))
    with open(GEM) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_open_epoch']); sec = int(r['sec_from_open'])
            except: continue
            for b in bins:
                if abs(sec-b) <= 10:
                    bk = by_ep[ep][b]
                    v = f(r.get('best_ask'))
                    if v: bk['up'].append(v)
                    v = f(r.get('no_best_ask'))
                    if v: bk['down'].append(v)
    out = {}
    for ep, bins_d in by_ep.items():
        for b, vals in bins_d.items():
            out[(ep, b)] = {'up': median(vals['up']), 'down': median(vals['down'])}
    return out


def build_kal_snapshots():
    rows_by_kal_ep = defaultdict(list)
    with open(KAL) as fh:
        for r in csv.DictReader(fh):
            try:
                oe = int(r['open_epoch']); ce = int(r['close_epoch']); es = int(r['epoch_sec'])
            except: continue
            ya = f(r.get('yes_ask')); na = f(r.get('no_ask'))
            if ya is None and na is None: continue
            rows_by_kal_ep[oe].append((es, ya, na, ce))
    return rows_by_kal_ep


def kal_snapshot_at(rows_by_kal_ep, poly_ep):
    target_sec = poly_ep + 90
    matches_ya = []; matches_na = []
    for kal_open, rows in rows_by_kal_ep.items():
        for es, ya, na, ce in rows:
            if kal_open <= target_sec <= ce and (poly_ep+80) <= es <= (poly_ep+100):
                if ya is not None: matches_ya.append(ya)
                if na is not None: matches_na.append(na)
    return {'up': median(matches_ya), 'down': median(matches_na)}


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


def vote_for(snap):
    if not snap: return 'no_data'
    u = snap.get('up'); d = snap.get('down')
    if u is None and d is None: return 'no_data'
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
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
    print(f"  {name:<55} fires={fires:<4} win%={wr:5.1f}%  total ${pnl_sum:+7.2f}  per-trade ${per:+.3f}")


def split_and_show(v2_trades, vote_fn, label):
    agree    = [t for t in v2_trades if vote_fn(t[0]) == t[1]]
    disagree = [t for t in v2_trades if vote_fn(t[0]) in ('UP','DOWN') and vote_fn(t[0]) != t[1]]
    silent   = [t for t in v2_trades if vote_fn(t[0]) == 'silent']
    nodata   = [t for t in v2_trades if vote_fn(t[0]) == 'no_data']
    print(f'  -- split by {label} --')
    evaluate(agree,    f'{label} AGREES with V2')
    evaluate(disagree, f'{label} DISAGREES with V2')
    evaluate(silent,   f'{label} SILENT')
    evaluate(nodata,   f'{label} NO DATA')
    print(f'  -- rules layered on V2 --')
    evaluate(agree,                 f'REQUIRE {label} agree')
    evaluate(agree + silent + nodata, f'BLOCK only {label} dissent')
    evaluate(agree + nodata,        f'BLOCK dissent + silent (keep no-data)')


def main():
    print('Loading poly/pred/lim ...')
    poly_outs = load_poly_outcomes()
    poly_snaps = build_poly_snapshots()
    pred_snaps, pred_outs = build_predict_snapshots()
    lim_snaps, lim_outs = build_lim_snapshots()
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    print(f'  windows: {len(windows)}')
    print('Loading gemini ...')
    gem_snaps = build_gem_snapshots()
    print(f'  gem bins: {len(gem_snaps)}')
    print('Loading kalshi ...')
    kal_rows = build_kal_snapshots()
    print(f'  kal windows: {len(kal_rows)}')

    for r in windows:
        ep = r['ep']
        r['gem'] = gem_snaps.get((ep, 90)) or {}
        r['kal'] = kal_snapshot_at(kal_rows, ep)

    v2_trades = []
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        d = r['poly'].get('dist')
        if d is not None and 50 <= abs(d) <= 100: continue
        plat, price = cheap_pick(r, side)
        v2_trades.append((r, side, plat, price))

    print()
    print('='*95)
    print('V2 BASELINE')
    print('='*95)
    evaluate(v2_trades, 'V2 (consensus+distance, no hr)')

    # Restrict to windows where the third platform actually has data, so the comparison is fair
    gem_has_data = [t for t in v2_trades if vote_for(t[0].get('gem')) != 'no_data']
    kal_has_data = [t for t in v2_trades if vote_for(t[0].get('kal')) != 'no_data']
    print()
    print(f'GEM data available for {len(gem_has_data)}/{len(v2_trades)} V2 trades')
    print(f'KAL data available for {len(kal_has_data)}/{len(v2_trades)} V2 trades')

    print()
    print('='*95)
    print('STAGE 2 - GEMINI (only on windows where gem has data)')
    print('='*95)
    evaluate(gem_has_data, 'V2 restricted to GEM-covered windows')
    split_and_show(gem_has_data, lambda r: vote_for(r.get('gem')), 'GEM')

    print()
    print('='*95)
    print('STAGE 3 - KALSHI (only on windows where kal has data)')
    print('='*95)
    evaluate(kal_has_data, 'V2 restricted to KAL-covered windows')
    split_and_show(kal_has_data, lambda r: vote_for(r.get('kal')), 'KAL')

    print()
    print('='*95)
    print('STAGE 4 - COMBINE all three on V2 trades that have BOTH gem+kal data')
    print('='*95)
    both = [t for t in v2_trades
            if vote_for(t[0].get('gem')) != 'no_data'
            and vote_for(t[0].get('kal')) != 'no_data']
    print(f'V2 trades with BOTH gem+kal data: {len(both)}')
    evaluate(both, 'V2 baseline within this overlap')

    lv = lambda r: vote_for({'up':(r.get('lim') or {}).get('up'),
                              'down':(r.get('lim') or {}).get('down')})
    gv = lambda r: vote_for(r.get('gem'))
    kv = lambda r: vote_for(r.get('kal'))

    rule = [t for t in both if gv(t[0]) == t[1] and kv(t[0]) == t[1]]
    evaluate(rule, 'GEM AND KAL both agree')

    rule = [t for t in both if lv(t[0]) == t[1] and gv(t[0]) == t[1] and kv(t[0]) == t[1]]
    evaluate(rule, 'LIM AND GEM AND KAL all agree')

    rule = [t for t in both if gv(t[0]) == t[1] or kv(t[0]) == t[1]]
    evaluate(rule, 'GEM OR KAL agree')

    opp = lambda s: 'DOWN' if s == 'UP' else 'UP'
    rule = [t for t in both
            if lv(t[0]) != opp(t[1])
            and gv(t[0]) != opp(t[1])
            and kv(t[0]) != opp(t[1])]
    evaluate(rule, 'BLOCK if ANY of L/G/K dissents (silent OK)')


if __name__ == '__main__':
    main()
