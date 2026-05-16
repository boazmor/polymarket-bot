#!/usr/bin/env python3
"""Calendar spread DEEP analysis — profit potential.

For each platform with both windows recorded, at each common close T:
  - Snapshot YES asks at T-30 sec
  - Find pairs where YES on lower-strike market < YES on higher-strike market
    by gap >= MIN_GAP
  - Determine actual close outcome (which side won)
  - Compute what BUYING the underpriced (correct) side would have paid

Also tests smaller gaps (1¢, 3¢) to see if there are more opportunities.

Backtest mode: assumes we'd have bought the strike-monotonic-correct side
at the listed ask and held to close.
"""

import csv
import sys
import re
from collections import defaultdict
from datetime import datetime


WINDOW_5M = 300
WINDOW_15M = 900
WINDOW_1H = 3600
INVEST = 2.0


def fnum(s):
    try:
        return float(s) if s not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def iso_to_epoch(iso):
    try:
        if " " in iso:
            iso = iso.replace(" ", "T") + "+00:00"
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return 0


def load_poly(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep = int(r.get("market_epoch") or 0)
                    es = int(r.get("epoch_sec") or 0)
                    if not ep: continue
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "yes_ask": fnum(r.get("up_ask")),
                        "no_ask": fnum(r.get("down_ask")),
                        "yes_bid": fnum(r.get("up_bid")),
                        "no_bid": fnum(r.get("down_bid")),
                        "strike": fnum(r.get("target_chainlink_at_open")),
                        "btc": fnum(r.get("binance_price")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_predict(path, win_sec):
    out = {}
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    ep_raw = r.get("market_open_epoch")
                    sec_raw = r.get("sec_from_open")
                    if not ep_raw or not sec_raw: continue
                    ep = int(ep_raw); sec = int(sec_raw)
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "yes_ask": fnum(r.get("yes_ask")),
                        "no_ask": fnum(r.get("no_ask_implied")),
                        "yes_bid": fnum(r.get("yes_bid")),
                        "no_bid": fnum(r.get("no_bid_implied")),
                        "strike": fnum(r.get("strike")),
                        "btc": fnum(r.get("binance_now")),
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def load_limitless(path, win_sec):
    out = {}
    if win_sec == WINDOW_5M:
        pat = re.compile(r"-5-min-(\d{10,13})$")
    elif win_sec == WINDOW_15M:
        pat = re.compile(r"-15-min-(\d{10,13})$")
    elif win_sec == WINDOW_1H:
        pat = re.compile(r"-hourly-(\d{10,13})$")
    else:
        return out
    try:
        with open(path) as f:
            for r in csv.DictReader(f):
                try:
                    slug = r.get("slug", "")
                    m = pat.search(slug)
                    if not m: continue
                    lim_id = int(m.group(1))
                    if lim_id > 10**12: lim_id = lim_id // 1000
                    ep = (lim_id // win_sec) * win_sec
                    es = int(r.get("epoch_sec") or 0)
                    sec = es - ep
                    if sec < 0 or sec > win_sec: continue
                    out[(ep, sec)] = {
                        "yes_ask": fnum(r.get("best_ask")),
                        "no_ask": fnum(r.get("no_best_ask")),
                        "yes_bid": fnum(r.get("best_bid")),
                        "no_bid": fnum(r.get("no_best_bid")),
                        "strike": 0,
                        "btc": 0,
                    }
                except (KeyError, ValueError): continue
    except FileNotFoundError: pass
    return out


def reconstruct_outcomes(per_sec, win_sec):
    """ep -> 'UP' or 'DOWN'. Try bids first; fallback to BTC vs strike at close."""
    by_ep = defaultdict(list)
    tail = max(60, win_sec - 60)
    for (ep, sec), d in per_sec.items():
        if sec >= tail:
            by_ep[ep].append((sec, d))
    out = {}
    for ep, ticks in by_ep.items():
        winner = None
        last_btc = 0.0
        last_strike = 0.0
        for sec, d in sorted(ticks):
            if d["yes_bid"] >= 0.95:
                winner = "UP"
            elif d["no_bid"] >= 0.95:
                winner = "DOWN"
            if d.get("btc", 0) > 0:
                last_btc = d["btc"]
            if d.get("strike", 0) > 0:
                last_strike = d["strike"]
        if winner is None and last_btc > 0 and last_strike > 0:
            winner = "UP" if last_btc > last_strike else "DOWN"
        if winner:
            out[ep] = winner
    return out


def backtest_calendar(name, data_short, win_short, out_short,
                     data_long, win_long, out_long, common_period,
                     min_gap=0.05, snap_offset=30):
    """At each common close T, if there's a strike-monotonic violation
    >= min_gap, simulate buying the correct side at the listed YES ask.
    Returns dict of stats."""
    short_eps = {ep for (ep, sec) in data_short}
    Ts = {ep + win_short for ep in short_eps}
    Ts = sorted([t for t in Ts if t % common_period == 0])

    total = liquid = violations = wins = losses = 0
    total_pnl = 0.0
    examples = []
    LIQ_LO, LIQ_HI = 0.03, 0.97

    for T in Ts:
        total += 1
        ep_s = T - win_short
        ep_l = T - win_long
        snap_s = data_short.get((ep_s, win_short - snap_offset))
        snap_l = data_long.get((ep_l, win_long - snap_offset))
        if not snap_s or not snap_l: continue
        ys, yl = snap_s["yes_ask"], snap_l["yes_ask"]
        ss, sl = snap_s["strike"], snap_l["strike"]
        if not (LIQ_LO <= ys <= LIQ_HI): continue
        if not (LIQ_LO <= yl <= LIQ_HI): continue
        if ss <= 0 or sl <= 0: continue
        if abs(ss - sl) > 500: continue
        liquid += 1

        # strike-monotonic rule: lower strike → easier UP → higher YES
        if ss < sl:
            low_yes, high_yes = ys, yl
            low_side_winner = out_short.get(ep_s)
            buy_side = "yes_short"
            buy_price = ys
        elif ss > sl:
            low_yes, high_yes = yl, ys
            low_side_winner = out_long.get(ep_l)
            buy_side = "yes_long"
            buy_price = yl
        else:
            continue

        gap = low_yes - high_yes
        if gap >= -min_gap:
            continue
        # violation: buy YES_low + NO_high (paired arbitrage)
        violations += 1
        # determine which snap is low-strike vs high-strike
        if ss < sl:
            yes_low_price = snap_s["yes_ask"]
            no_high_price = snap_l["no_ask"]
            low_winner = out_short.get(ep_s)
            high_winner = out_long.get(ep_l)
        else:
            yes_low_price = snap_l["yes_ask"]
            no_high_price = snap_s["no_ask"]
            low_winner = out_long.get(ep_l)
            high_winner = out_short.get(ep_s)
        # Both must be in liquid range
        if not (LIQ_LO <= no_high_price <= LIQ_HI):
            continue
        pair_cost = yes_low_price + no_high_price
        if pair_cost >= 1.0:
            continue  # not really arbitrage after liquidity check
        # 1 share of each side, total cost = pair_cost
        # Payouts:
        #   if low_winner == UP and high_winner == UP -> YES_low pays $1, NO_high loses -> $1 - pair_cost
        #   if low_winner == UP and high_winner == DOWN -> both win -> $2 - pair_cost
        #   if low_winner == DOWN and high_winner == DOWN -> YES_low loses, NO_high wins -> $1 - pair_cost
        #   if low_winner == DOWN and high_winner == UP -> IMPOSSIBLE if strikes work right
        if low_winner is None or high_winner is None:
            continue
        payout = 0
        if low_winner == "UP":
            payout += 1
        if high_winner == "DOWN":
            payout += 1
        pnl_per_pair = payout - pair_cost
        # Scale: how many pairs at INVEST per leg total ($INVEST per side)
        # buy INVEST worth of each side; total cost = 2*INVEST
        # shares_low = INVEST/yes_low_price; shares_high = INVEST/no_high_price
        # payout = shares_low * (1 if low_winner==UP else 0) + shares_high * (1 if high_winner==DOWN else 0)
        sl_shares = INVEST / yes_low_price
        sh_shares = INVEST / no_high_price
        pay = (sl_shares if low_winner == "UP" else 0) + (sh_shares if high_winner == "DOWN" else 0)
        pnl = pay - 2 * INVEST
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        total_pnl += pnl
        examples.append({"T": T, "gap": gap,
                         "yes_low": yes_low_price, "no_high": no_high_price,
                         "pair_cost": pair_cost,
                         "ss": ss, "sl": sl,
                         "low_winner": low_winner, "high_winner": high_winner,
                         "pnl": pnl,
                         "btc": snap_s["btc"]})

    return {
        "platform": name, "total": total, "liquid": liquid,
        "violations": violations, "wins": wins, "losses": losses,
        "pnl": total_pnl, "examples": examples,
    }


def report(stats, min_gap):
    n = stats["total"]
    print(f"  {stats['platform']} (פער מינ׳ {min_gap*100:.0f}¢):")
    print(f"    סגירות: {n}, נזילים: {stats['liquid']}, הפרות: {stats['violations']}")
    print(f"    זכיות: {stats['wins']}, הפסדים: {stats['losses']}")
    rate = (stats['wins']/(stats['wins']+stats['losses'])*100) if (stats['wins']+stats['losses'])>0 else 0
    print(f"    דיוק: {rate:.1f}%, רווח כולל: ${stats['pnl']:+.2f}")
    if stats["examples"]:
        stats["examples"].sort(key=lambda r: r["gap"])
        print(f"    דוגמאות הכי גדולות (זוגי):")
        for v in stats["examples"][:5]:
            r = "+" if v["pnl"] > 0 else "-"
            print(f"      BTC={v['btc']:.0f} | YES_low@{v['yes_low']:.2f} NO_high@{v['no_high']:.2f} "
                  f"עלות זוג ${v['pair_cost']:.2f} | "
                  f"winners L={v['low_winner']}/H={v['high_winner']} → {r}${abs(v['pnl']):.2f}")


def main():
    print("טוען...", file=sys.stderr)

    # ====== 5m vs 15m ======
    print("=== 5דק מול 15דק ===")

    poly_5m = load_poly("/root/data_btc_5m_research_h/combined_per_second.csv", WINDOW_5M)
    poly_15m = load_poly("/root/data_btc_15m_research/combined_per_second.csv", WINDOW_15M)
    o5 = reconstruct_outcomes(poly_5m, WINDOW_5M)
    o15 = reconstruct_outcomes(poly_15m, WINDOW_15M)
    print(f"poly: 5m={len(poly_5m)} 15m={len(poly_15m)} out5={len(o5)} out15={len(o15)}", file=sys.stderr)

    for gap in [0.05, 0.03, 0.01]:
        st = backtest_calendar("Polymarket", poly_5m, WINDOW_5M, o5,
                               poly_15m, WINDOW_15M, o15, WINDOW_15M, min_gap=gap)
        report(st, gap)
        print()

    # Predict 5m only on Helsinki, skip on Hetzner
    # ====== 15m vs 1h ======
    print()
    print("=== 15דק מול שעה ===")

    poly_1h = load_poly("/root/data_btc_1h_research/combined_per_second.csv", WINDOW_1H)
    o1h = reconstruct_outcomes(poly_1h, WINDOW_1H)

    for gap in [0.05, 0.03, 0.01]:
        st = backtest_calendar("Polymarket", poly_15m, WINDOW_15M, o15,
                               poly_1h, WINDOW_1H, o1h, WINDOW_1H, min_gap=gap)
        report(st, gap)
        print()

    pred_15m = load_predict("/root/data_predict_btc_15m/combined_per_second.csv", WINDOW_15M)
    pred_1h = load_predict("/root/data_predict_btc_1h/combined_per_second.csv", WINDOW_1H)
    op15 = reconstruct_outcomes(pred_15m, WINDOW_15M)
    op1h = reconstruct_outcomes(pred_1h, WINDOW_1H)
    print(f"predict: 15m={len(pred_15m)} 1h={len(pred_1h)} out15={len(op15)} out1h={len(op1h)}", file=sys.stderr)

    for gap in [0.05, 0.03, 0.01]:
        st = backtest_calendar("Predict.fun", pred_15m, WINDOW_15M, op15,
                               pred_1h, WINDOW_1H, op1h, WINDOW_1H, min_gap=gap)
        report(st, gap)
        print()

    # Limitless without strikes — skip
    print("Limitless: דילגנו, אין טרגט בקובץ ההקלטה")
    print("Kalshi: דילגנו, ה-1h שלהם בעצם שווקים שבועיים")


if __name__ == "__main__":
    main()
