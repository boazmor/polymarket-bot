#!/usr/bin/env python3
"""Clean 5-min consensus: genuine 5-min platforms only, NO Kalshi (which is 15m).
Fast 5-min voters: Predict5, Limitless5, OKX5, Gemini5. Require >=3 agree.
Buy cheapest tradeable (poly/pred/lim). No time limit. 2% commission.
Compares thr 0.60 vs 0.70 and shows which platforms formed the trio.
"""
import csv, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone

INVEST=2.0; COMM=0.02; GAP=200; SMIN=10; SMAX=295

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
LIM='/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK='/root/data_limitless_btc_5m/markets.csv'
GEM='/root/data_gemini_btc_5m/combined_per_second.csv'
OKX='/root/data_okx_btc_5m/combined_per_second.csv'


def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None
def poly_outs():
    o={}
    for r in csv.DictReader(open(POLYOUT)):
        try:
            ep=int(r['market_epoch'])
            if r.get('winner_side') in ('UP','DOWN'): o[ep]=r['winner_side']
        except: pass
    return o
def pred_outs():
    lb={};sk={}
    for r in csv.DictReader(open(PRED)):
        try:
            ep=int(r['market_open_epoch']);bn=f(r.get('binance_now'));tg=f(r.get('strike'))
            if bn is not None: lb[ep]=bn
            if tg is not None: sk[ep]=tg
        except: pass
    return {ep:('UP' if lb[ep]>s else 'DOWN') for ep,s in sk.items() if ep in lb}
def lim_outs():
    m={};tg={}
    for r in csv.DictReader(open(LIMMK)):
        try:
            mid=r['market_id'];m[mid]=int(r['expirationTimestamp'])//1000-300
            t=f(r.get('target_price'))
            if t is not None: tg[mid]=t
        except: pass
    lb={}
    for r in csv.DictReader(open(LIM)):
        mid=r.get('market_id')
        if mid is None: continue
        bn=f(r.get('binance_now'))
        if bn is not None: lb[mid]=bn
    return {ep:('UP' if lb[mid]>tg[mid] else 'DOWN') for mid,ep in m.items() if mid in lb and mid in tg}
def load_allsec(path,epc,sc,up,dn,tg):
    out=defaultdict(dict)
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]);s=int(r[sc])
        except: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out
def load_lim_allsec():
    m={}
    for r in csv.DictReader(open(LIMMK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-300
        except: pass
    out=defaultdict(dict)
    for r in csv.DictReader(open(LIM)):
        ep=m.get(r.get('market_id'))
        if ep is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        s=es-ep
        if s<0 or s>320: continue
        out[ep][s]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask')),'target':f(r.get('target_price'))}
    return out

def run(thr):
    po=poly_outs();pro=pred_outs();lo=lim_outs()
    poly=load_allsec(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
    pred=load_allsec(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    lim=load_lim_allsec()
    gem=load_allsec(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
    okx=load_allsec(OKX,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
    def vote(s):
        if not s: return None
        u=s.get('up');d=s.get('down')
        uo=u is not None and u>=thr;do=d is not None and d>=thr
        if uo and not do: return 'UP'
        if do and not uo: return 'DOWN'
        return None
    # restrict to windows where OKX has data (since OKX is the new genuine-5m leg)
    eps=set(pred)&set(poly)&set(okx)
    trades=[]; trio_counts=Counter()
    for ep in eps:
        if ep not in po: continue
        fired=False
        for sec in range(SMIN,SMAX+1):
            ps=pred[ep].get(sec);ls=lim[ep].get(sec);gs=gem[ep].get(sec);os_=okx[ep].get(sec)
            votes={}
            for nm,snp in (('pred',ps),('lim',ls),('gem',gs),('okx',os_)):
                v=vote(snp)
                if v: votes[nm]=(v,snp.get('target'))
            for side in ('UP','DOWN'):
                ag=[(nm,t) for nm,(vv,t) in votes.items() if vv==side]
                if len(ag)<3: continue
                tgs=[t for _,t in ag if t is not None]
                if len(tgs)>=2 and (max(tgs)-min(tgs))>GAP: continue
                cands=[]
                for p,snp in (('poly',poly[ep].get(sec)),('pred',ps),('lim',ls)):
                    if not snp: continue
                    px=snp.get('up') if side=='UP' else snp.get('down')
                    if px and 0.01<px<0.99: cands.append((p,px))
                if not cands: continue
                plat,price=min(cands,key=lambda x:x[1])
                oc={'poly':po,'pred':pro,'lim':lo}[plat].get(ep)
                if oc is None: continue
                trio_counts['+'.join(sorted(n for n,_ in ag))]+=1
                trades.append((price,oc==side))
                fired=True;break
            if fired: break
    return trades, trio_counts, len(eps)

def rep(rows,label):
    if not rows: print('  %-22s n=0'%label); return
    n=len(rows);w=sum(1 for r in rows if r[1])
    ap=statistics.mean([r[0] for r in rows])
    pls=[(INVEST/r[0]-INVEST*(1+COMM)) if r[1] else -INVEST*(1+COMM) for r in rows]
    net=sum(pls);per=net/n
    print('  %-22s n=%-4d win%%=%4.1f avgP=%.3f net=%+6.2f per=%+.3f'%(label,n,100*w/n,ap,net,per))

def main():
    print('CLEAN 5-min consensus: pred5+lim5+okx5+gem5, >=3 agree, NO kalshi')
    print('(restricted to windows where OKX has data ~ last 17h)')
    print('='*70)
    for thr in (0.60,0.70):
        trades,trio,nw=run(thr)
        print('thr=%.2f  candidate windows(with okx)=%d'%(thr,nw))
        rep(trades,'all >=3 agree')
        band=[r for r in trades if 0.55<=r[0]<0.70]
        rep(band,'price 0.55-0.70')
        print('  trio composition:',dict(trio))
        print()

if __name__=='__main__':
    main()
