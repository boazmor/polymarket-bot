#!/usr/bin/env python3
"""15-min 'third part' confirmation for the 5-min trade.
A 15-min Kalshi window split into 3 parts of 5 min each. Its THIRD part
[+10min, +15min] ENDS at the same moment as a standalone 5-min window.
So during that 5-min window, Kalshi's 15-min market is in its final third
and resolves together with the 5-min market.
Test: does Kalshi-15m agreeing (during its third part) confirm/improve the
5-min Poly+Pred trade? Wisdom-of-crowds extra leg.
Commission 2%, standard deviation included.
"""
import sys, csv, statistics
from collections import defaultdict

THR = 0.60
INVEST = 2.0
COMMISSION = 0.02
REF_SEC = 90   # 5-min decision moment (also = sec 690 of the 15-min window)

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
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


def load_5m_at(path, key_open, sec_col, up_col, down_col, target_col, sec):
    out = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r[key_open]); s = int(r[sec_col])
            except: continue
            if s != sec: continue
            out[ep] = {'up': f(r.get(up_col)), 'down': f(r.get(down_col)),
                       'target': f(r.get(target_col))}
    return out


def load_kal_rows():
    """Return list of all kalshi rows with parsed fields, plus index by epoch_sec."""
    by_es = defaultdict(list)
    by_close = defaultdict(list)
    with open(KAL) as fh:
        for r in csv.DictReader(fh):
            try:
                es = int(r['epoch_sec']); oe = int(r['open_epoch']); ce = int(r['close_epoch'])
            except: continue
            rec = {'es': es, 'oe': oe, 'ce': ce,
                   'up': f(r.get('yes_ask')), 'down': f(r.get('no_ask')),
                   'target': f(r.get('target_price')),
                   'sec_from_open': es - oe}
            by_es[es].append(rec)
            by_close[ce].append(rec)
    return by_es, by_close


def kal_third_part_snapshot(by_es, poly_ep, ref_sec):
    """The 5-min window starts at poly_ep, ends at poly_ep+300.
    Find the kalshi 15-min window whose close == poly_ep+300.
    Read it at real time poly_ep+ref_sec (which is sec_from_open = ref_sec+600)."""
    end5 = poly_ep + 300
    target_time = poly_ep + ref_sec
    cands = by_es.get(target_time, [])
    for c in cands:
        if c['ce'] == end5 and c['oe'] == end5 - 900:
            # confirm we're in the third part: sec_from_open in [600, 900]
            if 600 <= c['sec_from_open'] <= 900:
                return c
    return None


def vote_of(snap):
    if not snap: return 'no_data'
    u = snap.get('up'); d = snap.get('down')
    if u is None and d is None: return 'no_data'
    u_ok = u is not None and u >= THR
    d_ok = d is not None and d >= THR
    if u_ok and not d_ok: return 'UP'
    if d_ok and not u_ok: return 'DOWN'
    return 'silent'


def net_pnl(price, won):
    shares = INVEST / price
    cost = INVEST * (1 + COMMISSION)
    return (shares - cost) if won else (-cost)


def stats_block(rows):
    """rows = list of (price, won)."""
    if not rows: return "n=0"
    pnls = [net_pnl(p, w) for p, w in rows]
    wins = sum(1 for _, w in rows if w)
    total = sum(pnls); per = total/len(pnls)
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    wr = 100*wins/len(rows)
    sharpe = (per/sd) if sd > 0 else 0
    return (f"n={len(rows):<4} win%={wr:5.1f}%  net=${total:+7.2f}  "
            f"per=${per:+.3f}  sd=${sd:.2f}  per/sd={sharpe:+.2f}")


def main():
    poly_outs = load_poly_outcomes()
    poly = load_5m_at(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price', REF_SEC)
    pred = load_5m_at(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike', REF_SEC)
    by_es, by_close = load_kal_rows()
    print(f'commission={COMMISSION*100:.0f}%  invest=${INVEST}  ref_sec={REF_SEC}')
    print(f'5m snaps: poly={len(poly)} pred={len(pred)}')

    eps = set(poly.keys()) & set(pred.keys()) & set(poly_outs.keys())
    # Build 5-min Poly+Pred consensus trades
    base = []
    aligned = 0
    for ep in eps:
        p = poly[ep]; pr = pred[ep]
        pu, pd = p.get('up'), p.get('down')
        yu, yd = pr.get('up'), pr.get('down')
        pu_ok = pu is not None and pu >= THR
        pd_ok = pd is not None and pd >= THR
        yu_ok = yu is not None and yu >= THR
        yd_ok = yd is not None and yd >= THR
        if pu_ok and yu_ok: side = 'UP'
        elif pd_ok and yd_ok: side = 'DOWN'
        else: continue
        price = (pu if side=='UP' else pd)
        # is this 5-min window aligned to a kalshi 15-min third part?
        kal = kal_third_part_snapshot(by_es, ep, REF_SEC)
        if kal: aligned += 1
        base.append({'ep': ep, 'side': side, 'price': price,
                     'outcome': poly_outs[ep], 'kal': kal})
    print(f'5-min Poly+Pred consensus trades: {len(base)}')
    print(f'  of which aligned to a Kalshi-15m third part with data: {aligned}')
    print()

    def rows_of(trades):
        return [(t['price'], t['outcome'] == t['side']) for t in trades]

    print('='*92)
    print('Does the Kalshi-15m third-part signal confirm the 5-min Poly+Pred trade?')
    print('='*92)
    print('  ALL 5-min consensus: ' + stats_block(rows_of(base)))

    aligned_trades = [t for t in base if t['kal']]
    print('  aligned to Kalshi-15m third part: ' + stats_block(rows_of(aligned_trades)))

    # split by kalshi 15m vote
    kal_agree = [t for t in aligned_trades if vote_of(t['kal']) == t['side']]
    kal_disagree = [t for t in aligned_trades if vote_of(t['kal']) in ('UP','DOWN') and vote_of(t['kal']) != t['side']]
    kal_silent = [t for t in aligned_trades if vote_of(t['kal']) == 'silent']
    print()
    print('  -- split by Kalshi-15m third-part vote --')
    print('  Kalshi-15m AGREES:    ' + stats_block(rows_of(kal_agree)))
    print('  Kalshi-15m DISAGREES: ' + stats_block(rows_of(kal_disagree)))
    print('  Kalshi-15m SILENT:    ' + stats_block(rows_of(kal_silent)))

    # also: kalshi 15m target gap vs the 5-min implied? (informational)
    print()
    print('  -- when Kalshi-15m AGREES, by its target gap vs poly 5-min target --')
    buckets = defaultdict(list)
    for t in kal_agree:
        kt = t['kal'].get('target')
        pt = poly[t['ep']].get('target')
        if kt is None or pt is None:
            buckets['no_target'].append(t); continue
        g = abs(kt - pt)
        if g < 20: bk = 'gap <20'
        elif g < 50: bk = 'gap 20-50'
        elif g < 100: bk = 'gap 50-100'
        else: bk = 'gap 100+'
        buckets[bk].append(t)
    for k in ('gap <20','gap 20-50','gap 50-100','gap 100+','no_target'):
        if k in buckets:
            print(f'    {k:<12} ' + stats_block(rows_of(buckets[k])))


if __name__ == '__main__':
    main()
