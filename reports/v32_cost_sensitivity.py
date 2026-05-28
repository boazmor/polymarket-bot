#!/usr/bin/env python3
"""Cost + risk sensitivity for the fast-consensus / buy-cheapest strategy.
Sweep commission 2/3/4/5%. Report per combo: avg buy price, actual win%,
break-even win% at each cost, net per-trade, standard deviation, per/sd.
"""
import sys, csv, statistics
from collections import Counter
from datetime import datetime, timezone

THR = 0.60
INVEST = 2.0
REF_SEC = 90
COSTS = [0.02, 0.03, 0.04, 0.05]

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
    out={}
    for r in csv.DictReader(open(POLYOUT)):
        try:
            ep=int(r['market_epoch'])
            if r.get('winner_side') in ('UP','DOWN'): out[ep]=r['winner_side']
        except: pass
    return out


def load_pred_outcomes():
    lb={}; sk={}
    for r in csv.DictReader(open(PRED)):
        try:
            ep=int(r['market_open_epoch']); bn=f(r.get('binance_now')); tg=f(r.get('strike'))
            if bn is not None: lb[ep]=bn
            if tg is not None: sk[ep]=tg
        except: pass
    return {ep:('UP' if lb[ep]>s else 'DOWN') for ep,s in sk.items() if ep in lb}


def load_lim_outcomes():
    m={}; tg={}
    for r in csv.DictReader(open(LIMMK)):
        try:
            mid=r['market_id']; m[mid]=int(r['expirationTimestamp'])//1000-300
            t=f(r.get('target_price'))
            if t is not None: tg[mid]=t
        except: pass
    lb={}
    for r in csv.DictReader(open(LIM)):
        mid=r.get('market_id')
        if mid is None: continue
        bn=f(r.get('binance_now'))
        if bn is not None: lb[mid]=bn
    out={}
    for mid,ep in m.items():
        t=tg.get(mid); fin=lb.get(mid)
        if t is not None and fin is not None: out[ep]='UP' if fin>t else 'DOWN'
    return out


def load_gem_outcomes():
    lb={}; tg={}
    for r in csv.DictReader(open(GEM)):
        try: ep=int(r['market_open_epoch'])
        except: continue
        bn=f(r.get('binance_now')); t=f(r.get('target_price'))
        if bn is not None: lb[ep]=bn
        if t is not None and ep not in tg: tg[ep]=t
    return {ep:('UP' if lb[ep]>t else 'DOWN') for ep,t in tg.items() if ep in lb}


def load_at(path, epcol, seccol, upcol, downcol, sec):
    out={}
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epcol]); s=int(r[seccol])
        except: continue
        if s!=sec: continue
        out[ep]={'up':f(r.get(upcol)),'down':f(r.get(downcol))}
    return out


def load_lim_at(sec):
    m={}
    for r in csv.DictReader(open(LIMMK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-300
        except: pass
    out={}
    for r in csv.DictReader(open(LIM)):
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
    oc={'poly':poly_o,'predict':pred_o,'lim':lim_o,'gem':gem_o}

    def build(require_n, use_gem):
        fast=['pred','lim']+(['gem'] if use_gem else [])
        snaps={'pred':pred,'lim':lim,'gem':gem}
        eps=set(pred)&set(lim)
        if use_gem: eps&=set(gem)
        trades=[]
        for ep in eps:
            votes=[vote(snaps[p].get(ep)) for p in fast]
            ups=votes.count('UP'); dns=votes.count('DOWN')
            side='UP' if (ups>=require_n and ups>dns) else ('DOWN' if (dns>=require_n and dns>ups) else None)
            if not side: continue
            cands=[]
            for p,snap in [('poly',poly.get(ep)),('predict',pred.get(ep)),('lim',lim.get(ep)),('gem',gem.get(ep))]:
                if not snap: continue
                px=snap.get('up') if side=='UP' else snap.get('down')
                if px and px>0: cands.append((p,px))
            if not cands: continue
            plat,price=min(cands,key=lambda x:x[1])
            o=oc[plat].get(ep)
            if o is None: continue
            trades.append((side,plat,price,o))
        return trades

    def report(trades,label):
        if not trades:
            print(f'{label}: no resolved trades'); return
        n=len(trades)
        wins=sum(1 for s,p,pr,o in trades if o==s)
        wr=100*wins/n
        avgp=statistics.mean([pr for _,_,pr,_ in trades])
        print(f'{label}')
        print(f'  resolved={n}  win%={wr:.1f}  avg_buy_price={avgp:.3f}')
        for c in COSTS:
            pls=[]
            for s,p,pr,o in trades:
                cost=INVEST*(1+c)
                pls.append(INVEST/pr-cost if o==s else -cost)
            net=sum(pls); per=net/n
            sd=statistics.pstdev(pls) if n>1 else 0
            persd=per/sd if sd>0 else 0
            be=avgp*(1+c)*100  # break-even win% at avg price
            verdict='PROFIT' if per>0 else 'loss'
            print(f'    cost {int(c*100)}%: net=${net:+8.2f}  per=${per:+.3f}  sd=${sd:.2f}  per/sd={persd:+.2f}  break-even-win={be:.1f}%  -> {verdict}')
        print()

    print(f'thr={THR} ref_sec={REF_SEC} buy=cheapest. break-even-win = avg_price*(1+cost).')
    print('If actual win% < break-even win%, the trade set loses money.\n')
    print('='*86)
    report(build(2,False),'2 of 2 fast (Pred+Lim) — full 16.8d sample')
    report(build(2,True),'2 of 3 fast (Pred+Lim+Gem) — gemini-limited ~1d')
    report(build(3,True),'3 of 3 fast (Pred+Lim+Gem) — gemini-limited ~1d')


if __name__ == '__main__':
    main()
