#!/usr/bin/env python3
"""Re-run analysis cuts from scratch on raw recorder data.
NO median bin. NO fixed sec=90. Test each cut twice — raw, and layered on V2.
Lesson from hour-filter: filter order changes conclusions.
"""
import sys, csv, statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta

POLY    = '/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv'
POLYOUT = '/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv'
PRED    = '/root/data_predict_btc_5m/combined_per_second.csv'
LIM     = '/root/data_limitless_btc_5m/combined_per_second.csv'
LIMMK   = '/root/data_limitless_btc_5m/markets.csv'

THR = 0.60
INVEST = 2.0
SECS_TO_TEST = [15, 30, 45, 60, 90, 120, 180, 240, 270]


def f(v):
    if v in (None, '', 'None'): return None
    try: return float(v)
    except: return None


def load_poly_outcomes():
    out = {}
    with open(POLYOUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_epoch'])
                if r.get('winner_side') in ('UP', 'DOWN'):
                    out[ep] = r['winner_side']
            except: pass
    return out


def load_pred_outcomes():
    """Predict outcomes derived by comparing strike to final binance price.
    Predict uses 'strike' column (not 'target_price')."""
    last_binance_by_ep = {}
    strikes = {}
    with open(PRED) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r['market_open_epoch'])
                bn = f(r.get('binance_now'))
                tg = f(r.get('strike'))
                if bn is not None: last_binance_by_ep[ep] = bn
                if tg is not None: strikes[ep] = tg
            except: pass
    out = {}
    for ep, strike in strikes.items():
        final = last_binance_by_ep.get(ep)
        if final is None: continue
        out[ep] = 'UP' if final > strike else 'DOWN'
    return out


def load_lim_markets_and_outcomes():
    """Limitless: derive window epoch from expirationTimestamp - 300s.
    Match against per-second binance_now at the end of the window."""
    market_to_ep = {}
    market_target = {}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try:
                mid = r['market_id']
                exp_ms = int(r['expirationTimestamp'])
                ep = exp_ms // 1000 - 300  # market opens 5 min before expiration
                tg = f(r.get('target_price'))
                market_to_ep[mid] = ep
                if tg is not None: market_target[mid] = tg
            except: pass
    # For each market_id, find the LAST binance_now in the file
    last_binance_by_mid = {}
    with open(LIM) as fh:
        for r in csv.DictReader(fh):
            mid = r.get('market_id')
            if not mid: continue
            bn = f(r.get('binance_now'))
            if bn is not None: last_binance_by_mid[mid] = bn
    out = {}
    for mid, ep in market_to_ep.items():
        tg = market_target.get(mid)
        fin = last_binance_by_mid.get(mid)
        if tg is None or fin is None: continue
        out[ep] = 'UP' if fin > tg else 'DOWN'
    return out


def load_snaps_per_second(path, key_open='market_epoch', sec_col='sec_from_start',
                          up_col='up_ask', down_col='down_ask', dist_col='distance_signed',
                          derive_ep=None):
    """For each (epoch, sec) store the single per-second row's up/down/dist.
    derive_ep: optional callable(row) -> (ep, sec) for non-standard schemas."""
    snaps = {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if derive_ep:
                pair = derive_ep(r)
                if pair is None: continue
                ep, sec = pair
            else:
                try:
                    ep = int(r[key_open]); sec = int(r[sec_col])
                except: continue
            up = f(r.get(up_col))
            dn = f(r.get(down_col))
            dist = f(r.get(dist_col))
            snaps[(ep, sec)] = {'up': up, 'down': dn, 'dist': dist}
    return snaps


def derive_lim_ep(market_to_ep):
    """Limitless rows lack market_open_epoch. Derive via market_id and per-second offset."""
    def _derive(r):
        mid = r.get('market_id')
        ep = market_to_ep.get(mid)
        if ep is None: return None
        try:
            es = int(r['epoch_sec'])
        except: return None
        sec = es - ep
        if sec < 0 or sec > 320: return None
        return (ep, sec)
    return _derive


def snap_at(snaps, ep, sec):
    """Find the per-second row exactly at (ep, sec)."""
    return snaps.get((ep, sec))


def build_window_set(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, sec):
    """For a specific second, build the list of windows that have snapshots from all 3 + an outcome."""
    rows = []
    eps_with_outcome = set(poly_outs) | set(pred_outs) | set(lim_outs)
    for ep in eps_with_outcome:
        poly = snap_at(poly_snaps, ep, sec)
        pred = snap_at(pred_snaps, ep, sec)
        lim  = snap_at(lim_snaps,  ep, sec)
        if poly is None and pred is None: continue
        # outcomes
        po = poly_outs.get(ep); pdo = pred_outs.get(ep); lo = lim_outs.get(ep)
        if po is None and pdo is None: continue
        rows.append({
            'ep': ep, 'sec': sec,
            'poly': poly or {}, 'pred': pred or {}, 'lim': lim or {},
            'poly_out': po, 'pred_out': pdo, 'lim_out': lo,
        })
    return rows


def has_consensus(row):
    poly = row.get('poly') or {}; pred = row.get('pred') or {}
    pu, pd = poly.get('up'), poly.get('down')
    yu, yd = pred.get('up'), pred.get('down')
    pu_ok = pu is not None and pu >= THR
    pd_ok = pd is not None and pd >= THR
    yu_ok = yu is not None and yu >= THR
    yd_ok = yd is not None and yd >= THR
    if pu_ok and yu_ok: return 'UP'
    if pd_ok and yd_ok: return 'DOWN'
    return None


def cheap_pick(row, side):
    pp = row['poly'].get('up') if side == 'UP' else row['poly'].get('down')
    yp = row['pred'].get('up') if side == 'UP' else row['pred'].get('down')
    if pp is None and yp is None: return None
    if pp is None: return ('predict', yp)
    if yp is None: return ('poly', pp)
    return ('poly', pp) if pp <= yp else ('predict', yp)


def outcome_for(row, plat):
    return row.get(f'{plat}_out')


def pnl_of(row, side, plat, price):
    o = outcome_for(row, plat)
    if o is None: return None
    if o == side: return INVEST/price - INVEST
    return -INVEST


def v2_pick(row):
    side = has_consensus(row)
    if not side: return None
    d = (row.get('poly') or {}).get('dist')
    if d is not None and 50 <= abs(d) <= 100: return None
    pk = cheap_pick(row, side)
    if pk is None: return None
    plat, price = pk
    return side, plat, price


def summarize(rows_trades, name):
    fires = wins = losses = 0
    pnl_sum = 0.0
    for r, side, plat, price in rows_trades:
        fires += 1
        p = pnl_of(r, side, plat, price)
        if p is None: continue
        if p > 0: wins += 1
        else: losses += 1
        pnl_sum += p
    res = wins + losses
    wr = (100*wins/res) if res else 0
    per = (pnl_sum/res) if res else 0
    print(f"  {name:<48} fires={fires:<5} win%={wr:5.1f}%  PnL=${pnl_sum:+8.2f}  per-trade ${per:+.3f}")
    return {'fires': fires, 'wins': wins, 'losses': losses, 'pnl': pnl_sum, 'wr': wr, 'per': per}


# ============================================================================
# MAIN
# ============================================================================
def main():
    print('Loading outcomes...')
    poly_outs = load_poly_outcomes()
    pred_outs = load_pred_outcomes()
    # Build market_to_ep for limitless
    lim_market_to_ep = {}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try:
                mid = r['market_id']
                exp_ms = int(r['expirationTimestamp'])
                ep = exp_ms // 1000 - 300
                lim_market_to_ep[mid] = ep
            except: pass
    lim_outs  = load_lim_markets_and_outcomes()
    print(f'  poly_outs: {len(poly_outs)}  pred_outs: {len(pred_outs)}  lim_outs: {len(lim_outs)}')

    print('Loading per-second snapshots...')
    poly_snaps = load_snaps_per_second(POLY)
    # Predict uses yes_ask but has no_ask_implied / no_ask_usd_buyable, not no_ask
    pred_snaps = load_snaps_per_second(PRED, key_open='market_open_epoch',
                                       sec_col='sec_from_open',
                                       up_col='yes_ask', down_col='no_ask_implied')
    lim_snaps  = load_snaps_per_second(LIM,
                                       up_col='best_ask', down_col='no_best_ask',
                                       derive_ep=derive_lim_ep(lim_market_to_ep))
    print(f'  poly snaps: {len(poly_snaps)}  pred snaps: {len(pred_snaps)}  lim snaps: {len(lim_snaps)}')

    # ========================================================================
    print()
    print('='*90)
    print('CUT 3 - entry second comparison (each row is V2 logic at that single second)')
    print('='*90)
    print(f"  {'sec':<4} {'fires':<6} {'wins':<6} {'lose':<5} {'win%':<7} {'PnL$':<10} {'per-trade':<10}")
    best_sec = None
    best_per = -1e9
    for s in SECS_TO_TEST:
        windows = build_window_set(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, s)
        trades = []
        for r in windows:
            pk = v2_pick(r)
            if not pk: continue
            side, plat, price = pk
            trades.append((r, side, plat, price))
        fires = wins = losses = 0; pnl_sum = 0.0
        for r, side, plat, price in trades:
            fires += 1
            p = pnl_of(r, side, plat, price)
            if p is None: continue
            if p > 0: wins += 1
            else: losses += 1
            pnl_sum += p
        res = wins + losses
        wr = (100*wins/res) if res else 0
        per = (pnl_sum/res) if res else 0
        marker = ''
        if per > best_per:
            best_per = per; best_sec = s
            marker = ' <-- BEST so far'
        print(f"  {s:<4} {fires:<6} {wins:<6} {losses:<5} {wr:<6.1f}% ${pnl_sum:+8.2f}  ${per:+.3f}{marker}")
    print()
    print(f'Best entry sec by per-trade PnL: {best_sec}  (${best_per:+.3f})')
    print()

    # Use best_sec going forward
    SEC = best_sec
    windows = build_window_set(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, SEC)
    print(f'Continuing all subsequent cuts at sec={SEC}, {len(windows)} windows with snapshots+outcome.')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 16 - single-platform knowledge score (sec={SEC})')
    print('='*90)
    for plat in ('poly', 'pred', 'lim'):
        up_corr = up_n = dn_corr = dn_n = 0
        for r in windows:
            snap = r[plat] or {}
            up = snap.get('up'); dn = snap.get('down')
            up_ok = up is not None and up >= THR
            dn_ok = dn is not None and dn >= THR
            actual = outcome_for(r, plat)
            if actual is None: continue
            if up_ok and not dn_ok:
                up_n += 1
                if actual == 'UP': up_corr += 1
            if dn_ok and not up_ok:
                dn_n += 1
                if actual == 'DOWN': dn_corr += 1
        if up_n: print(f'  {plat:6s} UP-vote correct: {up_corr}/{up_n} = {100*up_corr/up_n:.1f}%')
        if dn_n: print(f'  {plat:6s} DN-vote correct: {dn_corr}/{dn_n} = {100*dn_corr/dn_n:.1f}%')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 1 - outlier per platform (sec={SEC})')
    print('='*90)
    print('When platform X voted alone against the other 2, was X right?')
    for plat_name in ('poly', 'pred', 'lim'):
        correct = wrong = 0
        for r in windows:
            sources = {'poly': r['poly'], 'pred': r['pred'], 'lim': r['lim']}
            ups = []; dns = []
            for n, s in sources.items():
                if not s: continue
                u = s.get('up'); d = s.get('down')
                if u is not None and u >= THR: ups.append(n)
                if d is not None and d >= THR: dns.append(n)
            actual = outcome_for(r, plat_name)
            if actual is None: continue
            if plat_name in ups and len(ups) == 1 and len(dns) >= 1:
                if actual == 'UP': correct += 1
                else: wrong += 1
            if plat_name in dns and len(dns) == 1 and len(ups) >= 1:
                if actual == 'DOWN': correct += 1
                else: wrong += 1
        tot = correct + wrong
        if tot:
            print(f'  {plat_name:6s} alone vs majority {tot} times — right {correct} ({100*correct/tot:.0f}%)')
        else:
            print(f'  {plat_name:6s} never alone vs majority')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 15 - pairwise agreement (sec={SEC})')
    print('='*90)
    print('Just THESE TWO platforms agree on direction. Take cheapest of the tradeables.')
    pairs = [('poly','pred'), ('poly','lim'), ('pred','lim')]
    for p1, p2 in pairs:
        trades = []
        for r in windows:
            s1 = r[p1] or {}; s2 = r[p2] or {}
            u1 = s1.get('up'); d1 = s1.get('down')
            u2 = s2.get('up'); d2 = s2.get('down')
            up1 = u1 is not None and u1 >= THR
            dn1 = d1 is not None and d1 >= THR
            up2 = u2 is not None and u2 >= THR
            dn2 = d2 is not None and d2 >= THR
            side = None
            if up1 and up2: side = 'UP'
            elif dn1 and dn2: side = 'DOWN'
            else: continue
            # choose cheaper
            cands = []
            for p in (p1, p2):
                snap = r[p]
                price = snap.get('up') if side == 'UP' else snap.get('down')
                if price: cands.append((p, price))
            if not cands: continue
            plat, price = min(cands, key=lambda x: x[1])
            trades.append((r, side, plat, price))
        summarize(trades, f'{p1}+{p2}')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 9 - distance from poly target (sec={SEC})')
    print('='*90)

    def dist_bucket(d):
        if d is None: return 'NA'
        ad = abs(d)
        if ad < 20: return '0-20'
        if ad < 50: return '20-50'
        if ad <= 100: return '50-100'
        if ad <= 200: return '100-200'
        return '200+'

    print('A) RAW (no other filter, side picked by Poly+Pred consensus only):')
    by_b = defaultdict(list)
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        pk = cheap_pick(r, side)
        if not pk: continue
        plat, price = pk
        d = (r['poly'] or {}).get('dist')
        by_b[dist_bucket(d)].append((r, side, plat, price))
    for k in ('0-20','20-50','50-100','100-200','200+','NA'):
        if k in by_b: summarize(by_b[k], f'dist {k}')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 11 - price bucket (sec={SEC})')
    print('='*90)
    print('Of V2 picks (consensus+dist), bucket by entry price.')
    def price_bucket(p):
        if p <= 0.55: return '<=0.55'
        if p <= 0.65: return '0.55-0.65'
        if p <= 0.75: return '0.65-0.75'
        if p <= 0.85: return '0.75-0.85'
        return '>0.85'
    by_b = defaultdict(list)
    for r in windows:
        pk = v2_pick(r)
        if not pk: continue
        side, plat, price = pk
        by_b[price_bucket(price)].append((r, side, plat, price))
    for k in ('<=0.55','0.55-0.65','0.65-0.75','0.75-0.85','>0.85'):
        if k in by_b: summarize(by_b[k], f'price {k}')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 13 - NYC hour (sec={SEC})')
    print('='*90)
    print('A) RAW (no other filter, just consensus to know side):')
    by_h = defaultdict(list)
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        pk = cheap_pick(r, side)
        if not pk: continue
        plat, price = pk
        nyc = (datetime.fromtimestamp(r['ep'], tz=timezone.utc) - timedelta(hours=4)).hour
        by_h[nyc].append((r, side, plat, price))
    print(f"  {'hr':<3} {'fires':<6} {'win%':<7} {'PnL$':<10} {'per-trade':<10}")
    for h in sorted(by_h.keys()):
        s = summarize(by_h[h], f'hr {h}')

    print()
    print('B) AFTER V2 filters (consensus + distance):')
    by_h = defaultdict(list)
    for r in windows:
        pk = v2_pick(r)
        if not pk: continue
        side, plat, price = pk
        nyc = (datetime.fromtimestamp(r['ep'], tz=timezone.utc) - timedelta(hours=4)).hour
        by_h[nyc].append((r, side, plat, price))
    for h in sorted(by_h.keys()):
        summarize(by_h[h], f'hr {h}')

    # ========================================================================
    print()
    print('='*90)
    print(f'CUT 2 - silent majority (sec={SEC})')
    print('='*90)
    by_silent = defaultdict(list)
    for r in windows:
        side = has_consensus(r)
        if not side: continue
        pk = cheap_pick(r, side)
        if not pk: continue
        plat, price = pk
        n_silent = 0
        for n in ('poly','pred','lim'):
            s = r[n] or {}
            u = s.get('up'); d = s.get('down')
            u_ok = u is not None and u >= THR
            d_ok = d is not None and d >= THR
            if not u_ok and not d_ok: n_silent += 1
        by_silent[n_silent].append((r, side, plat, price))
    for k in sorted(by_silent.keys()):
        summarize(by_silent[k], f'{k} silent of 3')

    # ========================================================================
    print()
    print('='*90)
    print('OVERALL V2 baseline at best sec for reference')
    print('='*90)
    trades = []
    for r in windows:
        pk = v2_pick(r)
        if not pk: continue
        side, plat, price = pk
        trades.append((r, side, plat, price))
    summarize(trades, f'V2 at sec={SEC}')


if __name__ == '__main__':
    main()
