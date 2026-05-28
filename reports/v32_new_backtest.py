#!/usr/bin/env python3
"""Backtest of the NEW V3.2 config:
 - fast platforms: Predict5, Limitless5, Gemini5, Kalshi15(concurrent) -- need >=3 agree
 - ask threshold 0.60 (lowered)
 - NO time limit: scan sec 10..295, fire on FIRST match
 - targets of the agreeing trio within $200
 - buy cheapest of TRADEABLE platforms (poly, pred, lim)
 - 2% commission, std dev
Reports recording span per source and the backtest outcome.
"""
import csv, statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone

THR=0.60; INVEST=2.0; COMM=0.02; GAP=200
SMIN=10; SMAX=295

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
    out={}
    for mid,ep in m.items():
        t=tg.get(mid);fin=lb.get(mid)
        if t is not None and fin is not None: out[ep]='UP' if fin>t else 'DOWN'
    return out


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


def load_kal_byes():
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


def span(path,epc):
    eps=set()
    for r in csv.DictReader(open(path)):
        try: eps.add(int(r[epc]))
        except: pass
    if not eps: return None
    return min(eps),max(eps),len(eps)


def main():
    print('=== recording spans ===')
    for name,path,epc in [('poly5',POLY,'market_epoch'),('pred5',PRED,'market_open_epoch'),
                          ('gem5',GEM,'market_open_epoch'),('kalshi15',KAL,'open_epoch')]:
        sp=span(path,epc)
        if sp:
            lo,hi,n=sp
            print(f'  {name:9s}: {datetime.fromtimestamp(lo,tz=timezone.utc):%m-%d %H:%M} -> {datetime.fromtimestamp(hi,tz=timezone.utc):%m-%d %H:%M}  {(hi-lo)/3600:.1f}h  {n} windows')
    print()

    po=poly_outs(); pro=pred_outs(); lo=lim_outs()
    poly=load_allsec(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price')
    pred=load_allsec(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike')
    lim=load_lim_allsec()
    gem=load_allsec(GEM,'market_open_epoch','sec_from_open','best_ask','no_best_ask','target_price')
    kal=load_kal_byes()

    eps=set(pred)&set(poly)  # need poly to buy + pred as anchor
    trades=[]
    for ep in eps:
        if ep not in po: continue
        fired=False
        for sec in range(SMIN,SMAX+1):
            ps=pred[ep].get(sec); ls=lim[ep].get(sec); gs=gem[ep].get(sec); ks=kal_at(kal,ep,sec)
            votes={}
            for nm,snp in (('pred',ps),('lim',ls),('gem',gs),('kal',ks)):
                v=vote(snp)
                if v: votes[nm]=(v,snp.get('target'))
            # need >=3 agree same side with targets within GAP
            for side in ('UP','DOWN'):
                ag=[(nm,t) for nm,(vv,t) in votes.items() if vv==side]
                if len(ag)<3: continue
                tg=[t for _,t in ag if t is not None]
                if len(tg)>=2 and (max(tg)-min(tg))>GAP: continue
                # buy cheapest tradeable
                cands=[]
                for p,snp in (('poly',poly[ep].get(sec)),('pred',ps),('lim',ls)):
                    if not snp: continue
                    px=snp.get('up') if side=='UP' else snp.get('down')
                    if px and 0.01<px<0.99: cands.append((p,px))
                if not cands: continue
                plat,price=min(cands,key=lambda x:x[1])
                oc={'poly':po,'pred':pro,'lim':lo}[plat].get(ep)
                if oc is None: continue
                trades.append((ep,sec,side,plat,price,oc==side,len(ag)))
                fired=True; break
            if fired: break

    print('=== NEW V3.2 backtest (ask 0.60, no time limit, 3-of-4 fast, buy cheapest tradeable) ===')
    if not trades:
        print('no trades'); return
    eps_t=[t[0] for t in trades]
    lo_t,hi_t=min(eps_t),max(eps_t); hrs=(hi_t-lo_t)/3600
    wins=sum(1 for *_,w,_ in trades if w)
    pls=[(INVEST/pr-INVEST*(1+COMM)) if w else -INVEST*(1+COMM) for _,_,_,_,pr,w,_ in trades]
    net=sum(pls);per=net/len(trades);sd=statistics.pstdev(pls) if len(pls)>1 else 0
    avgp=statistics.mean([pr for *_,pr,w,_ in [(t[0],t[1],t[2],t[3],t[4],t[5],t[6]) for t in trades]])
    avgp=statistics.mean([t[4] for t in trades])
    print(f'effective backtest span: {hrs:.1f}h ({hrs/24:.1f} days)  [limited by gemini+kalshi overlap]')
    print(f'trades: {len(trades)}  win%: {100*wins/len(trades):.1f}  avg_price: {avgp:.3f}')
    print(f'net after 2% comm: ${net:+.2f}  per-trade: ${per:+.3f}  sd: ${sd:.2f}')
    be=avgp*(1+COMM)*100
    print(f"break-even win% at 2% cost: {be:.1f}  -> {'PROFIT' if 100*wins/len(trades)>be else 'loss'}")
    if hrs>0: print(f'rate: {len(trades)/hrs:.2f}/hour  {len(trades)/(hrs/24):.1f}/day')
    bp=Counter(t[3] for t in trades); ns=Counter(t[6] for t in trades)
    print(f'buy platform: {dict(bp)}   agree-count: {dict(ns)}')
    sec_b=Counter('early(10-60)' if t[1]<60 else 'mid(60-180)' if t[1]<180 else 'late(180-295)' for t in trades)
    print(f'fire timing: {dict(sec_b)}')


if __name__=='__main__':
    main()
