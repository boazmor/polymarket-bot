#!/usr/bin/env python3
"""Test the proposed V3 filter: V2 + require similar-target third platform agrees.
Try multiple entry seconds and multiple variants of the filter."""
import sys, csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta

THR = 0.60
INVEST = 2.0
SIMILAR_GAP = 5.0
ENTRY_SECS = [30, 45, 60, 90, 120, 150, 180, 210, 240]

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'
KAL     = '/root/data_kalshi_btc_15m/combined_per_second.csv'


def f(v):
    if v in (None, '', 'None'): return None
    try: return float(v)
    except: return None


def load_poly_outcomes():
    out = {}
    with open(POLYOUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_epoch'])
                if r.get('winner_side') in ('UP','DOWN'):
                    out[ep] = r['winner_side']
            except: pass
    return out


def load_pred_outcomes():
    last_bn = {}; strikes = {}
    with open(PRED) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_open_epoch'])
                bn = f(r.get('binance_now')); tg = f(r.get('strike'))
                if bn is not None: last_bn[ep] = bn
                if tg is not None: strikes[ep] = tg
            except: pass
    out = {}
    for ep, s in strikes.items():
        fin = last_bn.get(ep)
        if fin is None: continue
        out[ep] = 'UP' if fin > s else 'DOWN'
    return out


def load_per_sec(path, key_open, sec_col, up_col, down_col, target_col,
                 bn_col=None, derive_ep=None):
    out = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if derive_ep:
                pair = derive_ep(r)
                if pair is None: continue
                ep, sec = pair
            else:
                try: ep = int(r[key_open]); sec = int(r[sec_col])
                except: continue
            out[(ep, sec)] = {
                'up': f(r.get(up_col)),
                'down': f(r.get(down_col)),
                'target': f(r.get(target_col)),
                'binance': f(r.get(bn_col)) if bn_col else None,
            }
    return out


def load_kal_lookup():
    rows_by_es = defaultdict(list)
    with open(KAL) as fh:
        for r in csv.DictReader(fh):
            try:
                es = int(r['epoch_sec']); oe = int(r['open_epoch']); ce = int(r['close_epoch'])
            except: continue
            rows_by_es[es].append({
                'up': f(r.get('yes_ask')), 'down': f(r.get('no_ask')),
                'target': f(r.get('target_price')), 'binance': f(r.get('binance_now')),
                'oe': oe, 'ce': ce
            })

    def at(poly_ep, sec):
        target = poly_ep + sec
        for c in rows_by_es.get(target, []):
            if c['oe'] <= target <= c['ce']:
                return {'up': c['up'], 'down': c['down'],
                        'target': c['target'], 'binance': c['binance']}
        return None
    return at


def lim_market_map():
    m = {}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try:
                mid = r['market_id']; exp_ms = int(r['expirationTimestamp'])
                m[mid] = exp_ms // 1000 - 300
            except: pass
    return m


def derive_lim(m):
    def _d(r):
        mid = r.get('market_id'); ep = m.get(mid)
        if ep is None: return None
        try: es = int(r['epoch_sec'])
        except: return None
        sec = es - ep
        if sec < 0 or sec > 320: return None
        return (ep, sec)
    return _d


def has_consensus(poly, pred):
    if not poly or not pred: return None
    pu, pd = poly.get('up'), poly.get('down')
    yu, yd = pred.get('up'), pred.get('down')
    pu_ok = pu is not None and pu >= THR
    pd_ok = pd is not None and pd >= THR
    yu_ok = yu is not None and yu >= THR
    yd_ok = yd is not None and yd >= THR
    if pu_ok and yu_ok: return 'UP'
    if pd_ok and yd_ok: return 'DOWN'
    return None


def cheap_pick(poly, pred, side):
    pp = poly.get('up') if side=='UP' else poly.get('down')
    yp = pred.get('up') if side=='UP' else pred.get('down')
    if pp is None and yp is None: return None
    if pp is None: return ('predict', yp)
    if yp is None: return ('poly', pp)
    return ('poly', pp) if pp <= yp else ('predict', yp)


def vote_of(snap):
    if not snap: return None
    u = snap.get('up'); d = snap.get('down')
    if u is None and d is None: return None
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
    return 'silent'


def is_similar_and_agrees(third_snap, poly_target, pred_target, side):
    if not third_snap: return False
    tg = third_snap.get('target')
    if tg is None or poly_target is None or pred_target is None: return False
    if abs(tg - (poly_target + pred_target)/2) >= SIMILAR_GAP: return False
    return vote_of(third_snap) == side


def measure(trades, poly_outs, pred_outs):
    fires = wins = losses = 0
    pnl_sum = 0.0
    for row, side, plat, price in trades:
        outcome = poly_outs.get(row['ep']) if plat == 'poly' else pred_outs.get(row['ep'])
        fires += 1
        if outcome is None: continue
        if outcome == side:
            pnl = INVEST/price - INVEST; wins += 1
        else:
            pnl = -INVEST; losses += 1
        pnl_sum += pnl
    res = wins + losses
    wr = (100*wins/res) if res else 0
    per = (pnl_sum/res) if res else 0
    return {'fires': fires, 'wins': wins, 'losses': losses,
            'res': res, 'wr': wr, 'pnl': pnl_sum, 'per': per}


def fmt(s, label):
    return f"  {label:<48} fires={s['fires']:<4} win%={s['wr']:5.1f}%  PnL=${s['pnl']:+8.2f}  per-trade ${s['per']:+.3f}"


def main():
    print('Loading data ...')
    poly_outs = load_poly_outcomes()
    pred_outs = load_pred_outcomes()
    poly_snaps = load_per_sec(POLY,'market_epoch','sec_from_start',
                              'up_ask','down_ask','target_price','binance_price')
    pred_snaps = load_per_sec(PRED,'market_open_epoch','sec_from_open',
                              'yes_ask','no_ask_implied','strike','binance_now')
    lim_snaps  = load_per_sec(LIM,None,None,
                              'best_ask','no_best_ask','target_price','binance_now',
                              derive_ep=derive_lim(lim_market_map()))
    kal_at = load_kal_lookup()
    print(f'  poly outs={len(poly_outs)}  pred outs={len(pred_outs)}')

    print()
    print('='*95)
    print(f'V3 filter: V2 + require third-platform (target gap <${SIMILAR_GAP}, vote=V2 side)')
    print('Tested at each entry sec. Total $ and per-trade $.')
    print('='*95)

    for sec in ENTRY_SECS:
        eps = set(p[0] for p in poly_snaps.keys() if p[1] == sec)
        v2 = []
        for ep in eps:
            poly = poly_snaps.get((ep, sec))
            pred = pred_snaps.get((ep, sec))
            if not poly or not pred: continue
            side = has_consensus(poly, pred)
            if not side: continue
            d_t = poly.get('target'); bn = poly.get('binance')
            dist = (bn - d_t) if (d_t is not None and bn is not None) else None
            if dist is not None and 50 <= abs(dist) <= 100: continue
            pk = cheap_pick(poly, pred, side)
            if not pk: continue
            plat, price = pk
            row = {'ep': ep, 'poly': poly, 'pred': pred,
                   'lim': lim_snaps.get((ep, sec)),
                   'kal': kal_at(ep, sec)}
            v2.append((row, side, plat, price))

        # variants
        def with_filter(filter_fn):
            return [t for t in v2 if filter_fn(t[0], t[1])]

        def lim_ok(row, side):
            return is_similar_and_agrees(row.get('lim'), row['poly'].get('target'),
                                          row['pred'].get('target'), side)

        def kal_ok(row, side):
            return is_similar_and_agrees(row.get('kal'), row['poly'].get('target'),
                                          row['pred'].get('target'), side)

        v3_lim_only = with_filter(lim_ok)
        v3_kal_only = with_filter(kal_ok)
        v3_either   = with_filter(lambda r,s: lim_ok(r,s) or kal_ok(r,s))
        v3_both     = with_filter(lambda r,s: lim_ok(r,s) and kal_ok(r,s))

        print()
        print(f'--- entry sec={sec} ---')
        print(fmt(measure(v2,         poly_outs, pred_outs), 'V2 baseline'))
        print(fmt(measure(v3_lim_only, poly_outs, pred_outs), 'V3 + Lim sim+agree REQUIRED'))
        print(fmt(measure(v3_kal_only, poly_outs, pred_outs), 'V3 + Kal sim+agree REQUIRED'))
        print(fmt(measure(v3_either,   poly_outs, pred_outs), 'V3 + (Lim OR Kal) sim+agree'))
        print(fmt(measure(v3_both,     poly_outs, pred_outs), 'V3 + (Lim AND Kal) sim+agree'))


if __name__ == '__main__':
    main()
