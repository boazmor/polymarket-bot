#!/usr/bin/env python3
"""For each part-3 5m window today, replay the LIVE5 recipe:
- find the FIRST sec (10..295) where >=3 of {pred,lim,okx,gem} agree @0.70 (trio targets within 200)
- at that sec evaluate the 3 filters: dist>=20, >=1 15m part-3 confirm, |15m strike - 5m strike|<60
- determine cheapest buy (poly/pred/lim) + price
- resolve the actual outcome of the consensus side on the BOUGHT platform
- classify each window: FIRE (passed all) or BLOCKED_<filter>, and what the result WOULD have been
Answers: did our filters block winners or save us from losers?
"""
import csv, statistics
from collections import defaultdict
from datetime import datetime, timezone

THR=0.70; GAP=200.0; DMIN=20.0; TGT=60.0; COMM=1.02; INV=2.0
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

def la(path,epc,sc,up,dn,tg):
    out=defaultdict(dict)
    try:
        for r in csv.DictReader(open(path)):
            try: ep=int(r[epc]); s=int(r[sc])
            except: continue
            out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg)),
                        'bn':f(r.get('binance_price') or r.get('binance_now')),
                        'dist':f(r.get('distance_signed'))}
    except FileNotFoundError: pass
    return out

def lal(path,mk,up,dn,tg,off,maxs):
    m={}
    try:
        for r in csv.DictReader(open(mk)):
            try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-off
            except: pass
    except FileNotFoundError: pass
    out=defaultdict(dict)
    try:
        for r in csv.DictReader(open(path)):
            ep=m.get(r.get('market_id'))
            if ep is None: continue
            try: es=int(r['epoch_sec'])
            except: continue
            s=es-ep
            if 0<=s<=maxs: out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg)),
                                       'bn':f(r.get('binance_now'))}
    except FileNotFoundError: pass
    return out

def poly_outs():
    o={}
    try:
        for r in csv.DictReader(open(POLYOUT)):
            try:
                ep=int(r['market_epoch'])
                if r.get('winner_side') in ('UP','DOWN'): o[ep]=r['winner_side']
            except: pass
    except FileNotFoundError: pass
    return o

def derive_out(secs):
    # last binance vs target -> UP/DOWN
    if not secs: return None
    last=max(secs); bn=None; tg=None
    for s in sorted(secs):
        if secs[s].get('bn') is not None: bn=secs[s]['bn']
        if secs[s].get('target') is not None: tg=secs[s]['target']
    if bn is None or tg is None: return None
    return 'UP' if bn>tg else 'DOWN'

poly=la(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
pred=la(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
lim=lal(LIM,LIMMK,'best_ask','no_best_ask','target_price',300,320)
gem=la(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
okx=la(OKX,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
p15=la(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
o15=la(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price')
l15=lal(LIM15,LIM15MK,'best_ask','no_best_ask','target_price',900,960)
po=poly_outs()

def first_target(secs):
    for s in sorted(secs):
        t=secs[s].get('target')
        if t: return t
    return None
p15strike={ep:first_target(s) for ep,s in p15.items()}

def vote(snap,side):
    if not snap: return None
    return snap.get('up') if side=='UP' else snap.get('down')

def v15(secs,o15open,tsec,side):
    # nearest sec within 20 of tsec for the 15m window, vote if ask>=THR one-sided
    best=99999; u=d=None
    for s,row in secs.items():
        dd=abs(s-tsec)
        if dd<=20 and dd<best: best=dd; u=row.get('up'); d=row.get('down')
    if u is None and d is None: return None
    uo=u is not None and u>=THR; do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None

# part-3 windows present in poly data — ONLY since LIVE5 went live on Germany (15:59 UTC 2026-05-29)
LIVE_START=1780070100  # 15:55 UTC 2026-05-29
wins=sorted(w for w in poly if w%900==600 and w>=LIVE_START)
rows=[]
for w in wins:
    # find first consensus sec
    chosen=None
    for sec in range(10,296):
        for side in ('UP','DOWN'):
            ag=[]
            for nm,src in (('pred',pred),('lim',lim),('okx',okx),('gem',gem)):
                snap=src.get(w,{}).get(sec); a=vote(snap,side); t=(snap or {}).get('target')
                if a is not None and t is not None and a>=THR: ag.append((nm,t))
            if len(ag)>=3:
                tg=[t for _,t in ag]
                if max(tg)-min(tg)<=GAP:
                    chosen=(sec,side,ag); break
        if chosen: break
    if not chosen: continue
    sec,side,ag=chosen
    psnap=pred.get(w,{}).get(sec) or {}
    dist=psnap.get('dist'); strike5=psnap.get('target')
    o15open=w-600; tsec=sec+600
    n15=0
    for src in (p15,o15,l15):
        if v15(src.get(o15open,{}),o15open,tsec,side)==side: n15+=1
    strike15=p15strike.get(o15open)
    # cheapest buy
    cands=[]
    for p,src in (('poly',poly),('pred',pred),('lim',lim)):
        snap=src.get(w,{}).get(sec); a=vote(snap,side)
        if a and 0.01<a<0.99: cands.append((p,a))
    plat,price=min(cands,key=lambda x:x[1]) if cands else (None,None)
    # filter classification (same order as bot)
    block=None
    if dist is None or abs(dist)<DMIN: block='dist<20'
    elif n15<1: block='no_15m_confirm'
    elif strike15 is None or strike5 is None: block='no_tgt_data'
    elif abs(strike15-strike5)>=TGT: block='tgtsim>=60'
    # outcome on bought platform
    oc=None
    if plat=='poly': oc=po.get(w) or derive_out(poly.get(w,{}))
    elif plat=='pred': oc=derive_out(pred.get(w,{}))
    elif plat=='lim': oc=derive_out(lim.get(w,{}))
    won=(oc==side) if oc in ('UP','DOWN') else None
    net=None
    if won is True and price: net=INV/price-INV*COMM
    elif won is False: net=-INV*COMM
    rows.append({'w':w,'sec':sec,'side':side,'plat':plat,'price':price,'dist':dist,
                 'n15':n15,'tgtgap':(abs(strike15-strike5) if strike15 and strike5 else None),
                 'block':block,'oc':oc,'won':won,'net':net})

def hhmm(ep): return datetime.fromtimestamp(ep,tz=timezone.utc).strftime('%H:%M')

print('Part-3 windows with 5m consensus today: %d'%len(rows))
print('='*70)
fires=[r for r in rows if r['block'] is None]
blocked=[r for r in rows if r['block'] is not None]
print('WOULD-FIRE (passed all filters): %d'%len(fires))
for r in fires:
    print('  %s %s on %s @%.3f -> out=%s won=%s net=%s'%(hhmm(r['w']),r['side'],r['plat'],r['price'] or 0,r['oc'],r['won'],('%+.2f'%r['net']) if r['net'] is not None else 'NA'))
print()
print('BLOCKED by a filter: %d'%len(blocked))
for r in blocked:
    tg='%.0f'%r['tgtgap'] if r['tgtgap'] is not None else '-'
    d='%.0f'%abs(r['dist']) if r['dist'] is not None else '-'
    wb='WIN' if r['won'] else ('LOSS' if r['won'] is False else 'UNRESOLVED')
    print('  %s %s on %s @%s blocked=%-14s dist=%s 15m=%d tgtgap=%s -> WOULD %s net=%s'%(
        hhmm(r['w']),r['side'],r['plat'],('%.3f'%r['price']) if r['price'] else '-',
        r['block'],d,r['n15'],tg,wb,('%+.2f'%r['net']) if r['net'] is not None else 'NA'))
print()
# summary
def summ(label, rs):
    res=[r for r in rs if r['won'] is not None]
    w=sum(1 for r in res if r['won']); l=len(res)-w
    net=sum(r['net'] for r in res if r['net'] is not None)
    print('  %-22s n=%d resolved=%d would-WIN=%d would-LOSS=%d net=%+.2f'%(label,len(rs),len(res),w,l,net))
print('SUMMARY')
summ('would-fire', fires)
summ('blocked-ALL', blocked)
for b in ('tgtsim>=60','dist<20','no_15m_confirm','no_tgt_data'):
    summ('blocked-'+b, [r for r in blocked if r['block']==b])
