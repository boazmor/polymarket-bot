#!/usr/bin/env python3
"""Backfill blank pred_outcome / lim_outcome in consensus_v3_2_outcomes.csv from the
recorders. The live bot's final-snapshot sometimes runs before the Predict/Limitless
recorder has flushed the window's last rows, leaving the outcome blank even though the
window resolved. This re-derives them (strike/target vs final binance) and rewrites
the file atomically. Safe to run on a cron; does not touch the live bot.
"""
import csv, os, tempfile

OUT='/root/live/consensus_v3_2/consensus_v3_2_outcomes.csv'
PRED='/root/data_predict_btc_5m/combined_per_second.csv'
LIM='/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK='/root/data_limitless_btc_5m/markets.csv'

def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None

def pred_outcomes():
    lastbn={}; strike={}
    for r in csv.DictReader(open(PRED)):
        try: ep=int(r['market_open_epoch'])
        except: continue
        bn=f(r.get('binance_now')); tg=f(r.get('strike'))
        if bn is not None: lastbn[ep]=bn
        if tg is not None: strike[ep]=tg
    return {ep:('UP' if lastbn[ep]>s else 'DOWN') for ep,s in strike.items() if ep in lastbn}

def lim_outcomes():
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

def main():
    if not os.path.exists(OUT):
        print('no outcomes file'); return
    po=pred_outcomes(); lo=lim_outcomes()
    with open(OUT) as fh:
        rows=list(csv.DictReader(fh)); hdr=rows[0].keys() if rows else []
    if not rows:
        print('empty'); return
    hdr=list(rows[0].keys())
    filled_p=filled_l=0
    for r in rows:
        try: ep=int(r['window_epoch'])
        except: continue
        if (not r.get('pred_outcome')) and ep in po:
            r['pred_outcome']=po[ep]; filled_p+=1
        if 'lim_outcome' in r and (not r.get('lim_outcome')) and ep in lo:
            r['lim_outcome']=lo[ep]; filled_l+=1
    # atomic rewrite
    d=os.path.dirname(OUT)
    fd,tmp=tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(fd,'w',newline='') as fh:
        wr=csv.DictWriter(fh, fieldnames=hdr); wr.writeheader(); wr.writerows(rows)
    os.replace(tmp, OUT)
    print('backfilled pred=%d lim=%d'%(filled_p,filled_l))

if __name__=='__main__':
    main()
