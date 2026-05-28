#!/usr/bin/env python3
"""Part-3 of 15-min markets vs the aligned 5-min window — Binance-based 15m platforms.
A 15-min window [O, O+900]; its part 3 [O+600, O+900] resolves WITH the 5-min
window that opens at O+600. We read the 15m platform's signal during part 3
(at ~90s into the aligned 5m window) and compare its target to the 5m target.
Hypothesis: when 15m target ~ 5m target, the 15m vote is a clean extra signal.
Platforms tested: Predict-15m, Limitless-15m, OKX-15m. Commission 2%, std dev.
"""
import csv, statistics
from collections import defaultdict

THR=0.60; INVEST=2.0; COMM=0.02
REF5=90          # decision sec inside the 5-min window
PART3_SEC=600+REF5  # = sec_from_open in the 15m window during part 3

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED5='/root/data_predict_btc_5m/combined_per_second.csv'
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


def load5(path, epc, sc, up, dn, tg, sec):
    out={}
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]); s=int(r[sc])
        except: continue
        if s!=sec: continue
        out[ep]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out


def load15_std(path, epc, sc, up, dn, tg, sec):
    """15m platform with market_open_epoch column. Returns open_epoch -> snap at part-3 sec."""
    out={}
    for r in csv.DictReader(open(path)):
        try: oe=int(r[epc]); s=int(r[sc])
        except: continue
        if s!=sec: continue
        out[oe]={'up':f(r.get(up)),'down':f(r.get(dn)),'target':f(r.get(tg))}
    return out


def load_lim15(sec):
    m={}
    for r in csv.DictReader(open(LIM15MK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-900  # 15-min open
        except: pass
    out={}
    for r in csv.DictReader(open(LIM15)):
        oe=m.get(r.get('market_id'))
        if oe is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        if es-oe!=sec: continue
        out[oe]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask')),'target':f(r.get('target_price'))}
    return out


def vote(snap):
    if not snap: return None
    u=snap.get('up'); d=snap.get('down')
    uo=u is not None and u>=THR; do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None


def netpnl(price, won):
    return (INVEST/price - INVEST*(1+COMM)) if won else -INVEST*(1+COMM)


def block(rows, label):
    if not rows: print(f'  {label:<34} n=0'); return
    wins=sum(1 for _,_,w in rows if w)
    pls=[netpnl(p,w) for _,p,w in rows]
    net=sum(pls); per=net/len(rows)
    sd=statistics.pstdev(pls) if len(pls)>1 else 0
    print(f'  {label:<34} n={len(rows):<3} win%={100*wins/len(rows):5.1f}  net=${net:+6.2f}  per=${per:+.3f}  sd=${sd:.2f}')


def main():
    po=poly_outcomes()
    poly5=load5(POLY,'market_epoch','sec_from_start','up_ask','down_ask','target_price',REF5)
    pred5=load5(PRED5,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike',REF5)

    p15=load15_std(PRED15,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied','strike',PART3_SEC)
    o15=load15_std(OKX15,'market_open_epoch','sec_from_open','up_ask','down_ask','target_price',PART3_SEC)
    l15=load_lim15(PART3_SEC)

    print(f'5m consensus windows available: {len(set(poly5)&set(pred5))}')
    print(f'15m part-3 snaps: predict={len(p15)} okx={len(o15)} limitless={len(l15)}')
    print(f'(reading 15m at sec_from_open={PART3_SEC} = {REF5}s into the aligned 5m window)\n')

    def consensus5(ep):
        p=poly5.get(ep); pr=pred5.get(ep)
        if not p or not pr: return None,None,None
        pu,pd=p.get('up'),p.get('down'); yu,yd=pr.get('up'),pr.get('down')
        if (pu and pu>=THR) and (yu and yu>=THR): side='UP'
        elif (pd and pd>=THR) and (yd and yd>=THR): side='DOWN'
        else: return None,None,None
        # buy cheapest of poly/pred (tradeable)
        pp=pu if side=='UP' else pd; yp=yu if side=='UP' else yd
        if pp is None: plat,price=('predict',yp)
        elif yp is None: plat,price=('poly',pp)
        else: plat,price=('poly',pp) if pp<=yp else ('predict',yp)
        return side,plat,price

    for name, snaps15 in [('PREDICT-15m (Binance)',p15),('LIMITLESS-15m (Binance)',l15),('OKX-15m (OKX idx)',o15)]:
        print('='*86)
        print(f'15m platform = {name}')
        print('='*86)
        aligned=[]
        for oe15, s15 in snaps15.items():
            ep5 = oe15 + 600  # aligned 5-min window opens 10 min into the 15m window
            side,plat,price = consensus5(ep5)
            if not side: continue
            oc = po.get(ep5) if plat=='poly' else None
            # resolve poly buys by poly outcome; pred buys: derive via pred5 target? use poly outcome as proxy
            if plat=='predict':
                oc = po.get(ep5)  # proxy; poly chainlink outcome (office: poly settles like fast)
            if oc is None: continue
            v15 = vote(s15)
            won = (oc==side)
            t15 = s15.get('target')
            t5 = (pred5.get(ep5) or {}).get('target')
            gap = abs(t15-t5) if (t15 is not None and t5 is not None) else None
            aligned.append((ep5, side, plat, price, won, v15, gap))
        print(f'aligned part-3 windows with 5m consensus: {len(aligned)}')
        block([(e,pr,w) for e,si,pl,pr,w,v,g in aligned], 'ALL aligned')
        block([(e,pr,w) for e,si,pl,pr,w,v,g in aligned if v==si], f'{name.split()[0]} AGREES')
        block([(e,pr,w) for e,si,pl,pr,w,v,g in aligned if v and v!=si], f'{name.split()[0]} DISAGREES')
        block([(e,pr,w) for e,si,pl,pr,w,v,g in aligned if v is None], f'{name.split()[0]} SILENT')
        # target gap when agrees
        agree=[(e,si,pl,pr,w,v,g) for e,si,pl,pr,w,v,g in aligned if v==si and g is not None]
        if agree:
            print('  -- AGREE, by 15m-vs-5m target gap --')
            block([(e,pr,w) for e,si,pl,pr,w,v,g in agree if g<20], '   target gap <20 (near-same bet)')
            block([(e,pr,w) for e,si,pl,pr,w,v,g in agree if 20<=g<60], '   target gap 20-60')
            block([(e,pr,w) for e,si,pl,pr,w,v,g in agree if g>=60], '   target gap 60+')
            gaps=[g for *_,g in agree]
            print(f'  median 15m-5m target gap (agree): ${statistics.median(gaps):.0f}')
        print()


if __name__=='__main__':
    main()
