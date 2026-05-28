#!/usr/bin/env python3
"""FIRST third (part 1) of the 15-min window, mirroring the part-3 analysis.
Part 2 of 15m window [O,O+900] = [O+300,O+600], aligns to the 5-min window opening at O+300.
NOTE: unlike part 3, the 5-min resolves at O+600 but the 15-min keeps going to O+900,
so they do NOT share a resolution moment. The 15m part-2 lean is a longer-horizon hint.
Same checks: clean consensus, distance, 15m-5m target similarity, 15m confirmation.
"""
import csv, statistics
from collections import defaultdict, Counter
THR=0.70; COMM=0.02; GAP=200; SMIN=10; SMAX=295; INVEST=10.0
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
def la(path,epc,sc,up,dn,tg,bn=None):
    out=defaultdict(dict)
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]);s=int(r[sc])
        except: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg)),'bn':f(r.get(bn)) if bn else None}
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
po=poly_outs();pro=pred_outs();lo=lim_outs()
poly=la(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price','binance_price')
pred=la(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike','binance_now')
lim=lal(LIM,LIMMK,'best_ask','no_best_ask','target_price',300,320)
gem=la(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
okx=la(OKX,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
p15=la(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
o15=la(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
l15=lal(LIM15,LIM15MK,'best_ask','no_best_ask','target_price',900,960)
def ft(secs):
    for s in sorted(secs):
        t=secs[s].get('target')
        if t: return t
    return None
p15s={ep:ft(secs) for ep,secs in p15.items()}; p5s={ep:ft(secs) for ep,secs in pred.items()}
# PART 1: 15m open = ep5 (SAME start); read at sec_from_open = 5m_sec
def aligned(ep):
    o=ep; return (o in p15) or (o in o15) or (o in l15)
def leg15(ep,sec,side):
    o=ep;ts=sec;conf=[]
    for nm,src in (('pred15',p15),('okx15',o15),('lim15',l15)):
        w=src.get(o)
        if not w: continue
        best=None;bd=999
        for s in w:
            if abs(s-ts)<bd: bd=abs(s-ts);best=s
        if best is not None and bd<=20 and vote(w[best])==side: conf.append(nm)
    return conf
eps=set(pred)&set(poly)&set(okx)
trades=[]; lo_e=hi_e=None
for ep in eps:
    if ep not in po or not aligned(ep): continue
    fired=False
    for sec in range(SMIN,SMAX+1):
        sn={'pred':pred[ep].get(sec),'lim':lim[ep].get(sec),'gem':gem[ep].get(sec),'okx':okx[ep].get(sec)}
        vs={nm:(vote(s),(s or {}).get('target')) for nm,s in sn.items()}
        for side in ('UP','DOWN'):
            ag=[(nm,t) for nm,(vv,t) in vs.items() if vv==side]
            if len(ag)<3: continue
            tgs=[t for _,t in ag if t is not None]
            if len(tgs)>=2 and (max(tgs)-min(tgs))>GAP: continue
            ps=sn['pred']; dist=None
            if ps and ps.get('target') is not None and ps.get('bn') is not None: dist=abs(ps['bn']-ps['target'])
            cands=[]
            for p,s in (('poly',poly[ep].get(sec)),('pred',sn['pred']),('lim',sn['lim'])):
                if not s: continue
                px=s.get('up') if side=='UP' else s.get('down')
                if px and 0.01<px<0.99: cands.append((p,px))
            if not cands: continue
            plat,price=min(cands,key=lambda x:x[1])
            oc={'poly':po,'pred':pro,'lim':lo}[plat].get(ep)
            if oc is None: continue
            conf=leg15(ep,sec,side)
            t15=p15s.get(ep); t5=p5s.get(ep)
            sim=abs(t15-t5) if (t15 is not None and t5 is not None) else None
            trades.append({'won':oc==side,'price':price,'dist':dist,'conf':conf,'sim':sim})
            if lo_e is None or ep<lo_e: lo_e=ep
            if hi_e is None or ep>hi_e: hi_e=ep
            fired=True;break
        if fired: break
span=(hi_e-lo_e)/86400 if (lo_e and hi_e) else 0.7
def rep(rows,label):
    if not rows: print('  %-28s n=0'%label); return
    n=len(rows);w=sum(1 for t in rows if t['won'])
    pls=[(INVEST/t['price']-INVEST*(1+COMM)) if t['won'] else -INVEST*(1+COMM) for t in rows]
    net=sum(pls);per=net/n
    print('  %-28s n=%-3d win%%=%4.1f per=$%+.2f tot=$%+.1f ~$%+.0f/day'%(label,n,100*w/n,per,net,per*(n/span)))
print('FIRST third (part 1) of 15m, $%g/trade, span %.2f days'%(INVEST,span))
print('='*74)
base=[t for t in trades]
d20=[t for t in trades if t['dist'] is None or t['dist']>=20]
leg4=[t for t in d20 if len(t['conf'])>=1]
fin=[t for t in d20 if len(t['conf'])>=1 and (t['sim'] is not None and t['sim']<60)]
rep(base,'part-1 + consensus')
rep(d20,'+ dist>=20')
rep(leg4,'+ 15m part-1 confirms')
rep(fin,'+ tgtsim<60 (full recipe)')
print()
print('target similarity in part-2 (gap should be SMALLER, only 5 min apart):')
sims=[t['sim'] for t in base if t['sim'] is not None]
if sims: print('  median 15m-5m target gap: $%.1f (part-3 ~60, part-2 ~49)'%statistics.median(sims))
