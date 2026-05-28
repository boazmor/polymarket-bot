#!/usr/bin/env python3
"""V3 variant: scan ALL 300 seconds of each window, fire on FIRST moment when
the full rule is satisfied: V2 (consensus + distance) AND a third platform has
similar target AND agrees with V2 side.

This more closely matches how a real bot would operate — watch continuously
and act when the rare condition appears.
"""
import sys, csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta

THR = 0.60
INVEST = 2.0
SIMILAR_GAP = 5.0
ENTER_MIN_SEC = 30   # earliest entry
ENTER_MAX_SEC = 270  # latest entry

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


def load_per_sec(path, key_open, sec_col, up_col, down_col, target_col, bn_col=None,
                 derive_ep=None):
    out = defaultdict(dict)
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if derive_ep:
                pair = derive_ep(r)
                if pair is None: continue
                ep, sec = pair
            else:
                try: ep = int(r[key_open]); sec = int(r[sec_col])
                except: continue
            out[ep][sec] = {
                'up': f(r.get(up_col)),
                'down': f(r.get(down_col)),
                'target': f(r.get(target_col)),
                'binance': f(r.get(bn_col)) if bn_col else None,
            }
    return out


def load_kal_by_ep():
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
    return rows_by_es


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


def kal_at_sec(rows_by_es, poly_ep, sec):
    target = poly_ep + sec
    for c in rows_by_es.get(target, []):
        if c['oe'] <= target <= c['ce']:
            return {'up': c['up'], 'down': c['down'],
                    'target': c['target'], 'binance': c['binance']}
    return None


def consensus_at(poly_snap, pred_snap):
    if not poly_snap or not pred_snap: return None
    pu, pd = poly_snap.get('up'), poly_snap.get('down')
    yu, yd = pred_snap.get('up'), pred_snap.get('down')
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
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
    return None


def is_sim_agree(snap, poly_t, pred_t, side):
    if not snap or vote_of(snap) != side: return False
    tg = snap.get('target')
    if tg is None or poly_t is None or pred_t is None: return False
    return abs(tg - (poly_t + pred_t)/2) < SIMILAR_GAP


def main():
    print('Loading data ...')
    poly_outs = load_poly_outcomes()
    pred_outs = load_pred_outcomes()
    poly_by_ep = load_per_sec(POLY,'market_epoch','sec_from_start',
                              'up_ask','down_ask','target_price','binance_price')
    pred_by_ep = load_per_sec(PRED,'market_open_epoch','sec_from_open',
                              'yes_ask','no_ask_implied','strike','binance_now')
    lim_by_ep  = load_per_sec(LIM,None,None,
                              'best_ask','no_best_ask','target_price','binance_now',
                              derive_ep=derive_lim(lim_market_map()))
    kal_rows = load_kal_by_ep()
    print(f'  poly outs={len(poly_outs)}  pred outs={len(pred_outs)}')
    print(f'  windows in data: poly={len(poly_by_ep)} pred={len(pred_by_ep)} '
          f'lim={len(lim_by_ep)}')

    # For each window epoch, scan from sec ENTER_MIN_SEC to ENTER_MAX_SEC,
    # FIRST sec when V2 consensus+distance fires AND third sim+agree.
    eps = set(poly_by_ep.keys()) & set(pred_by_ep.keys())
    print(f'  candidate windows (have both poly+pred): {len(eps)}')

    v2_trades = []
    v3_lim_trades = []
    v3_kal_trades = []
    v3_either_trades = []
    v3_both_trades = []

    for ep in eps:
        poly_secs = poly_by_ep[ep]; pred_secs = pred_by_ep[ep]
        lim_secs  = lim_by_ep.get(ep, {})

        v2_fired = False
        v3_lim_fired = False
        v3_kal_fired = False
        v3_either_fired = False
        v3_both_fired = False

        for sec in range(ENTER_MIN_SEC, ENTER_MAX_SEC + 1):
            poly = poly_secs.get(sec); pred = pred_secs.get(sec)
            if not poly or not pred: continue
            side = consensus_at(poly, pred)
            if not side: continue
            d_t = poly.get('target'); bn = poly.get('binance')
            dist = (bn - d_t) if (d_t is not None and bn is not None) else None
            if dist is not None and 50 <= abs(dist) <= 100: continue
            pk = cheap_pick(poly, pred, side)
            if not pk: continue
            plat, price = pk
            row = {'ep': ep}

            # V2 baseline first match
            if not v2_fired:
                v2_trades.append((row, side, plat, price))
                v2_fired = True

            lim_snap = lim_secs.get(sec)
            kal_snap = kal_at_sec(kal_rows, ep, sec)
            poly_t = poly.get('target'); pred_t = pred.get('target')
            lim_ok = is_sim_agree(lim_snap, poly_t, pred_t, side)
            kal_ok = is_sim_agree(kal_snap, poly_t, pred_t, side)

            if lim_ok and not v3_lim_fired:
                v3_lim_trades.append((row, side, plat, price))
                v3_lim_fired = True
            if kal_ok and not v3_kal_fired:
                v3_kal_trades.append((row, side, plat, price))
                v3_kal_fired = True
            if (lim_ok or kal_ok) and not v3_either_fired:
                v3_either_trades.append((row, side, plat, price))
                v3_either_fired = True
            if (lim_ok and kal_ok) and not v3_both_fired:
                v3_both_trades.append((row, side, plat, price))
                v3_both_fired = True

    def measure(trades, label):
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
        print(f"  {label:<50} fires={fires:<4} win%={wr:5.1f}%  PnL=${pnl_sum:+8.2f}  per-trade ${per:+.3f}")

    print()
    print('='*95)
    print(f'V3 FIRST-MATCH SCAN — for each window, fire on the FIRST sec ({ENTER_MIN_SEC}-{ENTER_MAX_SEC})')
    print(f'where V2 fires AND third platform similar(<${SIMILAR_GAP}) + agrees.')
    print('='*95)
    measure(v2_trades, 'V2 baseline (first consensus+dist match)')
    measure(v3_lim_trades, 'V3 + Lim sim+agree REQUIRED')
    measure(v3_kal_trades, 'V3 + Kal sim+agree REQUIRED')
    measure(v3_either_trades, 'V3 + (Lim OR Kal) sim+agree')
    measure(v3_both_trades, 'V3 + (Lim AND Kal) sim+agree')


if __name__ == '__main__':
    main()
