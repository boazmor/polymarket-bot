#!/usr/bin/env python3
"""Apply ALL recommended filters from project_v1_historical_findings_2026_05_27
and report combined result at $1 per trade.

Filters applied jointly:
  - sec=90 ref
  - min_N >= 3 platforms agreeing
  - Poly must be in agreeing set
  - |distance| NOT in [50,100]
  - NYC hour NOT in {3, 9, 11, 14}
  - all 3 platforms voting (0 silent) — implicit via min_N=3
"""
import sys
sys.path.insert(0, "/root/reports")
import importlib.util
spec = importlib.util.spec_from_file_location("bt", "/root/reports/backtest_v1_historical.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

from datetime import datetime, timezone, timedelta

INVEST = 1.0
BAD_HOURS = {3, 9, 11, 14}
BAD_DIST_LO, BAD_DIST_HI = 50, 100


def main():
    poly_outs = bt.load_poly_outcomes()
    poly_snaps = bt.build_poly_snapshots()
    pred_snaps, pred_outs = bt.build_predict_snapshots()
    lim_snaps, lim_outs = bt.build_lim_snapshots()
    windows = bt.build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    n_windows = len(windows)

    # range of dates
    eps = [r["ep"] for r in windows]
    t_min = datetime.fromtimestamp(min(eps), tz=timezone.utc)
    t_max = datetime.fromtimestamp(max(eps), tz=timezone.utc)
    span_hours = (max(eps) - min(eps)) / 3600
    span_days = span_hours / 24

    print(f"Data span: {t_min} to {t_max}")
    print(f"  {span_days:.2f} days = {span_hours:.0f} hours")
    print(f"  total windows: {n_windows}")
    print()

    fires = wins = losses = 0
    pnl = 0.0
    prices_used = []
    filters_blocked = {
        "min_N<3": 0,
        "no_consensus": 0,
        "poly_not_in_set": 0,
        "distance_50_100": 0,
        "bad_nyc_hour": 0,
    }

    for r in windows:
        # Apply each filter and count what blocks
        # 1) compute votes
        sources = {
            "poly": (r["poly"], True),
            "pred": (r["pred"], True),
            "lim":  (r["lim"], False),
        }
        up_set = set()
        dn_set = set()
        for name, (snap, _) in sources.items():
            up, dn = bt.vote_classify(snap, bt.THR)
            if up: up_set.add(name)
            if dn: dn_set.add(name)

        if len(up_set) >= 3 and len(up_set) > len(dn_set):
            side, votes = "UP", up_set
        elif len(dn_set) >= 3 and len(dn_set) > len(up_set):
            side, votes = "DOWN", dn_set
        else:
            # didn't meet min_N=3 (since we have only 3 platforms, this means not all 3 agree)
            if max(len(up_set), len(dn_set)) < 3:
                filters_blocked["min_N<3"] += 1
            else:
                filters_blocked["no_consensus"] += 1
            continue

        # 2) require poly in agreeing set
        if "poly" not in votes:
            filters_blocked["poly_not_in_set"] += 1
            continue

        # 3) distance filter (use poly's distance)
        dist = r["poly"].get("dist")
        if dist is not None:
            ad = abs(dist)
            if BAD_DIST_LO <= ad <= BAD_DIST_HI:
                filters_blocked["distance_50_100"] += 1
                continue

        # 4) NYC hour filter
        nyc_hr = (datetime.fromtimestamp(r["ep"], tz=timezone.utc) - timedelta(hours=4)).hour
        if nyc_hr in BAD_HOURS:
            filters_blocked["bad_nyc_hour"] += 1
            continue

        # 5) pick cheapest TRADEABLE in the agreeing set (poly or pred)
        candidates = []
        for name in votes:
            if name in ("poly", "pred"):
                snap = sources[name][0]
                price = snap["up" if side == "UP" else "down"]
                if price: candidates.append((name, price))
        if not candidates:
            continue
        plat, price = min(candidates, key=lambda x: x[1])

        fires += 1
        prices_used.append(price)
        # compute pnl per chosen platform's oracle
        actual = bt.outcome_for(r, plat)
        if actual is None:
            continue
        if actual == side:
            wins += 1
            pnl += (INVEST / price) - INVEST
        else:
            losses += 1
            pnl -= INVEST

    resolved = wins + losses
    wr = (100 * wins / resolved) if resolved else 0
    avg_p = sum(prices_used) / len(prices_used) if prices_used else 0
    roi_pct = (100 * pnl / (fires * INVEST)) if fires else 0
    trades_per_day = fires / span_days
    pnl_per_day = pnl / span_days

    print("=" * 78)
    print(f"COMPOSITE BACKTEST at ${INVEST}/trade")
    print("=" * 78)
    print(f"  Fires (after all filters): {fires}")
    print(f"  Wins / Losses:             {wins} / {losses}")
    print(f"  Win rate:                  {wr:.1f}%")
    print(f"  Avg price paid:            {avg_p:.3f}")
    print(f"  Total PnL:                 ${pnl:+.2f}")
    print(f"  ROI per trade:             {roi_pct:+.1f}%")
    print()
    print(f"  Trades per day:            {trades_per_day:.1f}")
    print(f"  PnL per day:               ${pnl_per_day:+.2f}")
    print()
    print(f"  At $10/trade  → ~${pnl_per_day*10:+.0f}/day")
    print(f"  At $50/trade  → ~${pnl_per_day*50:+.0f}/day")
    print(f"  At $100/trade → ~${pnl_per_day*100:+.0f}/day")
    print()
    print("=" * 78)
    print("FILTERS REJECTED HOW MANY WINDOWS")
    print("=" * 78)
    for k, v in filters_blocked.items():
        print(f"  {k:<22} {v:>4} windows rejected")
    print(f"  total windows analyzed: {n_windows}")
    print(f"  windows fired:          {fires}")
    print(f"  windows unresolved:     {fires - resolved}")


def main_no_lim():
    """Variant: drop Limitless entirely. Require poly+pred BOTH agree."""
    poly_outs = bt.load_poly_outcomes()
    poly_snaps = bt.build_poly_snapshots()
    pred_snaps, pred_outs = bt.build_predict_snapshots()
    lim_snaps, lim_outs = bt.build_lim_snapshots()
    windows = bt.build_windows(poly_snaps, poly_outs, pred_snaps, pred_outs, lim_snaps, lim_outs, ref_sec=90)
    n_windows = len(windows)
    eps = [r["ep"] for r in windows]
    span_days = (max(eps) - min(eps)) / 86400

    fires = wins = losses = 0
    pnl = 0.0
    prices = []
    blocked = {"no_pp_consensus": 0, "distance_50_100": 0, "bad_nyc_hour": 0}

    for r in windows:
        poly_up, poly_dn = bt.vote_classify(r["poly"], bt.THR)
        pred_up, pred_dn = bt.vote_classify(r["pred"], bt.THR)
        side = None
        if poly_up and pred_up:
            side = "UP"
        elif poly_dn and pred_dn:
            side = "DOWN"
        if not side:
            blocked["no_pp_consensus"] += 1
            continue
        # distance filter (use poly's distance)
        dist = r["poly"].get("dist")
        if dist is not None:
            ad = abs(dist)
            if BAD_DIST_LO <= ad <= BAD_DIST_HI:
                blocked["distance_50_100"] += 1
                continue
        nyc_hr = (datetime.fromtimestamp(r["ep"], tz=timezone.utc) - timedelta(hours=4)).hour
        if nyc_hr in BAD_HOURS:
            blocked["bad_nyc_hour"] += 1
            continue
        # pick cheaper of poly vs pred for the side
        poly_price = r["poly"]["up" if side == "UP" else "down"]
        pred_price = r["pred"]["up" if side == "UP" else "down"]
        if poly_price <= pred_price:
            plat, price = "poly", poly_price
        else:
            plat, price = "pred", pred_price
        fires += 1
        prices.append(price)
        actual = bt.outcome_for(r, plat)
        if actual is None:
            continue
        if actual == side:
            wins += 1
            pnl += (INVEST / price) - INVEST
        else:
            losses += 1
            pnl -= INVEST

    resolved = wins + losses
    wr = (100 * wins / resolved) if resolved else 0
    avg_p = sum(prices) / len(prices) if prices else 0
    roi_pct = (100 * pnl / (fires * INVEST)) if fires else 0
    trades_per_day = fires / span_days
    pnl_per_day = pnl / span_days

    print()
    print("=" * 78)
    print(f"VARIANT — DROP LIMITLESS entirely (only Poly+Pred consensus)")
    print("=" * 78)
    print(f"  Fires:        {fires}")
    print(f"  Wins/Losses:  {wins}/{losses}  ({wr:.1f}%)")
    print(f"  Avg price:    {avg_p:.3f}")
    print(f"  Total PnL:    ${pnl:+.2f}")
    print(f"  ROI per trade: {roi_pct:+.1f}%")
    print(f"  Trades/day:   {trades_per_day:.1f}")
    print(f"  PnL/day:      ${pnl_per_day:+.2f}")
    print(f"  At $50/trade: ~${pnl_per_day*50:+.0f}/day")
    print(f"  At $100/trade: ~${pnl_per_day*100:+.0f}/day")
    print()
    print("  Filter rejections:")
    for k, v in blocked.items():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
    main_no_lim()
