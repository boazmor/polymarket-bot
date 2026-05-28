#!/usr/bin/env python3
"""Combine the two edges: entry-price band 0.55-0.70 AND the 15m part-3 4th leg.
Base: 3-of-4 fast @0.70, no time limit, buy cheapest tradeable (poly/pred/lim).
Then cross by price band and by 15m part-3 (predict15/okx15/lim15) agreement.
2% commission. Small-sample warning expected.
"""
import csv, statistics
from collections import defaultdict

THR=0.70; INVEST=2.0; COMM=0.02; GAP=200; SMIN=10; SMAX=295

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
LIM='/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK='/root/data_limitless_btc_5m/markets.csv'
GEM='/root/data_gemini_btc_5m/combined_per_second.csv'
KAL='/root/data_kalshi_btc_15m/combined_per_second.csv'
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
def load_allsec(path,epc,sc,up,dn,tg):
    out=defaultdict(dict)
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]);s=int(r[sc])
        except: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out
def load_lim_allsec(path,mk,up,dn,tg,off,maxs):
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
        if s<0 or s>maxs: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out
def load_kal():
    rows=defaultdict(list)
    for r in csv.DictReader(open(KAL)):
        try: es=int(r['epoch_sec']);oe=int(r['open_epoch']);ce=int(r['close_epoch'])
        except: continue
        rows[es].append({'up':f(r.get('yes_ask')),'down':f(r.get('no_ask')),'target':f(r.get('target_price')),'oe':oe,'ce':ce})
    return rows
def kal_at(rows,ep,sec):
    t=ep+sec
    for c in rows.get(t,[]):
        if c['oe']<=t<=c['ce']: return c
    return None
def vote(s,thr=THR):
    if not s: return None
    u=s.get('up');d=s.get('down')
    uo=u is not None and u>=thr;do=d is not None and d>=thr
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None

def main():
    po=poly_outs();pro=pred_outs();lo=lim_outs()
    poly=load_allsec(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
    pred=load_allsec(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    lim=load_lim_allsec(LIM,LIMMK,'best_ask','no_best_ask','target_price',300,320)
    gem=load_allsec(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
    kal=load_kal()
    p15=load_allsec(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    o15=load_allsec(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
    l15=load_lim_allsec(LIM15,LIM15MK,'best_ask','no_best_ask','target_price',900,960)

    def part3vote(ep,sec,side):
        o=ep-600; tsec=sec+600; agree=0; opp=0
        for src in (p15,o15,l15):
            w=src.get(o)
            if not w: continue
            best=None;bd=999
            for s in w:
                if abs(s-tsec)<bd: bd=abs(s-tsec);best=s
            if best is not None and bd<=20:
                v=vote(w[best])
                if v==side: agree+=1
                elif v: opp+=1
        return agree,opp

    eps=set(pred)&set(poly)
    trades=[]
    for ep in eps:
        if ep not in po: continue
        fired=False
        for sec in range(SMIN,SMAX+1):
            ps=pred[ep].get(sec);ls=lim[ep].get(sec);gs=gem[ep].get(sec);ks=kal_at(kal,ep,sec)
            votes={}
            for nm,snp in (('pred',ps),('lim',ls),('gem',gs),('kal',ks)):
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
                a3,o3=part3vote(ep,sec,side)
                trades.append((price,oc==side,a3,o3))
                fired=True;break
            if fired: break

    def rep(rows,label):
        if not rows: print('  %-40s n=0'%label); return
        n=len(rows);w=sum(1 for r in rows if r[1])
        ap=statistics.mean([r[0] for r in rows])
        pls=[(INVEST/r[0]-INVEST*(1+COMM)) if r[1] else -INVEST*(1+COMM) for r in rows]
        net=sum(pls);per=net/n
        print('  %-40s n=%-3d win%%=%4.1f avgP=%.3f net=%+6.2f per=%+.3f'%(label,n,100*w/n,ap,net,per))

    inband=lambda r: 0.55<=r[0]<0.70
    has15=lambda r: (r[2]+r[3])>0
    ag15=lambda r: r[2]>=1 and r[3]==0
    print('thr=0.70, 3-of-4 fast, no time limit, 2pct comm')
    print('='*82)
    rep(trades,'ALL 3+ agree')
    rep([r for r in trades if inband(r)],'PRICE 0.55-0.70')
    rep([r for r in trades if ag15(r)],'15m part-3 AGREE (any price)')
    rep([r for r in trades if inband(r) and ag15(r)],'PRICE 0.55-0.70 + 15m AGREE (combo)')
    rep([r for r in trades if inband(r) and has15(r)],'PRICE 0.55-0.70 + has 15m data')
    rep([r for r in trades if inband(r) and not has15(r)],'PRICE 0.55-0.70, no 15m data')

if __name__=='__main__':
    main()
