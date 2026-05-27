#!/usr/bin/env python3
"""3-platform BTC outcome comparison — each platform scored on its own oracle.

Poly outcome = Chainlink-based winner from market_outcomes.csv
Limitless outcome = Binance close vs Limitless's own target_price
Predict outcome = Binance close vs Predict's own strike

Then match by 5-min window epoch and compare:
- Do all 3 oracles agree on UP/DOWN?
- Where do they diverge?
"""
import csv
import os
from collections import defaultdict

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
POLY_MK = "/root/research/multi_coin/data_btc_5m_research/markets.csv"
PRED = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK = "/root/data_limitless_btc_5m/markets.csv"


def load_poly_outcomes():
    """Returns dict[window_epoch] -> 'UP'/'DOWN' from market_outcomes.csv (Chainlink)."""
    out = {}
    with open(POLY_OUT) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"])
                w = r.get("winner_side", "")
                if w in ("UP", "DOWN"):
                    out[ep] = w
            except (ValueError, KeyError):
                continue
    return out


def load_predict_outcomes():
    """Returns dict[window_epoch] -> 'UP'/'DOWN' from Predict's binance vs strike."""
    out = {}
    by_market = defaultdict(list)
    with open(PRED) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if len(rows) < 10:
            continue
        try:
            ep = int(rows[0].get("market_open_epoch") or 0)
            strike = float(rows[0].get("strike") or 0)
        except (ValueError, TypeError):
            continue
        if not ep or not strike:
            continue
        last_binance = None
        for r in reversed(rows):
            try:
                v = float(r.get("binance_now") or 0)
                if v > 0:
                    last_binance = v
                    break
            except (ValueError, TypeError):
                continue
        if last_binance is None:
            continue
        out[ep] = "UP" if last_binance > strike else "DOWN"
    return out


def load_lim_outcomes():
    """Returns dict[window_epoch] -> 'UP'/'DOWN' from Limitless's binance vs target.

    Match window by expirationTimestamp: lim market with expiration == X corresponds
    to Poly window starting at X-300.
    """
    # Get market expirations from markets.csv
    exp_by_mid = {}
    if os.path.exists(LIM_MK):
        with open(LIM_MK) as f:
            for r in csv.DictReader(f):
                mid = r.get("market_id")
                try:
                    exp_ms = int(r.get("expirationTimestamp") or 0)
                except (ValueError, TypeError):
                    continue
                if mid and exp_ms:
                    exp_by_mid[mid] = exp_ms // 1000

    by_market = defaultdict(list)
    with open(LIM) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)

    out = {}
    for mid, rows in by_market.items():
        if len(rows) < 10:
            continue
        try:
            target = float(rows[0].get("target_price") or 0)
        except (ValueError, TypeError):
            continue
        if not target:
            continue
        exp = exp_by_mid.get(mid)
        if not exp:
            continue
        window_ep = exp - 300  # window start = expiration - 5min
        last_binance = None
        for r in reversed(rows):
            try:
                v = float(r.get("binance_now") or 0)
                if v > 0:
                    last_binance = v
                    break
            except (ValueError, TypeError):
                continue
        if last_binance is None:
            continue
        out[window_ep] = "UP" if last_binance > target else "DOWN"
    return out


def main():
    poly = load_poly_outcomes()
    pred = load_predict_outcomes()
    lim = load_lim_outcomes()

    print(f"Poly outcomes (Chainlink):  {len(poly)}")
    print(f"Predict outcomes (Binance): {len(pred)}")
    print(f"Lim outcomes (Binance):     {len(lim)}")

    common3 = set(poly) & set(pred) & set(lim)
    print(f"All 3 platforms common windows: {len(common3)}")
    print()

    # ========= TRUE AGREEMENT =========
    unan_up = unan_dn = split = 0
    pair_pp = pair_pl = pair_predlim = 0
    pp_n = pl_n = pn_n = 0
    poly_only_up = pred_only_up = lim_only_up = 0
    splits = defaultdict(int)

    for ep in common3:
        p, pr, l = poly[ep], pred[ep], lim[ep]
        if p == pr == l == "UP":
            unan_up += 1
        elif p == pr == l == "DOWN":
            unan_dn += 1
        else:
            split += 1
            splits[(p, pr, l)] += 1

    n3 = len(common3)
    print("=" * 70)
    print("REAL 3-PLATFORM OUTCOME AGREEMENT (each platform on its own oracle)")
    print("=" * 70)
    print(f"  N={n3}")
    print(f"  Unanimous UP:    {unan_up:>4} ({100*unan_up/n3:.1f}%)")
    print(f"  Unanimous DOWN:  {unan_dn:>4} ({100*unan_dn/n3:.1f}%)")
    print(f"  Disagree:        {split:>4} ({100*split/n3:.1f}%)")
    print()
    print("  Split patterns (poly, predict, lim):")
    for k, v in sorted(splits.items(), key=lambda kv: -kv[1]):
        print(f"    {k}: {v}")
    print()

    # ========= PAIRWISE =========
    for name, a_d, b_d in [
        ("Poly vs Predict", poly, pred),
        ("Poly vs Limitless", poly, lim),
        ("Predict vs Limitless", pred, lim),
    ]:
        common = set(a_d) & set(b_d)
        if not common:
            continue
        agree = sum(1 for ep in common if a_d[ep] == b_d[ep])
        n = len(common)
        print(f"{name:<25} N={n:>4}  agree={agree} ({100*agree/n:.1f}%)")
    print()

    # ========= ARB OPPORTUNITY =========
    print("=" * 70)
    print("ARB OPPORTUNITY — windows where oracles RESOLVE DIFFERENTLY")
    print("=" * 70)
    print("These are windows where buying YES on one and YES on another (or YES+NO)")
    print("on opposite platforms guarantees one wins.")
    print()
    poly_vs_lim = set(poly) & set(lim)
    diverge = [ep for ep in poly_vs_lim if poly[ep] != lim[ep]]
    print(f"Poly vs Lim diverge: {len(diverge)} of {len(poly_vs_lim)} = {100*len(diverge)/len(poly_vs_lim):.1f}%")

    poly_vs_pred = set(poly) & set(pred)
    diverge2 = [ep for ep in poly_vs_pred if poly[ep] != pred[ep]]
    print(f"Poly vs Predict diverge: {len(diverge2)} of {len(poly_vs_pred)} = {100*len(diverge2)/len(poly_vs_pred):.1f}%")

    pred_vs_lim = set(pred) & set(lim)
    diverge3 = [ep for ep in pred_vs_lim if pred[ep] != lim[ep]]
    print(f"Predict vs Lim diverge: {len(diverge3)} of {len(pred_vs_lim)} = {100*len(diverge3)/len(pred_vs_lim):.1f}%")
    print()

    # ========= ARB-COVERED COINFLIP =========
    # If Poly says UP (Chainlink) and Lim says DOWN (Binance), buying UP on Poly + DOWN on Lim
    # gives one winner. Net depends on prices paid.
    print("Direction of Poly-vs-Lim divergence (Poly first):")
    counts = defaultdict(int)
    for ep in diverge:
        counts[(poly[ep], lim[ep])] += 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  Poly={k[0]} Lim={k[1]}: {v}")


if __name__ == "__main__":
    main()
