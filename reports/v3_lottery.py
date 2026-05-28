#!/usr/bin/env python3
"""LOTTERY / contrarian on the CHEAP side in STRONG-CONSENSUS windows.
When Poly+Pred strongly agree (e.g. UP), the opposite (DOWN) side is cheap.
Buy the cheap opposite side as a lottery ticket. If consensus is wrong even
15-20% of the time, the high payout can be profitable.
Full accounting: 2% commission, standard deviation, per/sd.
Tests entry at multiple seconds (cheap side is cheapest late in the window).
"""
import sys, csv, statistics
from collections import defaultdict

THR = 0.60
INVEST = 2.0
COMMISSION = 0.02
SECS = [60, 90, 120, 180, 240, 270, 290]

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'


def f(v):
    if v in (None,'','None'): return None
    try: return float(v)
    except: return None


def load_poly_outcomes():
    out = {}
    with open(POLYOUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_epoch'])
                if r.get('winner_side') in ('UP','DOWN'): out[ep] = r['winner_side']
            except: pass
    return out


def load_per_sec(path, key_open, sec_col, up_col, down_col):
    out = defaultdict(dict)
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try: ep = int(r[key_open]); sec = int(r[sec_col])
            except: continue
            out[ep][sec] = {'up': f(r.get(up_col)), 'down': f(r.get(down_col))}
    return out


def net_pnl(price, won, invest=INVEST, comm=COMMISSION):
    shares = invest / price
    cost = invest * (1 + comm)
    return (shares - cost) if won else (-cost)


def stats_block(pnls, wins, n):
    if not n: return "n=0"
    total = sum(pnls); per = total/len(pnls)
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    wr = 100*wins/n
    sharpe = (per/sd) if sd > 0 else 0
    return (f"n={n:<4} win%={wr:5.1f}%  net=${total:+8.2f}  "
            f"per=${per:+.3f}  sd=${sd:.2f}  per/sd={sharpe:+.2f}")


def main():
    poly_outs = load_poly_outcomes()
    poly = load_per_sec(POLY,'market_epoch','sec_from_start','up_ask','down_ask')
    pred = load_per_sec(PRED,'market_open_epoch','sec_from_open','yes_ask','no_ask_implied')
    eps = set(poly.keys()) & set(pred.keys())
    print(f'commission={COMMISSION*100:.0f}%  invest=${INVEST}')

    for sec in SECS:
        print()
        print('='*94)
        print(f'ENTRY sec={sec} — STRONG CONSENSUS windows, buy the CHEAP OPPOSITE side')
        print('='*94)
        # Build consensus trades at this sec; record opposite (cheap) side price.
        rows = []
        for ep in eps:
            if ep not in poly_outs: continue
            p = poly[ep].get(sec); pr = pred[ep].get(sec)
            if not p or not pr: continue
            pu, pd = p.get('up'), p.get('down')
            yu, yd = pr.get('up'), pr.get('down')
            if pu is None or pd is None: continue
            pu_ok = pu is not None and pu >= THR
            pd_ok = pd is not None and pd >= THR
            yu_ok = yu is not None and yu >= THR
            yd_ok = yd is not None and yd >= THR
            if pu_ok and yu_ok:
                fav = 'UP'; opp = 'DOWN'; opp_price = pd
            elif pd_ok and yd_ok:
                fav = 'DOWN'; opp = 'UP'; opp_price = pu
            else:
                continue
            if opp_price is None or opp_price <= 0: continue
            won = poly_outs[ep] == opp   # contrarian wins if consensus was WRONG
            rows.append((opp_price, won))
        if not rows:
            print('  (no consensus windows at this sec)')
            continue
        pnls = [net_pnl(p, w) for p, w in rows]
        wins = sum(1 for _, w in rows if w)
        print('  ALL consensus, buy opposite: ' + stats_block(pnls, wins, len(rows)))

        # bucket by opposite (cheap) price
        buckets = defaultdict(lambda: {'pnls': [], 'wins': 0})
        for p, w in rows:
            if p <= 0.03: bk = 'a <=0.03'
            elif p <= 0.05: bk = 'b 0.03-0.05'
            elif p <= 0.08: bk = 'c 0.05-0.08'
            elif p <= 0.12: bk = 'd 0.08-0.12'
            elif p <= 0.18: bk = 'e 0.12-0.18'
            elif p <= 0.25: bk = 'f 0.18-0.25'
            elif p <= 0.35: bk = 'g 0.25-0.35'
            else: bk = 'h 0.35+'
            buckets[bk]['pnls'].append(net_pnl(p, w))
            buckets[bk]['wins'] += 1 if w else 0
        for k in sorted(buckets.keys()):
            b = buckets[k]
            if len(b['pnls']) >= 2:
                print(f'    opp {k:<12} ' + stats_block(b['pnls'], b['wins'], len(b['pnls'])))


if __name__ == '__main__':
    main()
