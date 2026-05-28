#!/usr/bin/env python3
"""Cross-reference actual V3.2 5-min trades with the concurrent 15-min PART-3 signal.
For each 5-min trade in window O, the concurrent 15-min window opened at O-600 and
is in its part 3 [O, O+300], resolving WITH our 5-min window. We read that 15m
window's part-3 vote + target and ask:
  - match: did 15m part-3 agree with the trade side?
  - did the target gap interfere?
  - additional trades: windows where 15m part-3 had consensus but 5-min did NOT trade?
  - price: was a cheaper tradeable platform available?
"""
import csv, statistics
from collections import Counter, defaultdict

THR=0.60; INVEST=2.0; COMM=0.02

V32T='/root/live/consensus_v3_2/consensus_v3_2_trades.csv'
POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED5='/root/data_predict_btc_5m/combined_per_second.csv'
LIM5='/root/data_limitless_btc_5m/combined_per_second.csv'
LIM5MK='/root/data_limitless_btc_5m/markets.csv'
PRED15='/root/data_predict_btc_15m/combined_per_second.csv'
LIM15='/root/data_limitless_btc_15m/combined_per_second.csv'
LIM15MK='/root/data_limitless_btc_15m/markets.csv'
OKX15='/root/data_okx_btc_15m/combined_per_second.csv'


def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None


def poly_outcomes():
    out={}
    for r in csv.DictReader(open(POLYOUT)):
        try:
            ep=int(r['market_epoch'])
            if r.get('winner_side') in ('UP','DOWN'): out[ep]=r['winner_side']
        except: pass
    return out


def load5_at(path, epc, sc, up, dn, sec):
    out={}
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]); s=int(r[sc])
        except: continue
        if s!=sec: continue
        out[ep]={'up':f(r.get(up)),'down':f(r.get(dn))}
    return out


def load_lim5_at(sec):
    m={}
    for r in csv.DictReader(open(LIM5MK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-300
        except: pass
    out={}
    for r in csv.DictReader(open(LIM5)):
        ep=m.get(r.get('market_id'))
        if ep is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        if es-ep!=sec: continue
        out[ep]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask'))}
    return out


def load15_part3_std(path, epc, sc, up, dn, tg, part3_sec):
    """15m window keyed by open. Returns {window_close5: snap} where the part-3
    aligns to 5m window opening at open+600. Key by open+600 for easy join."""
    out={}
    for r in csv.DictReader(open(path)):
        try: oe=int(r[epc]); s=int(r[sc])
        except: continue
        if s!=part3_sec: continue
        out[oe+600]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out


def load_lim15_part3(part3_sec):
    m={}
    for r in csv.DictReader(open(LIM15MK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-900
        except: pass
    out={}
    for r in csv.DictReader(open(LIM15)):
        oe=m.get(r.get('market_id'))
        if oe is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        if es-oe!=part3_sec: continue
        out[oe+600]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask')),'target':f(r.get('target_price'))}
    return out


def vote(snap):
    if not snap: return None
    u=snap.get('up'); d=snap.get('down')
    uo=u is not None and u>=THR; do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None


def main():
    po=poly_outcomes()
    REF5=90; PART3=600+REF5
    # 5m prices at sec 90 for cheapest-platform check
    poly5=load5_at(POLY,'market_epoch','sec_from_start','up_ask','down_ask',REF5)
    pred5=load5_at(PRED5,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied',REF5)
    lim5=load_lim5_at(REF5)
    # 15m part-3 signals keyed by aligned 5m-window-open (= 15m open + 600)
    p15=load15_part3_std(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike',PART3)
    o15=load15_part3_std(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price',PART3)
    l15=load_lim15_part3(PART3)
    # 5m target (pred strike) at sec 90 for target-gap
    pred5_t={}
    for r in csv.DictReader(open(PRED5)):
        try:
            ep=int(r['market_open_epoch']); s=int(r['sec_from_open'])
        except: continue
        if s==1:
            t=f(r.get('strike'))
            if t is not None: pred5_t[ep]=t

    trades=[t for t in csv.DictReader(open(V32T))]
    print(f'V3.2 trades on file: {len(trades)}\n')

    print('PART 1 — each actual V3.2 trade vs concurrent 15m part-3 signal')
    print('%-19s %-4s %-5s %-6s %-7s | %-5s %-5s %-5s  tgtgap'%('time','side','plat','price','outcome','p15','l15','o15'))
    match=Counter(); rows=[]
    for t in trades:
        ep=int(t['window_epoch']); side=t['side']; plat=t['platform']; price=float(t['price'])
        oc=po.get(ep)
        vp=vote(p15.get(ep)); vl=vote(l15.get(ep)); vo=vote(o15.get(ep))
        # target gap: 15m predict target vs 5m pred target
        t15=(p15.get(ep) or {}).get('target'); t5=pred5_t.get(ep)
        gap=abs(t15-t5) if (t15 is not None and t5 is not None) else None
        votes=[v for v in (vp,vl,vo) if v]
        agree=sum(1 for v in votes if v==side); opp=sum(1 for v in votes if v!=side)
        if votes:
            if opp==0 and agree>0: match['all-agree']+=1
            elif agree==0 and opp>0: match['all-oppose']+=1
            else: match['mixed']+=1
        else: match['no-15m-data']+=1
        gp = '%.0f'%gap if gap is not None else 'NA'
        print('%-19s %-4s %-5s %-6.3f %-7s | %-5s %-5s %-5s  %s'%(
            t['ts_utc'][11:19],side,plat,price,oc or 'pend',str(vp),str(vl),str(vo),gp))
        rows.append((ep,side,plat,price,oc,vp,vl,vo,gap))
    print()
    print('match summary:', dict(match))
    print()

    # PART 2 — did 15m part-3 agreement coincide with WINS? (on resolved trades)
    print('PART 2 — on resolved trades, 15m part-3 stance vs win/loss')
    for stance,label in [('agree','15m part-3 had >=1 agree, 0 oppose'),
                         ('oppose','15m part-3 had >=1 oppose, 0 agree')]:
        sub=[]
        for ep,side,plat,price,oc,vp,vl,vo,gap in rows:
            if oc is None: continue
            votes=[v for v in (vp,vl,vo) if v]
            ag=sum(1 for v in votes if v==side); op=sum(1 for v in votes if v!=side)
            if stance=='agree' and ag>0 and op==0: sub.append((side,price,oc==side))
            if stance=='oppose' and op>0 and ag==0: sub.append((side,price,oc==side))
        if sub:
            w=sum(1 for *_,won in sub if won)
            print(f'  {label}: n={len(sub)} win%={100*w/len(sub):.0f}')
        else:
            print(f'  {label}: n=0')
    print()

    # PART 3 — additional trades: windows with 15m part-3 consensus where 5m did NOT trade
    traded_eps={int(t['window_epoch']) for t in trades}
    all15=set(p15)|set(l15)|set(o15)
    extra=0
    for ep in all15:
        if ep in traded_eps: continue
        if ep not in po: continue
        votes=[v for v in (vote(p15.get(ep)),vote(l15.get(ep)),vote(o15.get(ep))) if v]
        if len(votes)>=2 and len(set(votes))==1:
            extra+=1
    print(f'PART 3 — windows with >=2 15m part-3 platforms agreeing where V3.2 did NOT trade: {extra}')
    print('  (these are candidate ADDITIONAL trades the 15m part-3 leg could open)')
    print()

    # PART 4 — price: for traded windows, cheapest tradeable 5m price at sec90 vs what we paid
    print('PART 4 — could we have bought cheaper on another tradeable platform @sec90?')
    cheaper=0; same=0
    for ep,side,plat,price,oc,vp,vl,vo,gap in rows:
        cands=[]
        for p,snap in [('poly',poly5.get(ep)),('pred',pred5.get(ep)),('lim',lim5.get(ep))]:
            if not snap: continue
            px=snap.get('up') if side=='UP' else snap.get('down')
            if px and 0.01<px<0.99: cands.append((p,px))
        if not cands: continue
        bp,bprice=min(cands,key=lambda x:x[1])
        if bprice < price-0.005: cheaper+=1
        else: same+=1
    print(f'  trades where a cheaper tradeable platform existed @sec90: {cheaper}  (already-cheapest: {same})')


if __name__=='__main__':
    main()
