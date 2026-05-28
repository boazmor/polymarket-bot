#!/usr/bin/env python3
"""Historical backtest of CONSENSUS_BTC_V1 on the 3-day window of Poly+Predict+Limitless data.

Runs cuts 1, 2, 3, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16 from project_v1_analysis_cuts.
Skips cuts 4 (target alone — meaningless without context), 5 (target agreement — covered in cut 10), 14 (Kalshi — too little data).
"""
import csv
import os
import statistics
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PRED = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK = "/root/data_limitless_btc_5m/markets.csv"

THR = 0.60
INVEST = 2.0


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def f(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def load_poly_outcomes():
    out = {}
    with open(POLY_OUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r["market_epoch"])
                if r.get("winner_side") in ("UP", "DOWN"):
                    out[ep] = {
                        "outcome": r["winner_side"],
                        "target": f(r.get("target_price")),
                        "final_binance": f(r.get("final_binance_price")),
                    }
            except (ValueError, KeyError):
                pass
    return out


def build_poly_snapshots():
    """dict[(window_epoch, ref_sec_bin)] -> {up_ask, down_ask, target, binance, distance, n}.
    Bins: 30, 60, 90, 120, 180, 240 (using +/-10 window)."""
    bins = [30, 60, 90, 120, 180, 240]
    by_ep = defaultdict(lambda: defaultdict(lambda: {"up": [], "down": [], "target": [], "binance": [], "dist": []}))
    print(f"  loading {POLY}...", flush=True)
    with open(POLY) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r["market_epoch"])
                sec = int(r["sec_from_start"])
            except (ValueError, KeyError, TypeError):
                continue
            for b in bins:
                if abs(sec - b) <= 10:
                    bucket = by_ep[ep][b]
                    v = f(r.get("up_ask"))
                    if v: bucket["up"].append(v)
                    v = f(r.get("down_ask"))
                    if v: bucket["down"].append(v)
                    v = f(r.get("target_price"))
                    if v: bucket["target"].append(v)
                    v = f(r.get("binance_price"))
                    if v: bucket["binance"].append(v)
                    v = f(r.get("distance_signed"))
                    if v: bucket["dist"].append(v)
    snaps = {}
    for ep, by_sec in by_ep.items():
        for sec, vals in by_sec.items():
            snaps[(ep, sec)] = {
                "up": median(vals["up"]),
                "down": median(vals["down"]),
                "target": median(vals["target"]),
                "binance": median(vals["binance"]),
                "dist": median(vals["dist"]),
                "n": len(vals["up"]),
            }
    return snaps


def build_predict_snapshots():
    bins = [30, 60, 90, 120, 180, 240]
    by_market = defaultdict(list)
    print(f"  loading {PRED}...", flush=True)
    with open(PRED) as fh:
        for r in csv.DictReader(fh):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    snaps = {}
    pred_outcomes = {}
    for mid, rows in by_market.items():
        if len(rows) < 5:
            continue
        try:
            ep = int(rows[0].get("market_open_epoch") or 0)
            strike = f(rows[0].get("strike"))
        except (ValueError, TypeError):
            continue
        if not ep or not strike:
            continue
        # outcome: last binance_now vs strike
        last_bn = None
        for r in reversed(rows):
            v = f(r.get("binance_now"))
            if v:
                last_bn = v
                break
        if last_bn:
            pred_outcomes[ep] = {"outcome": "UP" if last_bn > strike else "DOWN", "target": strike, "final_binance": last_bn}
        # snapshots per bin
        for b in bins:
            ya, na, bn, ds = [], [], [], []
            for r in rows:
                try:
                    sec = int(r.get("sec_from_open") or -1)
                    if abs(sec - b) > 10:
                        continue
                    v = f(r.get("yes_ask"))
                    if v: ya.append(v)
                    v = f(r.get("no_ask_implied"))
                    if v: na.append(v)
                    v = f(r.get("binance_now"))
                    if v: bn.append(v)
                    v = f(r.get("distance_signed"))
                    if v: ds.append(v)
                except (ValueError, TypeError):
                    continue
            if ya:
                snaps[(ep, b)] = {
                    "up": median(ya), "down": median(na),
                    "target": strike, "binance": median(bn),
                    "dist": median(ds), "n": len(ya),
                }
    return snaps, pred_outcomes


def build_lim_snapshots():
    bins = [30, 60, 90, 120, 180, 240]
    # market_id -> {first_ts, target, expirationTimestamp}
    mk_meta = {}
    if os.path.exists(LIM_MK):
        with open(LIM_MK) as fh:
            for r in csv.DictReader(fh):
                mid = r.get("market_id")
                if not mid:
                    continue
                try:
                    exp_ms = int(r.get("expirationTimestamp") or 0)
                    tg = f(r.get("target_price"))
                except (ValueError, TypeError):
                    continue
                if exp_ms:
                    mk_meta[mid] = {"expiration": exp_ms // 1000, "target": tg}

    by_market = defaultdict(list)
    print(f"  loading {LIM}...", flush=True)
    with open(LIM) as fh:
        for r in csv.DictReader(fh):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)

    snaps = {}
    lim_outcomes = {}
    for mid, rows in by_market.items():
        if len(rows) < 5:
            continue
        meta = mk_meta.get(mid)
        if not meta:
            continue
        # window_epoch = expiration - 300 (5-min market)
        window_epoch = meta["expiration"] - 300
        target = meta["target"]
        if not target:
            try:
                target = f(rows[0].get("target_price"))
            except (ValueError, TypeError):
                continue
        if not target:
            continue
        # outcome: last binance_now vs target
        last_bn = None
        for r in reversed(rows):
            v = f(r.get("binance_now"))
            if v:
                last_bn = v
                break
        if last_bn:
            lim_outcomes[window_epoch] = {"outcome": "UP" if last_bn > target else "DOWN", "target": target, "final_binance": last_bn}
        # snapshots per bin (sec_from_open = epoch_sec - window_epoch)
        for b in bins:
            ya, na, bn, ds = [], [], [], []
            for r in rows:
                try:
                    sec = int(r["epoch_sec"]) - window_epoch
                    if abs(sec - b) > 10:
                        continue
                    v = f(r.get("best_ask"))
                    if v: ya.append(v)
                    v = f(r.get("no_best_ask"))
                    if v: na.append(v)
                    v = f(r.get("binance_now"))
                    if v: bn.append(v)
                    v = f(r.get("distance_signed"))
                    if v: ds.append(v)
                except (ValueError, KeyError, TypeError):
                    continue
            if ya:
                snaps[(window_epoch, b)] = {
                    "up": median(ya), "down": median(na),
                    "target": target, "binance": median(bn),
                    "dist": median(ds), "n": len(ya),
                }
    return snaps, lim_outcomes


def build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90):
    """For each window with poly+pred+lim data at ref_sec, build a row."""
    common = set(poly_outs) & set(pred_outs) & set(lim_outs)
    print(f"  windows with all 3 outcomes: {len(common)}")
    rows = []
    for ep in common:
        ps = poly_snaps.get((ep, ref_sec))
        prs = pred_snaps.get((ep, ref_sec))
        ls = lim_snaps.get((ep, ref_sec))
        if not (ps and prs and ls):
            continue
        rows.append({
            "ep": ep,
            "poly": ps, "pred": prs, "lim": ls,
            "poly_out": poly_outs[ep]["outcome"],
            "pred_out": pred_outs[ep]["outcome"],
            "lim_out": lim_outs[ep]["outcome"],
            "poly_target": poly_outs[ep]["target"],
            "pred_target": pred_outs[ep]["target"],
            "lim_target": lim_outs[ep]["target"],
        })
    print(f"  windows with all snapshots at sec={ref_sec}: {len(rows)}")
    return rows


def vote_classify(snap, thr):
    """Returns (vote_up, vote_dn) — booleans."""
    if not snap:
        return False, False
    u = snap.get("up"); d = snap.get("down")
    return (u is not None and u >= thr), (d is not None and d >= thr)


def decide_row(row, thr, min_agreements, no_dissent=False):
    """Returns (side, plat, price) or None for trade decision."""
    sources = [
        ("poly", row["poly"], True),
        ("pred", row["pred"], True),
        ("lim", row["lim"], False),
    ]
    up_votes = []
    dn_votes = []
    for name, snap, tradeable in sources:
        up, dn = vote_classify(snap, thr)
        if up:
            up_votes.append((name, snap["up"], tradeable))
        if dn:
            dn_votes.append((name, snap["down"], tradeable))

    side = None
    votes = []
    if len(up_votes) >= min_agreements and len(up_votes) > len(dn_votes):
        side, votes = "UP", up_votes
        opp = len(dn_votes)
    elif len(dn_votes) >= min_agreements and len(dn_votes) > len(up_votes):
        side, votes = "DOWN", dn_votes
        opp = len(up_votes)
    else:
        return None
    if no_dissent and opp > 0:
        return None
    tradeable = [v for v in votes if v[2]]
    if not tradeable:
        return None
    plat, price, _ = min(tradeable, key=lambda v: v[1])
    return (side, plat, price, len(votes), opp)


def outcome_for(row, plat):
    return row.get(f"{plat}_out") if plat in ("poly", "pred") else row.get("lim_out")


def pnl(side, plat, price, row):
    o = outcome_for(row, plat)
    if o is None:
        return None
    if o == side:
        return (INVEST / price) - INVEST
    return -INVEST


def print_table(title, rows):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = [max(len(str(r[c])) for r in rows + [{c: c for c in cols}]) for c in cols]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


def main():
    print("Loading data...")
    poly_outs = load_poly_outcomes()
    print(f"  poly outcomes: {len(poly_outs)}")
    poly_snaps = build_poly_snapshots()
    print(f"  poly snapshot bins: {len(poly_snaps)}")
    pred_snaps, pred_outs = build_predict_snapshots()
    print(f"  pred snapshot bins: {len(pred_snaps)} outcomes: {len(pred_outs)}")
    lim_snaps, lim_outs = build_lim_snapshots()
    print(f"  lim snapshot bins: {len(lim_snaps)} outcomes: {len(lim_outs)}")
    print()

    # default: sec=90
    windows = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    if not windows:
        sys.exit("no windows with full data")
    print()

    # ========= BASELINE =========
    def run_strategy(rows, thr, min_n, no_dissent=False, label=""):
        fires = wins = losses = 0
        prices = []
        total_pnl = 0.0
        for r in rows:
            d = decide_row(r, thr, min_n, no_dissent)
            if not d:
                continue
            side, plat, price, n_v, opp = d
            fires += 1
            prices.append(price)
            p = pnl(side, plat, price, r)
            if p is None:
                continue
            if p > 0:
                wins += 1
            else:
                losses += 1
            total_pnl += p
        resolved = wins + losses
        wr = (100 * wins / resolved) if resolved else 0
        ap = sum(prices) / len(prices) if prices else 0
        return {"label": label, "fires": fires, "wins": wins, "losses": losses,
                "win%": f"{wr:.1f}", "avg_p": f"{ap:.3f}", "PnL$": f"{total_pnl:+.2f}"}

    baseline_rows = []
    for min_n in (2, 3):
        baseline_rows.append(run_strategy(windows, THR, min_n, False, f"loose N={min_n}"))
        baseline_rows.append(run_strategy(windows, THR, min_n, True, f"strict N={min_n}"))
    print_table("BASELINE — loose vs strict at sec=90 (3 platforms only)", baseline_rows)

    # ========= CUT 1: outlier per platform =========
    print()
    print("=" * 78)
    print("CUT 1 — per-platform outlier behavior")
    print("=" * 78)
    print("When platform X voted alone against the other 2, who won?")
    for plat_name in ("poly", "pred", "lim"):
        plat_alone_correct = plat_alone_wrong = 0
        for r in windows:
            ups = [name for name, snap, _ in [("poly", r["poly"], True), ("pred", r["pred"], True), ("lim", r["lim"], False)]
                   if vote_classify(snap, THR)[0]]
            dns = [name for name, snap, _ in [("poly", r["poly"], True), ("pred", r["pred"], True), ("lim", r["lim"], False)]
                   if vote_classify(snap, THR)[1]]
            # plat alone UP, others not UP
            if plat_name in ups and len(ups) == 1 and len(dns) >= 1:
                # plat says UP, others say DOWN or silent
                actual = outcome_for(r, plat_name) or outcome_for(r, "poly")
                if actual == "UP": plat_alone_correct += 1
                elif actual == "DOWN": plat_alone_wrong += 1
            if plat_name in dns and len(dns) == 1 and len(ups) >= 1:
                actual = outcome_for(r, plat_name) or outcome_for(r, "poly")
                if actual == "DOWN": plat_alone_correct += 1
                elif actual == "UP": plat_alone_wrong += 1
        total = plat_alone_correct + plat_alone_wrong
        if total:
            print(f"  {plat_name:6s}: alone vs majority {total:4d} times — correct {plat_alone_correct} ({100*plat_alone_correct/total:.0f}%)")
        else:
            print(f"  {plat_name:6s}: never voted alone against majority")

    # ========= CUT 2: silent majority =========
    print()
    print("=" * 78)
    print("CUT 2 — silent count (out of 3 platforms)")
    print("=" * 78)
    by_silent = defaultdict(lambda: {"fires": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in windows:
        d = decide_row(r, THR, 2, False)
        # count silents using THR
        n_silent = 0
        for snap in (r["poly"], r["pred"], r["lim"]):
            up, dn = vote_classify(snap, THR)
            if not up and not dn:
                n_silent += 1
        b = by_silent[n_silent]
        if d:
            side, plat, price, _, _ = d
            b["fires"] += 1
            p = pnl(side, plat, price, r)
            if p is None:
                continue
            if p > 0:
                b["wins"] += 1
            else:
                b["losses"] += 1
            b["pnl"] += p
    print(f"  {'silent_n':<8} {'fires':<6} {'wins':<5} {'losses':<7} {'win%':<7} {'PnL$':<8}")
    for n in sorted(by_silent.keys()):
        b = by_silent[n]
        res = b["wins"] + b["losses"]
        wr = (100 * b["wins"] / res) if res else 0
        print(f"  {n:<8} {b['fires']:<6} {b['wins']:<5} {b['losses']:<7} {wr:<6.1f}% {b['pnl']:+.2f}")

    # ========= CUT 3: seconds =========
    print()
    print("=" * 78)
    print("CUT 3 — by reference second (loose, min_N=2)")
    print("=" * 78)
    sec_rows = []
    for sec in (30, 60, 90, 120, 180, 240):
        ws = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=sec)
        if ws:
            r = run_strategy(ws, THR, 2, False, f"sec={sec}")
            sec_rows.append(r)
    print_table("", sec_rows)

    # ========= CUT 6+8: combined sec × agreement =========
    print()
    print("=" * 78)
    print("CUT 6+8 — sec × min_agreement matrix (PnL$)")
    print("=" * 78)
    print(f"  {'sec':<5} {'min_N=2':>10} {'min_N=3':>10}")
    for sec in (60, 90, 120, 180, 240):
        ws = build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=sec)
        out = []
        for n in (2, 3):
            r = run_strategy(ws, THR, n, False)
            out.append(f"{r['PnL$']:>10}")
        print(f"  {sec:<5}" + "".join(out))

    # ========= CUT 9: distance from target =========
    print()
    print("=" * 78)
    print("CUT 9 — distance from target at sec=90 (using poly's distance, loose N=2)")
    print("=" * 78)
    def dist_bucket(d):
        if d is None: return "?"
        a = abs(d)
        if a < 20: return "0-20"
        if a < 50: return "20-50"
        if a < 100: return "50-100"
        if a < 200: return "100-200"
        return "200+"
    by_dist = defaultdict(lambda: {"fires": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in windows:
        d = decide_row(r, THR, 2, False)
        if not d:
            continue
        side, plat, price, _, _ = d
        b = by_dist[dist_bucket(r["poly"]["dist"])]
        b["fires"] += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: b["wins"] += 1
        else: b["losses"] += 1
        b["pnl"] += p
    print(f"  {'dist':<10} {'fires':<6} {'wins':<5} {'losses':<7} {'win%':<7} {'PnL$':<8}")
    for k in ["0-20", "20-50", "50-100", "100-200", "200+", "?"]:
        if k not in by_dist: continue
        b = by_dist[k]
        res = b["wins"] + b["losses"]
        wr = (100 * b["wins"] / res) if res else 0
        print(f"  {k:<10} {b['fires']:<6} {b['wins']:<5} {b['losses']:<7} {wr:<6.1f}% {b['pnl']:+.2f}")

    # ========= CUT 10: cross-oracle gap =========
    print()
    print("=" * 78)
    print("CUT 10 — cross-oracle gap (poly_target - pred_target), loose N=2")
    print("=" * 78)
    def gap_bucket(g):
        if g is None: return "?"
        a = abs(g)
        if a < 20: return "0-20"
        if a < 50: return "20-50"
        if a < 100: return "50-100"
        return "100+"
    by_gap = defaultdict(lambda: {"fires": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in windows:
        d = decide_row(r, THR, 2, False)
        if not d:
            continue
        side, plat, price, _, _ = d
        gap = (r["poly_target"] - r["pred_target"]) if (r["poly_target"] and r["pred_target"]) else None
        b = by_gap[gap_bucket(gap)]
        b["fires"] += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: b["wins"] += 1
        else: b["losses"] += 1
        b["pnl"] += p
    print(f"  {'gap$':<8} {'fires':<6} {'wins':<5} {'losses':<7} {'win%':<7} {'PnL$':<8}")
    for k in ["0-20", "20-50", "50-100", "100+", "?"]:
        if k not in by_gap: continue
        b = by_gap[k]
        res = b["wins"] + b["losses"]
        wr = (100 * b["wins"] / res) if res else 0
        print(f"  {k:<8} {b['fires']:<6} {b['wins']:<5} {b['losses']:<7} {wr:<6.1f}% {b['pnl']:+.2f}")

    # ========= CUT 11: price bucket =========
    print()
    print("=" * 78)
    print("CUT 11 — price bucket of fired trades, loose N=2")
    print("=" * 78)
    def price_bucket(p):
        if p < 0.50: return "cheap_<0.50"
        if p < 0.75: return "mid_0.50-0.75"
        return "exp_>=0.75"
    by_price = defaultdict(lambda: {"fires": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in windows:
        d = decide_row(r, THR, 2, False)
        if not d:
            continue
        side, plat, price, _, _ = d
        b = by_price[price_bucket(price)]
        b["fires"] += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: b["wins"] += 1
        else: b["losses"] += 1
        b["pnl"] += p
    print(f"  {'bucket':<14} {'fires':<6} {'wins':<5} {'losses':<7} {'win%':<7} {'PnL$':<8}")
    for k in ["cheap_<0.50", "mid_0.50-0.75", "exp_>=0.75"]:
        if k not in by_price: continue
        b = by_price[k]
        res = b["wins"] + b["losses"]
        wr = (100 * b["wins"] / res) if res else 0
        print(f"  {k:<14} {b['fires']:<6} {b['wins']:<5} {b['losses']:<7} {wr:<6.1f}% {b['pnl']:+.2f}")

    # ========= CUT 13: NYC time of day =========
    print()
    print("=" * 78)
    print("CUT 13 — NYC hour of day (loose N=2)")
    print("=" * 78)
    by_hour = defaultdict(lambda: {"fires": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for r in windows:
        d = decide_row(r, THR, 2, False)
        if not d: continue
        side, plat, price, _, _ = d
        # NYC = UTC - 4 (EDT in May)
        nyc = (datetime.utcfromtimestamp(r["ep"]) - timedelta(hours=4)).hour
        b = by_hour[nyc]
        b["fires"] += 1
        p = pnl(side, plat, price, r)
        if p is None: continue
        if p > 0: b["wins"] += 1
        else: b["losses"] += 1
        b["pnl"] += p
    print(f"  {'hour':<5} {'fires':<6} {'wins':<5} {'losses':<7} {'win%':<7} {'PnL$':<8}")
    for h in sorted(by_hour.keys()):
        b = by_hour[h]
        res = b["wins"] + b["losses"]
        wr = (100 * b["wins"] / res) if res else 0
        print(f"  {h:<5} {b['fires']:<6} {b['wins']:<5} {b['losses']:<7} {wr:<6.1f}% {b['pnl']:+.2f}")

    # ========= CUT 15: pairwise =========
    print()
    print("=" * 78)
    print("CUT 15 — pairwise agreement (just 2 platforms required, the third can be anything)")
    print("=" * 78)
    pairs = [("poly", "pred"), ("poly", "lim"), ("pred", "lim")]
    for p1, p2 in pairs:
        fires = wins = losses = 0
        pnl_sum = 0.0
        prices = []
        for r in windows:
            sources = {"poly": (r["poly"], True), "pred": (r["pred"], True), "lim": (r["lim"], False)}
            # require p1 and p2 to both agree on same direction
            up1, dn1 = vote_classify(sources[p1][0], THR)
            up2, dn2 = vote_classify(sources[p2][0], THR)
            side = None
            if up1 and up2: side = "UP"
            elif dn1 and dn2: side = "DOWN"
            if not side: continue
            # choose cheaper of tradeable platforms in the pair
            tradeable_plats = [p for p in (p1, p2) if sources[p][1]]
            if not tradeable_plats: continue
            plat_prices = []
            for p in tradeable_plats:
                snap = sources[p][0]
                price = snap["up" if side == "UP" else "down"]
                if price: plat_prices.append((p, price))
            if not plat_prices: continue
            plat, price = min(plat_prices, key=lambda x: x[1])
            fires += 1
            prices.append(price)
            res = pnl(side, plat, price, r)
            if res is None: continue
            if res > 0: wins += 1
            else: losses += 1
            pnl_sum += res
        ap = sum(prices) / len(prices) if prices else 0
        wr = (100 * wins / (wins + losses)) if (wins + losses) else 0
        print(f"  {p1}+{p2}: fires={fires} wins={wins} losses={losses} win%={wr:.1f}% avg_p={ap:.3f} PnL${pnl_sum:+.2f}")

    # ========= CUT 16: single-platform predictive power =========
    print()
    print("=" * 78)
    print("CUT 16 — single-platform knowledge score (when X votes UP alone or with others)")
    print("=" * 78)
    for plat in ("poly", "pred", "lim"):
        up_corr = up_n = dn_corr = dn_n = 0
        for r in windows:
            snap = r[plat]
            up, dn = vote_classify(snap, THR)
            actual = outcome_for(r, plat) if plat in ("poly", "pred") else r["lim_out"]
            if up:
                up_n += 1
                if actual == "UP": up_corr += 1
            if dn:
                dn_n += 1
                if actual == "DOWN": dn_corr += 1
        if up_n:
            print(f"  {plat:6s} votes UP: {up_corr}/{up_n} = {100*up_corr/up_n:.1f}% correct")
        if dn_n:
            print(f"  {plat:6s} votes DN: {dn_corr}/{dn_n} = {100*dn_corr/dn_n:.1f}% correct")


if __name__ == "__main__":
    main()
