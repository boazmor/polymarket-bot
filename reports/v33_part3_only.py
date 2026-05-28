#!/usr/bin/env python3
"""Focus ONLY on 5-min windows that coincide with the THIRD part of a 15-min window.
For those windows the 15-min market is live in its part 3 and resolves WITH us.
Base 5-min consensus: 3 of {pred5,lim5,okx5,gem5} agree (NO poly voting, NO kalshi).
4th leg: the concurrent 15-min part-3 (predict15/okx15/lim15) must also agree.
Buy cheapest tradeable (poly/pred/lim). Report counts, buy prices, win%, per-$1 ROI net 2%.
"""
import csv, statistics
from collections import defaultdict

THR=0.70; COMM=0.02; GAP=200; SMIN=10; SMAX=295

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
LIM='/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK='/root/data_limitless_btc_5m/markets.csv'
GEM='/root/data_gemini_btc_5m/combined_per_second.csv'
OKX='/root/data_okx_btc_5m/combined_per_second.csv'
PRED15='/root/data_predict_btc_15m/combined_per_second.csv'
OKX15='/root/data_okx_btc_15m/combined_per_second.csv'
LIM15='/root/data_limitless_btc_15m/combined_per_second.csv'
LIM15MK='/root/data_limitless_btc_15m/markets.csv'


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
def la(path,epc,sc,up,dn,tg):
    out=defaultdict(dict)
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]);s=int(r[sc])
        except: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out
def lal(path,mk,up,dn,tg,off,maxs):
    m={}
    for r in csv.DictReader(open(mk)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-off
        except: pass
    out=defaultdict(dict)
    for r in csv.DictReader(open(path)):
        ep=m.get(r.get('market_id'))
        if ep is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        s=es-ep
        if 0<=s<=maxs: out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out
def vote(s):
    if not s: return None
    u=s.get('up');d=s.get('down')
    uo=u is not None and u>=THR;do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None

def main():
    po=poly_outs();pro=pred_outs();lo=lim_outs()
    poly=la(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
    pred=la(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    lim=lal(LIM,LIMMK,'best_ask','no_best_ask','target_price',300,320)
    gem=la(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
    okx=la(OKX,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
    p15=la(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    o15=la(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
    l15=lal(LIM15,LIM15MK,'best_ask','no_best_ask','target_price',900,960)

    def part3_aligned(ep5):
        # 15m window open = ep5-600 must exist in at least one 15m source
        o=ep5-600
        return (o in p15) or (o in o15) or (o in l15)
    def p15vote(ep5,sec,side):
        o=ep5-600; tsec=sec+600; a=0; opp=0; present=0
        for src in (p15,o15,l15):
            w=src.get(o)
            if not w: continue
            best=None;bd=999
            for s in w:
                if abs(s-tsec)<bd: bd=abs(s-tsec);best=s
            if best is not None and bd<=20:
                v=vote(w[best]); present+=1
                if v==side: a+=1
                elif v: opp+=1
        return a,opp,present

    eps=set(pred)&set(poly)&set(okx)
    base=[]   # part-3-aligned, 5m consensus, no 15m requirement
    joined=[] # + 15m part-3 agrees
    for ep in eps:
        if ep not in po: continue
        if not part3_aligned(ep): continue
        fired=False
        for sec in range(SMIN,SMAX+1):
            sn={'pred':pred[ep].get(sec),'lim':lim[ep].get(sec),'gem':gem[ep].get(sec),'okx':okx[ep].get(sec)}
            vs={nm:(vote(s),(s or {}).get('target')) for nm,s in sn.items()}
            for side in ('UP','DOWN'):
                ag=[(nm,t) for nm,(vv,t) in vs.items() if vv==side]
                if len(ag)<3: continue
                tgs=[t for _,t in ag if t is not None]
                if len(tgs)>=2 and (max(tgs)-min(tgs))>GAP: continue
                cands=[]
                for p,s in (('poly',poly[ep].get(sec)),('pred',sn['pred']),('lim',sn['lim'])):
                    if not s: continue
                    px=s.get('up') if side=='UP' else s.get('down')
                    if px and 0.01<px<0.99: cands.append((p,px))
                if not cands: continue
                plat,price=min(cands,key=lambda x:x[1])
                oc={'poly':po,'pred':pro,'lim':lo}[plat].get(ep)
                if oc is None: continue
                a,opp,pres=p15vote(ep,sec,side)
                base.append((price,oc==side,a,opp,pres))
                if a>=1 and opp==0: joined.append((price,oc==side))
                fired=True;break
            if fired: break

    def rep(rows,label):
        if not rows: print('  %-40s n=0'%label); return
        n=len(rows);w=sum(1 for r in rows if r[1])
        ap=statistics.mean([r[0] for r in rows])
        pls=[(1.0/r[0]-1.0-COMM) if r[1] else -(1.0+COMM) for r in rows]
        mean=statistics.mean(pls); sd=statistics.pstdev(pls)
        print('  %-40s n=%-3d win%%=%4.1f avgP=%.3f ROI/$1=%+.1f%% sd=%.2f'%(label,n,100*w/n,ap,mean*100,sd))

    print('PART-3-ONLY: 5-min windows aligned to a 15-min third part, thr=0.70')
    print('5m consensus = 3 of {pred,lim,okx,gem} (no poly vote, no kalshi); buy cheapest tradeable')
    print('='*86)
    rep([ (p,w) for (p,w,a,opp,pres) in base], 'A. part-3-aligned, 5m consensus only')
    rep([ (p,w) for (p,w,a,opp,pres) in base if pres>0], '   of which 15m part-3 has data')
    rep(joined, 'B. + 15m part-3 AGREES (the JOIN)')
    # price buckets within the join
    print('  price buckets within the JOIN:')
    for lab,a2,b2 in [('<0.60',0,0.60),('0.60-0.75',0.60,0.75),('>=0.75',0.75,1.0)]:
        rep([r for r in joined if a2<=r[0]<b2], '   join '+lab)

if __name__=='__main__':
    main()
