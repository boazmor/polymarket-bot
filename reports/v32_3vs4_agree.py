#!/usr/bin/env python3
"""Backtest at thr 0.70: compare 3-of-4 fast agreement vs 4-of-4 fast agreement.
Fast platforms: Predict5, Limitless5, Gemini5, Kalshi15(concurrent).
Buy cheapest TRADEABLE (poly/pred/lim). No time limit (first match sec 10-295).
Question: does 4-of-4 agreement justify a LOWER entry price (stronger conviction)?
Reports win%, avg price, per-trade for 3-agree vs 4-agree, plus price buckets.
2% commission.
"""
import csv, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone

THR=0.70; INVEST=2.0; COMM=0.02; GAP=200; SMIN=10; SMAX=295

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
LIM='/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK='/root/data_limitless_btc_5m/markets.csv'
GEM='/root/data_gemini_btc_5m/combined_per_second.csv'
KAL='/root/data_kalshi_btc_15m/combined_per_second.csv'


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
def vote(s):
    if not s: return None
    u=s.get('up');d=s.get('down')
    uo=u is not None and u>=THR;do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None

def main():
    po=poly_outs();pro=pred_outs();lo=lim_outs()
    poly=load_allsec(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
    pred=load_allsec(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    lim=load_lim_allsec()
    gem=load_allsec(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
    kal=load_kal()
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
                trades.append((side,plat,price,oc==side,len(ag),sec))
                fired=True;break
            if fired: break

    def rep(rows,label):
        if not rows: print('  %-26s n=0'%label); return
        n=len(rows);w=sum(1 for r in rows if r[3])
        ap=statistics.mean([r[2] for r in rows])
        pls=[(INVEST/r[2]-INVEST*(1+COMM)) if r[3] else -INVEST*(1+COMM) for r in rows]
        net=sum(pls);per=net/n;sd=statistics.pstdev(pls) if n>1 else 0
        be=ap*(1+COMM)*100
        print('  %-26s n=%-4d win%%=%4.1f avgP=%.3f net=%+7.2f per=%+.3f sd=%.2f BE=%.1f'%(label,n,100*w/n,ap,net,per,sd,be))

    print('thr=0.70, no time limit, buy cheapest tradeable, 2pct comm')
    print('='*88)
    rep(trades,'3+ agree (all)')
    rep([t for t in trades if t[4]==3],'exactly 3 agree')
    rep([t for t in trades if t[4]>=4],'4 agree (all 4 fast)')
    print()
    print('price buckets for 4-agree (does 4-agree allow cheaper entries that still win?):')
    four=[t for t in trades if t[4]>=4]
    for lab,lo2,hi2 in [('<0.55',0,0.55),('0.55-0.70',0.55,0.70),('0.70-0.85',0.70,0.85),('>=0.85',0.85,1.0)]:
        rep([t for t in four if lo2<=t[2]<hi2],'4-agree '+lab)
    print()
    print('price buckets for exactly-3-agree (for comparison):')
    three=[t for t in trades if t[4]==3]
    for lab,lo2,hi2 in [('<0.55',0,0.55),('0.55-0.70',0.55,0.70),('0.70-0.85',0.70,0.85),('>=0.85',0.85,1.0)]:
        rep([t for t in three if lo2<=t[2]<hi2],'3-agree '+lab)

if __name__=='__main__':
    main()
