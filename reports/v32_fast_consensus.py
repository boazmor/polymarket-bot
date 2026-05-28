#!/usr/bin/env python3
"""V3.2-style analysis with corrected oracle understanding.
Fast platforms (Binance/Pyth, 5-min): Predict, Limitless, Gemini.
Signal = 2 or 3 of them agree on a side. Buy the CHEAPEST of all 5m platforms
(Poly, Predict, Limitless, Gemini) for that side — Poly usually cheapest due to
Chainlink lag. Resolve by the bought platform's own outcome. 2% commission.
Reports win rates, where we buy, recording span, trades per hour/day.
"""
import sys, csv, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone

THR = 0.60
INVEST = 2.0
COMMISSION = 0.02
REF_SEC = 90

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'
GEM     = '/root/data_gemini_btc_5m/combined_per_second.csv'


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
        if fin is not None: out[ep] = 'UP' if fin > s else 'DOWN'
    return out


def load_lim_outcomes():
    m = {}; tgt = {}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try:
                mid=r['market_id']; m[mid]=int(r['expirationTimestamp'])//1000-300
                t=f(r.get('target_price'))
                if t is not None: tgt[mid]=t
            except: pass
    last_bn = {}
    with open(LIM) as fh:
        for r in csv.DictReader(fh):
            mid=r.get('market_id');
            if mid is None: continue
            bn=f(r.get('binance_now'))
            if bn is not None: last_bn[mid]=bn
    out={}
    for mid,ep in m.items():
        t=tgt.get(mid); fin=last_bn.get(mid)
        if t is not None and fin is not None: out[ep]='UP' if fin>t else 'DOWN'
    return out


def load_gem_outcomes():
    last_bn={}; tgt={}
    with open(GEM) as fh:
        for r in csv.DictReader(fh):
            try: ep=int(r['market_open_epoch'])
            except: continue
            bn=f(r.get('binance_now')); t=f(r.get('target_price'))
            if bn is not None: last_bn[ep]=bn
            if t is not None and ep not in tgt: tgt[ep]=t
    out={}
    for ep,t in tgt.items():
        fin=last_bn.get(ep)
        if fin is not None: out[ep]='UP' if fin>t else 'DOWN'
    return out


def load_at(path, epcol, seccol, upcol, downcol, sec):
    out={}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try: ep=int(r[epcol]); s=int(r[seccol])
            except: continue
            if s!=sec: continue
            out[ep]={'up':f(r.get(upcol)),'down':f(r.get(downcol))}
    return out


def load_lim_at(sec):
    m={}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-300
            except: pass
    out={}
    with open(LIM) as fh:
        for r in csv.DictReader(fh):
            ep=m.get(r.get('market_id'))
            if ep is None: continue
            try: es=int(r['epoch_sec'])
            except: continue
            if es-ep!=sec: continue
            out[ep]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask'))}
    return out


def vote(snap):
    if not snap: return None
    u=snap.get('up'); d=snap.get('down')
    uo=u is not None and u>=THR; do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None


def main():
    poly_o=load_poly_outcomes(); pred_o=load_pred_outcomes()
    lim_o=load_lim_outcomes(); gem_o=load_gem_outcomes()
    poly=load_at(POLY,'market_epoch','sec_from_start','up_ask','down_ask',REF_SEC)
    pred=load_at(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied',REF_SEC)
    lim=load_lim_at(REF_SEC)
    gem=load_at(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask',REF_SEC)

    # recording span
    all_eps=set(poly)|set(pred)|set(lim)
    span_lo=min(all_eps); span_hi=max(all_eps)
    hours=(span_hi-span_lo)/3600
    print(f'commission={COMMISSION*100:.0f}%  thr={THR}  ref_sec={REF_SEC}  buy=cheapest of 5m platforms')
    print(f'recording span: {datetime.fromtimestamp(span_lo,tz=timezone.utc):%Y-%m-%d %H:%M} -> {datetime.fromtimestamp(span_hi,tz=timezone.utc):%Y-%m-%d %H:%M} UTC = {hours:.1f}h ({hours/24:.1f} days)')
    print(f'windows: poly={len(poly)} pred={len(pred)} lim={len(lim)} gem={len(gem)}')
    print(f'  gemini coverage starts later (only ~1 day)')
    print()

    def outcome_of(plat, ep):
        return {'poly':poly_o,'predict':pred_o,'lim':lim_o,'gem':gem_o}[plat].get(ep)

    def run(require_n, use_gem):
        fast = ['pred','lim'] + (['gem'] if use_gem else [])
        snaps={'pred':pred,'lim':lim,'gem':gem}
        eps=set(pred)&set(lim)
        if use_gem: eps&=set(gem)
        trades=[]
        for ep in eps:
            votes=[vote(snaps[p].get(ep)) for p in fast]
            ups=votes.count('UP'); downs=votes.count('DOWN')
            side=None
            if ups>=require_n and ups>downs: side='UP'
            elif downs>=require_n and downs>ups: side='DOWN'
            if not side: continue
            # buy cheapest of available 5m platforms for that side
            cands=[]
            for p,snap in [('poly',poly.get(ep)),('predict',pred.get(ep)),('lim',lim.get(ep)),('gem',gem.get(ep))]:
                if not snap: continue
                px=snap.get('up') if side=='UP' else snap.get('down')
                if px is not None and px>0: cands.append((p,px))
            if not cands: continue
            plat,price=min(cands,key=lambda x:x[1])
            trades.append((ep,side,plat,price))
        return trades

    def report(trades, label):
        wins=losses=pend=0; net=0.0; pls=[]
        buyloc=Counter()
        for ep,side,plat,price in trades:
            buyloc[plat]+=1
            oc=outcome_of(plat,ep)
            if oc is None: pend+=1; continue
            cost=INVEST*(1+COMMISSION)
            if oc==side: pnl=INVEST/price-cost; wins+=1
            else: pnl=-cost; losses+=1
            net+=pnl; pls.append(pnl)
        res=wins+losses
        wr=100*wins/res if res else 0
        per=net/res if res else 0
        sd=statistics.pstdev(pls) if len(pls)>1 else 0
        print(f'{label}')
        print(f'  fires={len(trades)}  resolved={res}  win%={wr:.1f}  net=${net:+.2f}  per=${per:+.3f}  sd=${sd:.2f}')
        # per hour / day
        if hours>0:
            print(f'  rate: {len(trades)/hours:.2f}/hour  {len(trades)/(hours/24):.1f}/day')
        loc=', '.join(f'{k}:{v}' for k,v in buyloc.most_common())
        print(f'  bought on: {loc}')
        print()

    print('='*80)
    print('SIGNAL = fast platforms agree (Predict, Limitless [, Gemini]); BUY cheapest')
    print('='*80)
    print('-- Pred+Lim only (most data, full span) --')
    report(run(2, False), '2 of 2 fast agree (Pred+Lim)')
    print('-- Pred+Lim+Gem (needs all 3, gemini-limited span) --')
    report(run(2, True), '2 of 3 fast agree (Pred+Lim+Gem)')
    report(run(3, True), '3 of 3 fast agree (Pred+Lim+Gem)')


if __name__ == '__main__':
    main()
