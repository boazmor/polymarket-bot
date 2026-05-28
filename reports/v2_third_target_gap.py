#!/usr/bin/env python3
"""Third-platform target-gap analysis across ALL seconds 0..299 of the 5-min window.
For each second:
  - Build V2 trades (Poly+Pred consensus + distance filter)
  - For each candidate third platform (Lim/Gem/Kal):
      - Coverage at that sec
      - Win-rate / PnL within target-gap buckets
      - Within each bucket, split by vote (agree/disagree/silent)
"""
import sys, csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta

THR = 0.60
INVEST = 2.0
SECS_TO_REPORT = [15, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 290]

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'
GEM     = '/root/data_gemini_btc_5m/combined_per_second.csv'
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


def load_all_per_sec(path, key_open, sec_col, up_col, down_col, target_col, bn_col,
                    derive_ep=None):
    """Returns dict: (ep, sec) -> {up, down, target, binance}."""
    snaps = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if derive_ep:
                pair = derive_ep(r)
                if pair is None: continue
                ep, sec = pair
            else:
                try: ep = int(r[key_open]); sec = int(r[sec_col])
                except: continue
            snaps[(ep, sec)] = {
                'up': f(r.get(up_col)),
                'down': f(r.get(down_col)),
                'target': f(r.get(target_col)),
                'binance': f(r.get(bn_col)),
            }
    return snaps


def load_kal_per_sec():
    """Kalshi: keyed by epoch_sec, then look up the row whose window contains
    poly_ep+sec. Return a lookup function."""
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
        target_sec = poly_ep + sec
        cands = rows_by_es.get(target_sec, [])
        for c in cands:
            if c['oe'] <= target_sec <= c['ce']:
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
    if not snap: return 'no_data'
    u = snap.get('up'); d = snap.get('down')
    if u is None and d is None: return 'no_data'
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
    return 'silent'


def target_gap_bucket(third_target, poly_target, pred_target):
    if third_target is None: return 'no_target'
    if poly_target is None or pred_target is None: return 'no_ref'
    avg = (poly_target + pred_target) / 2
    gap = third_target - avg
    if abs(gap) < 5:    return 'similar(<5)'
    if abs(gap) < 20:   return 'close(5-20)'
    if abs(gap) < 50:   return 'medium(20-50)'
    if gap > 0:         return 'higher(50+)'
    return 'lower(50+)'


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


def fmt(stats, name):
    s = stats
    return f"  {name:<48} fires={s['fires']:<4} win%={s['wr']:5.1f}%  PnL=${s['pnl']:+8.2f}  per-trade ${s['per']:+.3f}"


def main():
    print('Loading outcomes & all-second snaps for poly/pred/lim/gem ...')
    poly_outs = load_poly_outcomes()
    pred_outs = load_pred_outcomes()
    poly_snaps = load_all_per_sec(POLY, 'market_epoch', 'sec_from_start',
                                  'up_ask','down_ask','target_price','binance_price')
    pred_snaps = load_all_per_sec(PRED, 'market_open_epoch','sec_from_open',
                                  'yes_ask','no_ask_implied','strike','binance_now')
    lim_snaps  = load_all_per_sec(LIM, None, None,
                                  'best_ask','no_best_ask','target_price','binance_now',
                                  derive_ep=derive_lim(lim_market_map()))
    gem_snaps  = load_all_per_sec(GEM, 'market_open_epoch','sec_from_open',
                                  'best_ask','no_best_ask','target_price','binance_now')
    kal_at = load_kal_per_sec()
    print(f'  poly outs: {len(poly_outs)}  pred outs: {len(pred_outs)}')
    print(f'  per-sec snaps: poly={len(poly_snaps)} pred={len(pred_snaps)} '
          f'lim={len(lim_snaps)} gem={len(gem_snaps)}')

    # ========================================================================
    # Layer 1 — coverage of each third platform at each SECOND of window
    # ========================================================================
    print()
    print('='*95)
    print('STAGE A — V2 baseline per second; for context')
    print('='*95)
    print(f"  {'sec':<4} {'fires':<5} {'win%':<7} {'PnL$':<10} {'per-trade':<10}")
    v2_by_sec = {}
    for sec in SECS_TO_REPORT:
        v2 = []
        eps = set(p[0] for p in poly_snaps.keys() if p[1] == sec)
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
                   'gem': gem_snaps.get((ep, sec)),
                   'kal': kal_at(ep, sec)}
            v2.append((row, side, plat, price))
        v2_by_sec[sec] = v2
        s = measure(v2, poly_outs, pred_outs)
        print(f"  {sec:<4} {s['fires']:<5} {s['wr']:<6.1f}% ${s['pnl']:+8.2f}  ${s['per']:+.3f}")

    # ========================================================================
    # Layer 2 — for each third platform, target-gap bucket × vote
    # ========================================================================
    for third in ('lim', 'gem', 'kal'):
        print()
        print('='*95)
        print(f'STAGE B — third platform = {third.upper()}: target-gap × vote, by entry sec')
        print('='*95)
        for sec in SECS_TO_REPORT:
            v2 = v2_by_sec[sec]
            cov = [t for t in v2 if (t[0].get(third) or {}).get('up') is not None or
                                    (t[0].get(third) or {}).get('down') is not None]
            print(f'  --- sec={sec}: coverage {len(cov)}/{len(v2)} V2 trades have {third} data ---')
            if not cov: continue
            # bucket by target gap
            buckets = defaultdict(list)
            for t in cov:
                row, side, plat, price = t
                third_snap = row.get(third) or {}
                tg = third_snap.get('target')
                pt = (row.get('poly') or {}).get('target')
                rt = (row.get('pred') or {}).get('target')
                buckets[target_gap_bucket(tg, pt, rt)].append(t)
            # report each bucket overall + agree-only inside it
            for k in ('similar(<5)','close(5-20)','medium(20-50)','higher(50+)','lower(50+)','no_target','no_ref'):
                if k not in buckets: continue
                b = buckets[k]
                s = measure(b, poly_outs, pred_outs)
                if s['res'] < 2: continue   # skip noise
                print(fmt(s, f'sec={sec} gap={k} ALL'))
                agree = [t for t in b if vote_of(t[0].get(third)) == t[1]]
                sa = measure(agree, poly_outs, pred_outs)
                if sa['res'] >= 2:
                    print(fmt(sa, f'sec={sec} gap={k} AGREE'))

    # ========================================================================
    # Layer 3 — aggregate ACROSS seconds for each (third, gap, vote)
    # ========================================================================
    print()
    print('='*95)
    print('STAGE C — AGGREGATE across all seconds 15-290, per third platform')
    print('='*95)
    for third in ('lim', 'gem', 'kal'):
        all_cov = []
        for sec in SECS_TO_REPORT:
            for t in v2_by_sec[sec]:
                snap = t[0].get(third) or {}
                if snap.get('up') is None and snap.get('down') is None: continue
                all_cov.append(t)
        if not all_cov:
            print(f'\n  {third}: NO data across selected seconds.')
            continue
        print(f'\n  -- {third.upper()} aggregate across all seconds: {len(all_cov)} trades --')
        s = measure(all_cov, poly_outs, pred_outs)
        print(fmt(s, f'{third} coverage ALL'))
        # by gap bucket
        buckets = defaultdict(list)
        for t in all_cov:
            row, side, plat, price = t
            third_snap = row.get(third) or {}
            tg = third_snap.get('target')
            pt = (row.get('poly') or {}).get('target')
            rt = (row.get('pred') or {}).get('target')
            buckets[target_gap_bucket(tg, pt, rt)].append(t)
        for k in ('similar(<5)','close(5-20)','medium(20-50)','higher(50+)','lower(50+)','no_target','no_ref'):
            if k not in buckets: continue
            b = buckets[k]
            s = measure(b, poly_outs, pred_outs)
            print(fmt(s, f'{third} gap={k} ALL'))
            for vote_kind in ('AGREE','DISAGREE','SILENT'):
                if vote_kind == 'AGREE':
                    sub = [t for t in b if vote_of(t[0].get(third)) == t[1]]
                elif vote_kind == 'DISAGREE':
                    sub = [t for t in b if vote_of(t[0].get(third)) in ('UP','DOWN') and vote_of(t[0].get(third)) != t[1]]
                else:
                    sub = [t for t in b if vote_of(t[0].get(third)) == 'silent']
                ss = measure(sub, poly_outs, pred_outs)
                if ss['res'] >= 1:
                    print(fmt(ss, f'    gap={k} {vote_kind}'))


if __name__ == '__main__':
    main()
