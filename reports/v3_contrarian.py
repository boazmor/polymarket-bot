#!/usr/bin/env python3
"""CONTRARIAN analysis — buy in NO-CONSENSUS windows.
Where Poly+Predict do NOT agree, test buying the cheaper (underdog) side
and the side opposite the weak lean. Full dollar accounting:
  - 2% commission on notional (configurable)
  - standard deviation of per-trade net PnL (risk)
Direction (UP/DOWN) is not a parameter — we bucket by price/cheapness only.
"""
import sys, csv, statistics
from collections import defaultdict
from datetime import datetime, timezone

THR = 0.60
INVEST = 2.0
COMMISSION = 0.02   # 2% on notional
ENTER_MIN_SEC = 30
ENTER_MAX_SEC = 270

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


def load_per_sec(path, key_open, sec_col, up_col, down_col, target_col, bn_col=None):
    out = defaultdict(dict)
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try: ep = int(r[key_open]); sec = int(r[sec_col])
            except: continue
            out[ep][sec] = {
                'up': f(r.get(up_col)), 'down': f(r.get(down_col)),
                'target': f(r.get(target_col)),
                'binance': f(r.get(bn_col)) if bn_col else None,
            }
    return out


def net_pnl(price, won, invest=INVEST, comm=COMMISSION):
    """Buy `invest` USD of shares at `price`. 2% commission on notional.
       Win: shares pay $1 each. Lose: shares worth 0."""
    shares = invest / price
    cost = invest * (1 + comm)
    if won:
        return shares * 1.0 - cost
    return -cost


def stats_block(pnls, wins, n):
    if not pnls:
        return "n=0"
    total = sum(pnls)
    per = total / len(pnls)
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    wr = 100 * wins / n if n else 0
    sharpe = (per / sd) if sd > 0 else 0
    return (f"n={n:<4} win%={wr:5.1f}%  net=${total:+8.2f}  "
            f"per=${per:+.3f}  sd=${sd:.3f}  per/sd={sharpe:+.2f}")


def main():
    poly_outs = load_poly_outcomes()
    poly_by_ep = load_per_sec(POLY,'market_epoch','sec_from_start',
                              'up_ask','down_ask','target_price','binance_price')
    pred_by_ep = load_per_sec(PRED,'market_open_epoch','sec_from_open',
                              'yes_ask','no_ask_implied','strike','binance_now')
    eps = set(poly_by_ep.keys()) & set(pred_by_ep.keys())
    print(f'commission={COMMISSION*100:.0f}%  invest=${INVEST}')
    print(f'windows with poly+pred: {len(eps)}\n')

    # Build NO-CONSENSUS windows: poly & pred do NOT both agree >=THR same side.
    # Take first such sec per window. Record both side prices on Poly.
    no_consensus = []
    for ep in eps:
        if ep not in poly_outs: continue
        poly_secs = poly_by_ep[ep]; pred_secs = pred_by_ep[ep]
        for sec in range(ENTER_MIN_SEC, ENTER_MAX_SEC + 1):
            poly = poly_secs.get(sec); pred = pred_secs.get(sec)
            if not poly or not pred: continue
            pu, pd = poly.get('up'), poly.get('down')
            yu, yd = pred.get('up'), pred.get('down')
            if pu is None or pd is None: continue
            # consensus check
            pu_ok = pu is not None and pu >= THR
            pd_ok = pd is not None and pd >= THR
            yu_ok = yu is not None and yu >= THR
            yd_ok = yd is not None and yd >= THR
            is_consensus = (pu_ok and yu_ok) or (pd_ok and yd_ok)
            if is_consensus: continue
            # this is a no-consensus window
            no_consensus.append({
                'ep': ep, 'sec': sec,
                'poly_up': pu, 'poly_down': pd,
                'pred_up': yu, 'pred_down': yd,
                'outcome': poly_outs[ep],
            })
            break
    print(f'NO-CONSENSUS windows (first such sec): {len(no_consensus)}\n')

    # ---- Strategy 1: buy CHEAPER side on Poly (underdog) ----
    print('='*92)
    print('STRATEGY 1 — buy the CHEAPER side on Poly (the underdog)')
    print('='*92)
    # overall
    pnls = []; wins = 0
    for w in no_consensus:
        if w['poly_up'] <= w['poly_down']:
            side, price = 'UP', w['poly_up']
        else:
            side, price = 'DOWN', w['poly_down']
        won = w['outcome'] == side
        pnls.append(net_pnl(price, won)); wins += 1 if won else 0
    print('  ALL no-consensus: ' + stats_block(pnls, wins, len(pnls)))

    # by cheapness bucket
    print('  -- by underdog price bucket --')
    buckets = defaultdict(lambda: {'pnls': [], 'wins': 0})
    for w in no_consensus:
        if w['poly_up'] <= w['poly_down']:
            side, price = 'UP', w['poly_up']
        else:
            side, price = 'DOWN', w['poly_down']
        if price <= 0.10: bk = 'a <=0.10'
        elif price <= 0.20: bk = 'b 0.10-0.20'
        elif price <= 0.30: bk = 'c 0.20-0.30'
        elif price <= 0.40: bk = 'd 0.30-0.40'
        elif price <= 0.50: bk = 'e 0.40-0.50'
        else: bk = 'f 0.50+'
        won = w['outcome'] == side
        buckets[bk]['pnls'].append(net_pnl(price, won))
        buckets[bk]['wins'] += 1 if won else 0
    for k in sorted(buckets.keys()):
        b = buckets[k]
        print(f'    {k:<14} ' + stats_block(b['pnls'], b['wins'], len(b['pnls'])))

    # ---- Strategy 2: buy side OPPOSITE the lean ----
    print()
    print('='*92)
    print('STRATEGY 2 — buy side OPPOSITE the Poly lean (contrarian to the more-expensive side)')
    print('='*92)
    # lean = the more expensive side (market thinks more likely). Buy the opposite (cheaper).
    # This is mathematically same as Strategy 1 (cheaper = opposite of lean). Provide as confirmation
    # but bucket by the LEAN STRENGTH (how lopsided the market is).
    print('  -- by lean strength (expensive side price) --')
    buckets = defaultdict(lambda: {'pnls': [], 'wins': 0})
    for w in no_consensus:
        exp_price = max(w['poly_up'], w['poly_down'])
        if w['poly_up'] <= w['poly_down']:
            side, price = 'UP', w['poly_up']
        else:
            side, price = 'DOWN', w['poly_down']
        if exp_price <= 0.55: bk = 'lean 0.50-0.55'
        elif exp_price <= 0.60: bk = 'lean 0.55-0.60'
        elif exp_price <= 0.70: bk = 'lean 0.60-0.70'
        elif exp_price <= 0.80: bk = 'lean 0.70-0.80'
        else: bk = 'lean 0.80+'
        won = w['outcome'] == side
        buckets[bk]['pnls'].append(net_pnl(price, won))
        buckets[bk]['wins'] += 1 if won else 0
    for k in ('lean 0.50-0.55','lean 0.55-0.60','lean 0.60-0.70','lean 0.70-0.80','lean 0.80+'):
        if k in buckets:
            b = buckets[k]
            print(f'    {k:<16} ' + stats_block(b['pnls'], b['wins'], len(b['pnls'])))

    # ---- Strategy 3: where Poly and Pred OPPOSE each other (true split) ----
    print()
    print('='*92)
    print('STRATEGY 3 — Poly and Pred OPPOSE (one leans UP, other leans DOWN)')
    print('='*92)
    split = []
    for w in no_consensus:
        poly_lean = 'UP' if w['poly_up'] > w['poly_down'] else 'DOWN'
        pred_lean = 'UP' if (w['pred_up'] or 0) > (w['pred_down'] or 0) else 'DOWN'
        if poly_lean != pred_lean:
            split.append((w, poly_lean, pred_lean))
    print(f'  windows where Poly lean != Pred lean: {len(split)}')
    # In a true split, buy the cheaper side on Poly
    for label, picker in [
        ('buy cheaper Poly side', lambda w: ('UP', w['poly_up']) if w['poly_up'] <= w['poly_down'] else ('DOWN', w['poly_down'])),
        ('buy Poly-lean side',    lambda w: ('UP', w['poly_up']) if w['poly_up'] > w['poly_down'] else ('DOWN', w['poly_down'])),
    ]:
        pnls = []; wins = 0
        for w, pl, prl in split:
            side, price = picker(w)
            won = w['outcome'] == side
            pnls.append(net_pnl(price, won)); wins += 1 if won else 0
        print(f'    {label:<22} ' + stats_block(pnls, wins, len(pnls)))


if __name__ == '__main__':
    main()
