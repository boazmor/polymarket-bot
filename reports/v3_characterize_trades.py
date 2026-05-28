#!/usr/bin/env python3
"""Dump the 24 winning V3 candidate trades and find shared characteristics."""
import sys, csv
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

THR = 0.60
INVEST = 2.0
SIMILAR_GAP = 5.0
ENTER_MIN_SEC = 30
ENTER_MAX_SEC = 270

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'
KAL     = '/root/data_kalshi_btc_15m/combined_per_second.csv'


def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None


def load_poly_outcomes():
    out = {}
    with open(POLYOUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_epoch'])
                if r.get('winner_side') in ('UP','DOWN'): out[ep] = r['winner_side']
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
                'up': f(r.get(up_col)), 'down': f(r.get(down_col)),
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


def consensus_at(poly, pred):
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

    eps = set(poly_by_ep.keys()) & set(pred_by_ep.keys())
    trades = []
    for ep in eps:
        poly_secs = poly_by_ep[ep]; pred_secs = pred_by_ep[ep]
        lim_secs  = lim_by_ep.get(ep, {})
        fired = False
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
            poly_t = poly.get('target'); pred_t = pred.get('target')
            lim_snap = lim_secs.get(sec)
            kal_snap = kal_at_sec(kal_rows, ep, sec)
            lim_ok = is_sim_agree(lim_snap, poly_t, pred_t, side)
            kal_ok = is_sim_agree(kal_snap, poly_t, pred_t, side)
            if not (lim_ok or kal_ok): continue
            if fired: continue
            fired = True
            third = 'lim' if lim_ok else 'kal'
            third_snap = lim_snap if lim_ok else kal_snap
            outcome = poly_outs.get(ep) if plat == 'poly' else pred_outs.get(ep)
            won = outcome == side if outcome else None
            trades.append({
                'ep': ep, 'sec': sec, 'side': side, 'plat': plat, 'price': price,
                'poly_target': poly_t, 'pred_target': pred_t, 'third_target': third_snap.get('target'),
                'avg_target': (poly_t + pred_t)/2,
                'binance': poly.get('binance'),
                'dist_signed': dist,
                'dist_abs': abs(dist) if dist is not None else None,
                'third': third,
                'lim_also': lim_ok, 'kal_also': kal_ok,
                'both_third': lim_ok and kal_ok,
                'poly_up_ask': poly.get('up'),
                'poly_dn_ask': poly.get('down'),
                'pred_yes_ask': pred.get('up'),
                'pred_no_ask': pred.get('down'),
                'third_yes': third_snap.get('up'),
                'third_no': third_snap.get('down'),
                'won': won,
                'outcome': outcome,
            })

    trades.sort(key=lambda t: t['ep'])
    print(f'Total V3 candidate trades: {len(trades)}')
    print(f'Won: {sum(1 for t in trades if t["won"])}')
    print(f'Lost: {sum(1 for t in trades if t["won"] is False)}')
    print()

    print('='*100)
    print(f'{"#":<3} {"date":<11} {"UTC":<6} {"NYC":<3} {"sec":<4} {"side":<5} {"plat":<5} {"price":<6} {"third":<5} {"dist":<6} {"WIN?":<5}')
    print('='*100)
    for i, t in enumerate(trades, 1):
        dt = datetime.fromtimestamp(t['ep'], tz=timezone.utc)
        nyc = (dt - timedelta(hours=4)).hour
        date_str = dt.strftime('%Y-%m-%d')
        time_str = dt.strftime('%H:%M')
        won_str = 'WIN' if t['won'] else 'LOSS'
        dist = f"{t['dist_abs']:.0f}" if t['dist_abs'] is not None else 'NA'
        print(f'{i:<3} {date_str:<11} {time_str:<6} {nyc:<3} {t["sec"]:<4} {t["side"]:<5} '
              f'{t["plat"]:<5} {t["price"]:<6.3f} {t["third"]:<5} {dist:<6} {won_str:<5}')

    print()
    print('='*100)
    print('AGGREGATE PATTERNS')
    print('='*100)

    # by side
    by_side = Counter(t['side'] for t in trades)
    by_side_win = Counter(t['side'] for t in trades if t['won'])
    print(f'Side mix:       UP={by_side["UP"]} (wins {by_side_win["UP"]})   DOWN={by_side["DOWN"]} (wins {by_side_win["DOWN"]})')

    # by third platform
    by_third = Counter(t['third'] for t in trades)
    by_third_win = Counter(t['third'] for t in trades if t['won'])
    print(f'Third platform: lim={by_third["lim"]} (wins {by_third_win["lim"]})   kal={by_third["kal"]} (wins {by_third_win["kal"]})')

    # by plat chosen
    by_plat = Counter(t['plat'] for t in trades)
    by_plat_win = Counter(t['plat'] for t in trades if t['won'])
    print(f'Chosen plat:    poly={by_plat["poly"]} (wins {by_plat_win["poly"]})   predict={by_plat["predict"]} (wins {by_plat_win["predict"]})')

    # both lim AND kal sim+agree?
    print(f'Both Lim AND Kal sim+agree at fire: {sum(1 for t in trades if t["both_third"])}')

    # by entry sec
    print()
    print('By entry sec bucket:')
    buckets = defaultdict(lambda: [0, 0])  # [fires, wins]
    for t in trades:
        if t['sec'] < 60: bk = 'sec 30-60'
        elif t['sec'] < 120: bk = 'sec 60-120'
        elif t['sec'] < 180: bk = 'sec 120-180'
        elif t['sec'] < 240: bk = 'sec 180-240'
        else: bk = 'sec 240-270'
        buckets[bk][0] += 1
        if t['won']: buckets[bk][1] += 1
    for k in ('sec 30-60','sec 60-120','sec 120-180','sec 180-240','sec 240-270'):
        if k in buckets: print(f'  {k}: {buckets[k][0]} fires, {buckets[k][1]} wins')

    # by price bucket
    print()
    print('By price bucket:')
    price_buckets = defaultdict(lambda: [0, 0])
    for t in trades:
        p = t['price']
        if p < 0.55: bk = '<0.55'
        elif p < 0.65: bk = '0.55-0.65'
        elif p < 0.75: bk = '0.65-0.75'
        elif p < 0.85: bk = '0.75-0.85'
        else: bk = '>=0.85'
        price_buckets[bk][0] += 1
        if t['won']: price_buckets[bk][1] += 1
    for k in ('<0.55','0.55-0.65','0.65-0.75','0.75-0.85','>=0.85'):
        if k in price_buckets:
            print(f'  {k}: {price_buckets[k][0]} fires, {price_buckets[k][1]} wins')

    # by distance bucket
    print()
    print('By distance bucket:')
    d_buckets = defaultdict(lambda: [0, 0])
    for t in trades:
        d = t['dist_abs']
        if d is None: bk = 'NA'
        elif d < 20: bk = '0-20'
        elif d < 50: bk = '20-50'
        elif d < 100: bk = '50-100'
        elif d < 200: bk = '100-200'
        else: bk = '200+'
        d_buckets[bk][0] += 1
        if t['won']: d_buckets[bk][1] += 1
    for k in ('0-20','20-50','50-100','100-200','200+','NA'):
        if k in d_buckets:
            print(f'  {k}: {d_buckets[k][0]} fires, {d_buckets[k][1]} wins')

    # by NYC hour
    print()
    print('By NYC hour:')
    nyc_buckets = defaultdict(lambda: [0, 0])
    for t in trades:
        nyc = (datetime.fromtimestamp(t['ep'], tz=timezone.utc) - timedelta(hours=4)).hour
        nyc_buckets[nyc][0] += 1
        if t['won']: nyc_buckets[nyc][1] += 1
    for h in sorted(nyc_buckets.keys()):
        print(f'  hr {h:2d}: {nyc_buckets[h][0]} fires, {nyc_buckets[h][1]} wins')

    # by direction × side correlation
    print()
    print('Side vs distance sign:')
    print('(UP trades — what was binance vs target?)')
    for tside in ('UP','DOWN'):
        ds = [t['dist_signed'] for t in trades if t['side']==tside and t['dist_signed'] is not None]
        if not ds: continue
        avg = sum(ds)/len(ds)
        print(f'  {tside}: {len(ds)} trades, avg signed distance = {avg:+.1f} (positive means BTC above target)')

    # the losing trade — special look
    print()
    print('THE LOSING TRADE detail:')
    for t in trades:
        if t['won'] is False:
            dt = datetime.fromtimestamp(t['ep'], tz=timezone.utc)
            nyc = (dt - timedelta(hours=4)).hour
            print(f'  {dt.isoformat()}  NYC {nyc:02d}:xx  sec={t["sec"]}  '
                  f'side={t["side"]} on {t["plat"]} @ {t["price"]:.3f}  '
                  f'dist_signed={t["dist_signed"]:+.0f}  '
                  f'third={t["third"]} (target {t["third_target"]:.2f} vs avg {t["avg_target"]:.2f}, gap ${abs(t["third_target"]-t["avg_target"]):.2f})  '
                  f'binance={t["binance"]:.2f}  outcome={t["outcome"]}')


if __name__ == '__main__':
    main()
