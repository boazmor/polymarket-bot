#!/usr/bin/env python3
import csv
from datetime import datetime, timezone
D='/root/live/consensus_v2/'
trades=list(csv.DictReader(open(D+'consensus_v1_trades.csv')))
outs={r['window_epoch']:r for r in csv.DictReader(open(D+'consensus_v1_outcomes.csv')) if r.get('window_epoch')}
CUT=datetime(2026,5,27,19,21,0,tzinfo=timezone.utc)
INV=2.0; COMM=0.02
ta=[t for t in trades if datetime.fromisoformat(t['ts_utc'])>=CUT]
wins=losses=pend=0; net=0.0; wp=0.0; lp=0.0; prices=[]
for t in ta:
    ep=t['window_epoch']; side=t['side']; price=float(t['price']); prices.append(price)
    o=outs.get(ep)
    if not o or not o.get('poly_outcome'): pend+=1; continue
    cost=INV*(1+COMM)
    if o['poly_outcome']==side: pnl=INV/price-cost; wins+=1; wp+=pnl
    else: pnl=-cost; losses+=1; lp+=pnl
    net+=pnl
res=wins+losses
hrs=(datetime.fromisoformat(ta[-1]['ts_utc'])-CUT).total_seconds()/3600 if ta else 0
print('V2 since hour-filter removal (27/05 19:21 UTC)')
print('span hours: %.1f' % hrs)
print('trades placed: %d  resolved: %d  pending: %d' % (len(ta),res,pend))
if res:
    print('wins %d  losses %d  win%% %.1f' % (wins,losses,100*wins/res))
    print('avg buy price: %.3f' % (sum(prices)/len(prices)))
    print('net after 2%% comm: $%+.2f   per-trade $%+.3f' % (net, net/res))
    print('winners +$%.2f  losers -$%.2f' % (wp, abs(lp)))
    be=(sum(prices)/len(prices))*1.02*100
    print('break-even win%% at 2%% cost: %.1f' % be)
if hrs>0:
    print('rate: %.2f/hour  %.1f/day' % (len(ta)/hrs, len(ta)/(hrs/24)))
