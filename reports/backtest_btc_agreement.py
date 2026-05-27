#!/usr/bin/env python3
"""BTC 3-platform direction agreement.

For each 5-min window present on all 3 platforms, take a reference second
and compare the implied UP probability (yes_ask) across platforms.

>0.50 = market favors UP
<0.50 = market favors DOWN

Report:
- # windows where all 3 platforms exist
- # unanimous UP / unanimous DOWN / disagree
- when they disagree, who was right (using Poly outcome)
- gap distribution between platforms
"""
import csv
import os
from collections import defaultdict

POLY = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PREDICT = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM = "/root/data_limitless_btc_5m/combined_per_second.csv"

REF_SEC = 60  # snapshot at sec=60 from window open
WIN_HALF = 15  # use median of yes_ask in [REF_SEC-15, REF_SEC+15]


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2


def load_poly_at_ref():
    """Returns dict[window_epoch] -> median up_ask in [REF_SEC-WIN_HALF, REF_SEC+WIN_HALF]."""
    out = defaultdict(list)
    if not os.path.exists(POLY):
        return {}
    with open(POLY) as f:
        for r in csv.DictReader(f):
            try:
                ep = int(r["market_epoch"])
                sec = int(r["sec_from_start"])
                if abs(sec - REF_SEC) > WIN_HALF:
                    continue
                ua = float(r.get("up_ask") or 0)
                if ua <= 0:
                    continue
                out[ep].append(ua)
            except (ValueError, KeyError):
                continue
    return {ep: _median(v) for ep, v in out.items() if v}


def load_predict_at_ref():
    """Returns dict[window_epoch] -> yes_ask at REF_SEC."""
    out = {}
    if not os.path.exists(PREDICT):
        return out
    by_market = defaultdict(list)
    with open(PREDICT) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if not rows:
            continue
        try:
            window_ep = int(rows[0].get("market_open_epoch") or 0)
        except ValueError:
            continue
        if not window_ep:
            continue
        samples = []
        for r in rows:
            try:
                sec = int(r.get("sec_from_open") or -1)
                if abs(sec - REF_SEC) > WIN_HALF:
                    continue
                ya = float(r.get("yes_ask") or 0)
                if ya <= 0:
                    continue
                samples.append(ya)
            except (ValueError, KeyError):
                continue
        if samples:
            out[window_ep] = _median(samples)
    return out


def load_lim_at_ref():
    """Returns dict[window_epoch] -> yes_ask at REF_SEC."""
    out = {}
    if not os.path.exists(LIM):
        return out
    by_market = defaultdict(list)
    with open(LIM) as f:
        for r in csv.DictReader(f):
            mid = r.get("market_id")
            if mid:
                by_market[mid].append(r)
    for mid, rows in by_market.items():
        if len(rows) < 5:
            continue
        try:
            first_ts = int(rows[0]["epoch_sec"])
        except (ValueError, KeyError):
            continue
        # use expirationTimestamp from markets.csv if available — more accurate
        window_ep = (first_ts // 300) * 300
        samples = []
        for r in rows:
            try:
                sec = int(r["epoch_sec"]) - first_ts
                if abs(sec - REF_SEC) > WIN_HALF:
                    continue
                ya = float(r.get("best_ask") or 0)
                if ya <= 0:
                    continue
                samples.append(ya)
            except (ValueError, KeyError):
                continue
        if samples:
            out[window_ep] = _median(samples)
    return out


def load_poly_outcomes():
    out = {}
    if not os.path.exists(POLY_OUT):
        return out
    with open(POLY_OUT) as f:
        for r in csv.DictReader(f):
            try:
                out[int(r["market_epoch"])] = r.get("winner_side", "")
            except (ValueError, KeyError):
                pass
    return out


def label(prob):
    if prob > 0.50:
        return "UP"
    if prob < 0.50:
        return "DN"
    return "EQ"


def main():
    poly = load_poly_at_ref()
    predict = load_predict_at_ref()
    lim = load_lim_at_ref()
    outcomes = load_poly_outcomes()

    print(f"Poly windows at sec={REF_SEC}: {len(poly)}")
    print(f"Predict windows at sec={REF_SEC}: {len(predict)}")
    print(f"Limitless windows at sec={REF_SEC}: {len(lim)}")

    # match windows by exact epoch (5-min boundary)
    common_pp = set(poly) & set(predict)
    common_pl = set(poly) & set(lim)
    common_pred_lim = set(predict) & set(lim)
    common_all3 = common_pp & set(lim)

    print(f"Poly+Predict common: {len(common_pp)}")
    print(f"Poly+Limitless common: {len(common_pl)}")
    print(f"Predict+Limitless common: {len(common_pred_lim)}")
    print(f"All 3 common: {len(common_all3)}")
    print()

    # === 2-PLATFORM AGREEMENT ===
    for name, common, p1_name, p1, p2_name, p2 in [
        ("Poly vs Predict", common_pp, "poly", poly, "predict", predict),
        ("Poly vs Limitless", common_pl, "poly", poly, "lim", lim),
        ("Predict vs Limitless", common_pred_lim, "predict", predict, "lim", lim),
    ]:
        if not common:
            continue
        agree = disagree = 0
        gaps = []
        for ep in common:
            a, b = p1[ep], p2[ep]
            la, lb = label(a), label(b)
            if la == lb:
                agree += 1
            else:
                disagree += 1
            gaps.append(abs(a - b))
        gaps.sort()
        n = len(gaps)
        median = gaps[n // 2]
        p90 = gaps[int(n * 0.9)] if n > 10 else gaps[-1]
        mx = gaps[-1]
        print(f"=== {name} ===")
        print(f"  N={n}  agree={agree} ({100*agree/n:.1f}%)  disagree={disagree} ({100*disagree/n:.1f}%)")
        print(f"  price gap: median={median:.3f}  p90={p90:.3f}  max={mx:.3f}")
        print()

    # === 3-PLATFORM AGREEMENT ===
    if common_all3:
        print("=" * 70)
        print("3-PLATFORM DIRECTION AGREEMENT")
        print("=" * 70)
        unan_up = unan_dn = split = 0
        unan_up_won = unan_dn_won = 0
        unan_up_with_outcome = unan_dn_with_outcome = 0
        split_breakdown = defaultdict(int)
        for ep in common_all3:
            a, b, c = poly[ep], predict[ep], lim[ep]
            labs = (label(a), label(b), label(c))
            if labs == ("UP", "UP", "UP"):
                unan_up += 1
                if ep in outcomes:
                    unan_up_with_outcome += 1
                    if outcomes[ep] == "UP":
                        unan_up_won += 1
            elif labs == ("DN", "DN", "DN"):
                unan_dn += 1
                if ep in outcomes:
                    unan_dn_with_outcome += 1
                    if outcomes[ep] == "DOWN":
                        unan_dn_won += 1
            else:
                split += 1
                split_breakdown[labs] += 1
        n3 = len(common_all3)
        print(f"  N={n3}")
        print(f"  Unanimous UP:   {unan_up:>4} ({100*unan_up/n3:.1f}%)")
        print(f"  Unanimous DOWN: {unan_dn:>4} ({100*unan_dn/n3:.1f}%)")
        print(f"  Split:          {split:>4} ({100*split/n3:.1f}%)")
        print()
        if unan_up_with_outcome:
            print(f"  When unanimous UP   -> actually UP: {unan_up_won}/{unan_up_with_outcome} = {100*unan_up_won/unan_up_with_outcome:.1f}%")
        if unan_dn_with_outcome:
            print(f"  When unanimous DOWN -> actually DOWN: {unan_dn_won}/{unan_dn_with_outcome} = {100*unan_dn_won/unan_dn_with_outcome:.1f}%")
        print()
        print("  Split breakdown (poly,predict,lim):")
        for labs, c in sorted(split_breakdown.items(), key=lambda kv: -kv[1]):
            print(f"    {labs}: {c}")
        print()

        # show biggest 3-platform gaps as arb candidates
        print("=" * 70)
        print("TOP 20 widest 3-platform gaps (max_ask - min_ask of YES/UP)")
        print("=" * 70)
        rows = []
        for ep in common_all3:
            a, b, c = poly[ep], predict[ep], lim[ep]
            gap = max(a, b, c) - min(a, b, c)
            outcome = outcomes.get(ep, "?")
            rows.append((gap, ep, a, b, c, outcome))
        rows.sort(reverse=True)
        print(f"{'epoch':<12} {'poly':>6} {'pred':>6} {'lim':>6} {'gap':>6} {'won':>5}")
        for gap, ep, a, b, c, w in rows[:20]:
            print(f"{ep:<12} {a:>6.3f} {b:>6.3f} {c:>6.3f} {gap:>6.3f} {w:>5}")


if __name__ == "__main__":
    main()
