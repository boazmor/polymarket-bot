#!/usr/bin/env python3
"""Two-camps analysis.
Camp A = Poly + Predict (they agree on a side = our base trade).
Camp B = the rest (Limitless, Gemini, Kalshi).
Question: when Camp B DISAGREES with Camp A, or has a TARGET GAP, who wins?
Does Poly+Pred still win, or do the others know better?
Direction itself is not analyzed — only agreement/disagreement and target gap.
"""
import sys, csv
from collections import defaultdict
from datetime import datetime, timezone

THR = 0.60
INVEST = 2.0
ENTER_MIN_SEC = 30
ENTER_MAX_SEC = 270
GAP_THR = 5.0  # USD; below this = "similar target", above = "gap"

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'
GEM     = '/root/data_gemini_btc_5m/combined_per_second.csv'
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
                'target': f(r.get('target_price')), 'oe': oe, 'ce': ce
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
            return {'up': c['up'], 'down': c['down'], 'target': c['target']}
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
    if not snap: return 'no_data'
    u = snap.get('up'); d = snap.get('down')
    if u is None and d is None: return 'no_data'
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
    return 'silent'


def main():
    poly_outs = load_poly_outcomes()
    pred_outs = load_pred_outcomes()
    poly_by_ep = load_per_sec(POLY,'market_epoch','sec_from_start',
                              'up_ask','down_ask','target_price')
    pred_by_ep = load_per_sec(PRED,'market_open_epoch','sec_from_open',
                              'yes_ask','no_ask_implied','strike')
    lim_by_ep  = load_per_sec(LIM,None,None,
                              'best_ask','no_best_ask','target_price',
                              derive_ep=derive_lim(lim_market_map()))
    gem_by_ep  = load_per_sec(GEM,'market_open_epoch','sec_from_open',
                              'best_ask','no_best_ask','target_price')
    kal_rows = load_kal_by_ep()

    eps = set(poly_by_ep.keys()) & set(pred_by_ep.keys())

    base = []
    for ep in eps:
        poly_secs = poly_by_ep[ep]; pred_secs = pred_by_ep[ep]
        for sec in range(ENTER_MIN_SEC, ENTER_MAX_SEC + 1):
            poly = poly_secs.get(sec); pred = pred_secs.get(sec)
            if not poly or not pred: continue
            side = consensus_at(poly, pred)
            if not side: continue
            pk = cheap_pick(poly, pred, side)
            if not pk: continue
            plat, price = pk
            poly_t = poly.get('target'); pred_t = pred.get('target')
            avg_t = (poly_t + pred_t)/2 if (poly_t is not None and pred_t is not None) else None
            base.append({
                'ep': ep, 'sec': sec, 'side': side, 'plat': plat, 'price': price, 'avg_t': avg_t,
                'lim': lim_by_ep.get(ep, {}).get(sec),
                'gem': gem_by_ep.get(ep, {}).get(sec),
                'kal': kal_at_sec(kal_rows, ep, sec),
            })
            break
    print(f'Base Poly+Pred trades: {len(base)}')

    def measure(rows):
        wins = losses = 0; pnl = 0.0
        for t in rows:
            outcome = poly_outs.get(t['ep']) if t['plat']=='poly' else pred_outs.get(t['ep'])
            if outcome is None: continue
            if outcome == t['side']: pnl += INVEST/t['price'] - INVEST; wins += 1
            else: pnl += -INVEST; losses += 1
        res = wins+losses
        return res, (100*wins/res if res else 0), pnl, (pnl/res if res else 0)

    def show(rows, label):
        res, wr, pnl, per = measure(rows)
        print(f"  {label:<46} n={len(rows):<4} win%={wr:5.1f}%  PnL=${pnl:+8.2f}  per ${per:+.3f}")

    # Camp B = others. For each base trade, classify others' stance.
    def others_stance(t):
        """Returns (n_agree, n_disagree, n_present)."""
        n_agree = n_disagree = n_present = 0
        for name in ('lim','gem','kal'):
            v = vote_of(t[name])
            if v == 'no_data': continue
            if v in ('UP','DOWN'):
                n_present += 1
                if v == t['side']: n_agree += 1
                else: n_disagree += 1
        return n_agree, n_disagree, n_present

    print()
    print('='*92)
    print('CAMP A (Poly+Pred) vs CAMP B (Lim/Gem/Kal) — does Poly+Pred win when others oppose?')
    print('='*92)
    show(base, 'ALL base trades')

    others_agree = []        # at least 1 other agrees, none disagree
    others_disagree = []     # at least 1 other disagrees, none agree
    others_mixed = []        # some agree, some disagree
    others_silent = []       # none of the others have a directional vote
    for t in base:
        na, nd, npz = others_stance(t)
        if npz == 0: others_silent.append(t)
        elif na > 0 and nd == 0: others_agree.append(t)
        elif nd > 0 and na == 0: others_disagree.append(t)
        else: others_mixed.append(t)

    show(others_agree,    'Others AGREE (>=1 agree, none oppose)')
    show(others_disagree, 'Others OPPOSE (>=1 oppose, none agree)')
    show(others_mixed,    'Others MIXED (some agree, some oppose)')
    show(others_silent,   'Others SILENT (no directional vote)')

    # Now combine OPPOSE with target gap of the opposing platform
    print()
    print('='*92)
    print('When others OPPOSE — does the opposing platform target GAP matter?')
    print('='*92)
    def opposing_gap(t):
        """Max |gap| among platforms that oppose Camp A."""
        gaps = []
        for name in ('lim','gem','kal'):
            v = vote_of(t[name])
            if v in ('UP','DOWN') and v != t['side']:
                tg = (t[name] or {}).get('target')
                if tg is not None and t['avg_t'] is not None:
                    gaps.append(tg - t['avg_t'])
        return gaps

    opp_similar = []   # opposing platform has SIMILAR target (<5)
    opp_lower = []     # opposing platform target LOWER than poly/pred
    opp_higher = []    # opposing platform target HIGHER
    for t in others_disagree:
        if t['avg_t'] is None: continue
        gaps = opposing_gap(t)
        if not gaps: continue
        g = max(gaps, key=abs)  # the biggest divergence
        if abs(g) <= GAP_THR: opp_similar.append(t)
        elif g < 0: opp_lower.append(t)
        else: opp_higher.append(t)
    show(opp_similar, 'Opposer SIMILAR target (<$5 gap)')
    show(opp_lower,   'Opposer target LOWER than poly/pred')
    show(opp_higher,  'Opposer target HIGHER than poly/pred')

    # And combine AGREE with target gap (mirror of the V3 finding)
    print()
    print('='*92)
    print('When others AGREE — does the agreeing platform target GAP matter?')
    print('='*92)
    def agreeing_gap(t):
        gaps = []
        for name in ('lim','gem','kal'):
            v = vote_of(t[name])
            if v in ('UP','DOWN') and v == t['side']:
                tg = (t[name] or {}).get('target')
                if tg is not None and t['avg_t'] is not None:
                    gaps.append(tg - t['avg_t'])
        return gaps
    ag_similar = []; ag_lower = []; ag_higher = []
    for t in others_agree:
        if t['avg_t'] is None: continue
        gaps = agreeing_gap(t)
        if not gaps: continue
        g = min(gaps, key=abs)  # the closest agreement
        if abs(g) <= GAP_THR: ag_similar.append(t)
        elif g < 0: ag_lower.append(t)
        else: ag_higher.append(t)
    show(ag_similar, 'Agreer SIMILAR target (<$5 gap)')
    show(ag_lower,   'Agreer target LOWER than poly/pred')
    show(ag_higher,  'Agreer target HIGHER than poly/pred')


if __name__ == '__main__':
    main()
