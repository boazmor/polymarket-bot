#!/usr/bin/env python3
"""Part-1 of the 15-min market as an EARLY, price-lowering leg for the 5-min trade.
Part 1 of a 15-min window [O, O+900] = [O, O+300], shares the START (and TARGET)
with the 5-min window [O, O+300]. So the 15m part-1 lean is an EARLY read on the
SAME target, available at the very start — before the 5-min consensus forms.
Question: if 15m part-1 already leans a side early, can we enter the 5-min trade
at sec 30/60 (cheap) instead of sec 90 (expensive), and on which tradeable platform?
Tradeable = Poly, Predict, Limitless. Commission 2%.
"""
import csv, statistics
from collections import Counter, defaultdict

THR=0.60; INVEST=2.0; COMM=0.02

POLY='/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT='/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED5='/root/data_predict_btc_5m/combined_per_second.csv'
LIM5='/root/data_limitless_btc_5m/combined_per_second.csv'
LIM5MK='/root/data_limitless_btc_5m/markets.csv'
PRED15='/root/data_predict_btc_15m/combined_per_second.csv'


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


def load5_multi(path, epc, sc, up, dn, secs):
    """Returns {ep: {sec: {'up','down'}}} for the listed secs."""
    out=defaultdict(dict)
    secset=set(secs)
    for r in csv.DictReader(open(path)):
        try: ep=int(r[epc]); s=int(r[sc])
        except: continue
        if s not in secset: continue
        out[ep][s]={'up':f(r.get(up)),'down':f(r.get(dn))}
    return out


def load_lim5_multi(secs):
    m={}
    for r in csv.DictReader(open(LIM5MK)):
        try: m[r['market_id']]=int(r['expirationTimestamp'])//1000-300
        except: pass
    out=defaultdict(dict); secset=set(secs)
    for r in csv.DictReader(open(LIM5)):
        ep=m.get(r.get('market_id'))
        if ep is None: continue
        try: es=int(r['epoch_sec'])
        except: continue
        s=es-ep
        if s not in secset: continue
        out[ep][s]={'up':f(r.get('best_ask')),'down':f(r.get('no_best_ask'))}
    return out


def load_pred15_part1(secs):
    """predict 15m, sec_from_open in secs (part 1 = low secs)."""
    out=defaultdict(dict); secset=set(secs)
    for r in csv.DictReader(open(PRED15)):
        try: oe=int(r['market_open_epoch']); s=int(r['sec_from_open'])
        except: continue
        if s not in secset: continue
        out[oe][s]={'up':f(r.get('yes_ask')),'down':f(r.get('no_ask_implied'))}
    return out


def vote(snap):
    if not snap: return None
    u=snap.get('up'); d=snap.get('down')
    uo=u is not None and u>=THR; do=d is not None and d>=THR
    if uo and not do: return 'UP'
    if do and not uo: return 'DOWN'
    return None


def cheapest(side, snaps):
    """snaps = list of (plat, snap). Return (plat, price) cheapest for side."""
    cands=[]
    for p,s in snaps:
        if not s: continue
        px=s.get('up') if side=='UP' else s.get('down')
        if px and 0.01<px<0.99: cands.append((p,px))
    return min(cands,key=lambda x:x[1]) if cands else (None,None)


def main():
    po=poly_outcomes()
    SECS=[30,60,90]
    poly5=load5_multi(POLY,'market_epoch','sec_from_start','up_ask','down_ask',SECS)
    pred5=load5_multi(PRED5,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied',SECS)
    lim5=load_lim5_multi(SECS)
    p15=load_pred15_part1([30,60])

    # universe: windows where pred5 & lim5 agree at sec 90 (the 5m fast pair side),
    # AND a 15m part-1 window exists at the same start.
    rows=[]
    for ep in set(pred5)&set(lim5)&set(p15)&set(po):
        p90=pred5[ep].get(90); l90=lim5[ep].get(90)
        sp=vote(p90); sl=vote(l90)
        if not sp or sp!=sl: continue  # need pred+lim agree at 90 = the trade side
        side=sp
        # 15m part-1 early lean
        e30=vote(p15[ep].get(30)); e60=vote(p15[ep].get(60))
        # prices for the side at each sec across tradeable platforms
        def snaps_at(sec): return [('poly',poly5[ep].get(sec)),('pred',pred5[ep].get(sec)),('lim',lim5[ep].get(sec))]
        c30=cheapest(side,snaps_at(30)); c60=cheapest(side,snaps_at(60)); c90=cheapest(side,snaps_at(90))
        rows.append({'ep':ep,'side':side,'won':po[ep]==side,
                     'e30':e30,'e60':e60,
                     'c30':c30,'c60':c60,'c90':c90})
    print(f'trade windows (pred+lim agree @90, with 15m part-1 + outcome): {len(rows)}\n')

    # PART A: does 15m part-1 early lean already match the eventual side?
    n=len(rows)
    e30_match=sum(1 for r in rows if r['e30']==r['side'])
    e60_match=sum(1 for r in rows if r['e60']==r['side'])
    e30_opp=sum(1 for r in rows if r['e30'] and r['e30']!=r['side'])
    e60_opp=sum(1 for r in rows if r['e60'] and r['e60']!=r['side'])
    print('PART A — 15m part-1 early lean vs eventual 5m side')
    print(f'  at sec30: matches {e30_match}/{n}  opposes {e30_opp}  silent {n-e30_match-e30_opp}')
    print(f'  at sec60: matches {e60_match}/{n}  opposes {e60_opp}  silent {n-e60_match-e60_opp}')
    print()

    # PART B: when 15m part-1 (sec60) matches, win rate + early price vs late price
    matched=[r for r in rows if r['e60']==r['side']]
    print('PART B — when 15m part-1 agrees @sec60: early entry economics')
    if matched:
        wins=sum(1 for r in matched if r['won'])
        print(f'  windows: {len(matched)}  win%: {100*wins/len(matched):.1f}')
        for tag in ('c30','c60','c90'):
            prices=[r[tag][1] for r in matched if r[tag][1] is not None]
            plats=Counter(r[tag][0] for r in matched if r[tag][0])
            if prices:
                avg=statistics.mean(prices)
                print(f'  {tag} avg cheapest price: {avg:.3f}  platform mix: {dict(plats)}')
        # net PnL entering at sec60 cheapest vs sec90 cheapest
        for tag,lbl in (('c60','enter @60'),('c90','enter @90')):
            net=0.0; cnt=0
            for r in matched:
                p=r[tag][1]
                if p is None: continue
                cost=INVEST*(1+COMM)
                net += (INVEST/p-cost) if r['won'] else -cost
                cnt+=1
            if cnt: print(f'  {lbl}: net ${net:+.2f} over {cnt}  per ${net/cnt:+.3f}')
    print()

    # PART C: platform mix of cheapest at each sec (all trade windows)
    print('PART C — where is the cheapest tradeable price, by sec (all windows)')
    for tag in ('c30','c60','c90'):
        plats=Counter(r[tag][0] for r in rows if r[tag][0])
        prices=[r[tag][1] for r in rows if r[tag][1] is not None]
        if prices:
            print(f'  {tag}: avg {statistics.mean(prices):.3f}  cheapest-platform mix: {dict(plats)}')
    print()
    print('Interpretation: if sec30/60 avg price < sec90, the 15m part-1 lets us enter earlier+cheaper.')
    print('If non-poly appears in early mix, the 15m signal lets us also buy on Predict/Limitless.')


if __name__=='__main__':
    main()
