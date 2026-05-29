#!/usr/bin/env python3
"""Apply the 2 mandatory filters (distance>=20, 15m-5m target sim<60) to the LIVE
V3.2 part-3 trades, with a dollar comparison at $2 and $10."""
import csv, statistics
from collections import defaultdict
COMM=0.02
TR='/root/live/consensus_v3_2/consensus_v3_2_trades.csv'
OUT='/root/live/consensus_v3_2/consensus_v3_2_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
PRED15='/root/data_predict_btc_15m/combined_per_second.csv'
def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None
# pred5: binance_now & strike per (window, sec); strike per window
pred5_bn=defaultdict(dict); pred5_strike={}
for r in csv.DictReader(open(PRED)):
    try: ep=int(r['market_open_epoch']); s=int(r['sec_from_open'])
    except: continue
    bn=f(r.get('binance_now')); tg=f(r.get('strike'))
    if bn is not None: pred5_bn[ep][s]=bn
    if tg is not None and ep not in pred5_strike: pred5_strike[ep]=tg
pred15_strike={}
for r in csv.DictReader(open(PRED15)):
    try: ep=int(r['market_open_epoch'])
    except: continue
    tg=f(r.get('strike'))
    if tg is not None and ep not in pred15_strike: pred15_strike[ep]=tg
outs={r['window_epoch']:r for r in csv.DictReader(open(OUT)) if r.get('window_epoch')}
def oc(o,p): return {'poly':o.get('poly_outcome'),'predict':o.get('pred_outcome'),'lim':o.get('lim_outcome')}.get(p) if o else None

trades=[]
for r in csv.DictReader(open(TR)):
    ep=int(r['window_epoch'])
    if ep%900!=600: continue  # part-3 aligned only
    res=oc(outs.get(str(ep)),r['platform'])
    if not res: continue
    sec=int(r.get('sec_now') or 90)
    # distance: |binance(ep,sec) - strike(ep)|
    bn=pred5_bn.get(ep,{}).get(sec)
    if bn is None:
        # nearest sec
        cand={s:v for s,v in pred5_bn.get(ep,{}).items()}
        if cand:
            ns=min(cand,key=lambda s:abs(s-sec)); bn=cand[ns]
    strike=pred5_strike.get(ep)
    dist=abs(bn-strike) if (bn is not None and strike is not None) else None
    t15=pred15_strike.get(ep-600); t5=pred5_strike.get(ep)
    sim=abs(t15-t5) if (t15 is not None and t5 is not None) else None
    trades.append({'price':float(r['price']),'won':res==r['side'],'dist':dist,'sim':sim})

def rep(rows,label,inv):
    if not rows: print('  %-30s n=0'%label); return (0,0)
    n=len(rows);w=sum(1 for t in rows if t['won'])
    pls=[(inv/t['price']-inv*(1+COMM)) if t['won'] else -inv*(1+COMM) for t in rows]
    net=sum(pls)
    print('  %-30s n=%-3d win%%=%4.1f net=$%+7.2f per=$%+.3f'%(label,n,100*w/n,net,net/n))
    return (n,net)

print('LIVE part-3 trades with mandatory filters (resolved only). $2 then $10:')
for inv in (2.0,10.0):
    print('=== $%g per trade ==='%inv)
    base=trades
    d=[t for t in trades if t['dist'] is not None and t['dist']>=20]
    s=[t for t in trades if t['sim'] is not None and t['sim']<60]
    both=[t for t in trades if (t['dist'] is not None and t['dist']>=20) and (t['sim'] is not None and t['sim']<60)]
    rep(base,'part-3 raw',inv)
    rep(d,'+ dist>=20',inv)
    rep(s,'+ tgtsim<60',inv)
    n_both,net_both=rep(both,'+ BOTH filters (safe set)',inv)
    print()
print('SIZE-UP strategy (dollars): $2 base on all part-3 + extra $8 (=$10) on the BOTH-filter safe set')
base_net=sum((2/t['price']-2*1.02) if t['won'] else -2*1.02 for t in trades)
both=[t for t in trades if (t['dist'] is not None and t['dist']>=20) and (t['sim'] is not None and t['sim']<60)]
extra_net=sum((8/t['price']-8*1.02) if t['won'] else -8*1.02 for t in both)
print('  base $2 on %d part-3 trades: $%+.2f'%(len(trades),base_net))
print('  +extra $8 on %d safe trades: $%+.2f'%(len(both),extra_net))
print('  combined: $%+.2f'%(base_net+extra_net))
