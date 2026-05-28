#!/usr/bin/env python3
"""Analyze CONSENSUS_BTC_V1 history: cut by N-platform agreement.

For each window the bot evaluated, count how many of the 5 platforms
priced the SAME direction as likely (>= THR). Then ask:
  - if we'd required 3 / 4 / 5 platforms to agree before firing,
    how many wins and losses would we have?
"""
import csv
from collections import defaultdict

BASE = "/root/live/consensus_v1"
THR = 0.60


def load(name):
    out = {}
    p = f"{BASE}/consensus_v1_{name}.csv"
    try:
        with open(p) as f:
            for r in csv.DictReader(f):
                out[r["window_epoch"]] = r
    except FileNotFoundError:
        pass
    return out


def f(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def main():
    decisions = load("decisions")
    lim = load("limitless")
    gem = load("gemini")
    kal = load("kalshi")
    outcomes = load("outcomes")
    trades_by_ep = {}
    with open(f"{BASE}/consensus_v1_trades.csv") as fh:
        for r in csv.DictReader(fh):
            trades_by_ep[r["window_epoch"]] = r

    print(f"Windows evaluated: {len(decisions)}")
    print(f"Trades fired: {len(trades_by_ep)}")
    print()

    # For each window, compute direction vote of each platform
    # Returns dict[window_epoch] -> {
    #   'up_votes': [(plat, ask)], 'dn_votes': [(plat, ask)],
    #   'majority': 'UP'/'DOWN'/None, 'majority_n': int, 'cheapest': (plat, ask),
    #   'outcome': ...
    # }
    rows = []
    for we, d in decisions.items():
        poly_up = f(d.get("poly_up_ask"))
        poly_dn = f(d.get("poly_down_ask"))
        pred_y = f(d.get("predict_yes_ask"))
        pred_n = f(d.get("predict_no_ask"))
        lim_y = f((lim.get(we) or {}).get("lim_yes_ask"))
        lim_n = f((lim.get(we) or {}).get("lim_no_ask"))
        gem_y = f((gem.get(we) or {}).get("gem_yes_ask"))
        gem_n = f((gem.get(we) or {}).get("gem_no_ask"))
        kal_y = f((kal.get(we) or {}).get("kal_yes_ask"))
        kal_n = f((kal.get(we) or {}).get("kal_no_ask"))

        up_votes = []
        dn_votes = []
        for plat, y, n in [
            ("poly", poly_up, poly_dn),
            ("pred", pred_y, pred_n),
            ("lim", lim_y, lim_n),
            ("gem", gem_y, gem_n),
            ("kal", kal_y, kal_n),
        ]:
            if y is not None and y >= THR:
                up_votes.append((plat, y))
            if n is not None and n >= THR:
                dn_votes.append((plat, n))

        # majority direction
        if len(up_votes) > len(dn_votes):
            side = "UP"; votes = up_votes
        elif len(dn_votes) > len(up_votes):
            side = "DOWN"; votes = dn_votes
        else:
            side = None; votes = []
        n_agree = len(votes)
        # cheapest among TRADEABLE platforms only (poly + predict)
        tradeable_votes = [v for v in votes if v[0] in ("poly", "pred")]
        cheapest = min(tradeable_votes, key=lambda x: x[1]) if tradeable_votes else None

        # outcomes
        o = outcomes.get(we) or {}
        outs = {
            "poly": o.get("poly_outcome"),
            "pred": o.get("pred_outcome"),
            "lim": o.get("lim_outcome"),
            "gem": o.get("gem_outcome"),
            "kal": o.get("kal_outcome"),
        }

        rows.append({
            "we": we, "side": side, "n_agree": n_agree,
            "up_votes": up_votes, "dn_votes": dn_votes,
            "cheapest": cheapest, "outcomes": outs,
        })

    # Distribution of agreement counts
    by_n = defaultdict(int)
    for r in rows:
        by_n[r["n_agree"]] += 1
    print("=== AGREEMENT DISTRIBUTION (out of 5 platforms) ===")
    for n in sorted(by_n.keys(), reverse=True):
        print(f"  {n}/5 agree: {by_n[n]} windows ({100*by_n[n]/len(rows):.1f}%)")
    print()

    # New breakdown: for each window, count UP_votes, DN_votes, silent
    print("=== full vote pattern (UP|DOWN|silent out of 5) ===")
    patterns = defaultdict(int)
    for r in rows:
        up_n = len(r["up_votes"]); dn_n = len(r["dn_votes"])
        silent = 5 - up_n - dn_n
        key = (up_n, dn_n, silent)
        patterns[key] += 1
    print(f"  {'UP':<3} {'DN':<3} {'SIL':<3}  count")
    for k in sorted(patterns.keys(), key=lambda x: (-x[0]-x[1], -x[0])):
        u, d, s = k
        print(f"  {u:<3} {d:<3} {s:<3}  {patterns[k]}")
    print()

    # For each min-agreement threshold, simulate "would have bought if N+ agree"
    print("=" * 88)
    print("BACKTEST (loose): fire if N+ agree AND > opposite (current rule)")
    print("=" * 88)
    print(f"{'min_N':<6} {'tot_fires':>9} {'wins':>5} {'losses':>7} {'pending':>8} {'win%':>7} {'avg_p':>6} {'PnL$':>8}")

    for min_n in (2, 3, 4, 5):
        fires = wins = losses = pending = 0
        prices_paid = []
        pnl = 0.0
        for r in rows:
            if r["side"] is None or r["n_agree"] < min_n:
                continue
            if not r["cheapest"]:
                continue
            fires += 1
            plat, price = r["cheapest"]
            prices_paid.append(price)
            outcome = r["outcomes"].get(plat)
            if not outcome:
                pending += 1
                continue
            if outcome == r["side"]:
                wins += 1
                pnl += (2.0 / price) - 2.0
            else:
                losses += 1
                pnl -= 2.0
        resolved = wins + losses
        wr = (100 * wins / resolved) if resolved else 0
        avg_p = sum(prices_paid) / len(prices_paid) if prices_paid else 0
        print(f"{min_n:<6} {fires:>9} {wins:>5} {losses:>7} {pending:>8} {wr:>6.1f}% {avg_p:>6.3f} {pnl:>+8.2f}")
    print()

    # STRICT rule: fire only if N+ agree AND 0 oppose AND opposite_votes == 0
    print("=" * 88)
    print("BACKTEST (strict): fire if N+ agree AND opposite votes == 0 (zero dissent)")
    print("=" * 88)
    print(f"{'min_N':<6} {'tot_fires':>9} {'wins':>5} {'losses':>7} {'pending':>8} {'win%':>7} {'avg_p':>6} {'PnL$':>8}")

    for min_n in (2, 3, 4, 5):
        fires = wins = losses = pending = 0
        prices_paid = []
        pnl = 0.0
        for r in rows:
            if r["side"] is None or r["n_agree"] < min_n:
                continue
            if not r["cheapest"]:
                continue
            opposite_votes = len(r["dn_votes"]) if r["side"] == "UP" else len(r["up_votes"])
            if opposite_votes > 0:
                continue  # any dissent kills the trade
            fires += 1
            plat, price = r["cheapest"]
            prices_paid.append(price)
            outcome = r["outcomes"].get(plat)
            if not outcome:
                pending += 1
                continue
            if outcome == r["side"]:
                wins += 1
                pnl += (2.0 / price) - 2.0
            else:
                losses += 1
                pnl -= 2.0
        resolved = wins + losses
        wr = (100 * wins / resolved) if resolved else 0
        avg_p = sum(prices_paid) / len(prices_paid) if prices_paid else 0
        print(f"{min_n:<6} {fires:>9} {wins:>5} {losses:>7} {pending:>8} {wr:>6.1f}% {avg_p:>6.3f} {pnl:>+8.2f}")
    print()

    # Detail per-window: what each loss looked like in terms of agreement
    print("=" * 72)
    print("8 LOSSES — agreement counts at decision time")
    print("=" * 72)
    print(f"{'win':<12} {'side':<5} {'plat':<5} {'price':<6} {'UP|DN|SIL':<10} {'same_dir_votes':<35} {'opposite_dir_votes':<25}")
    for r in rows:
        if not r["side"]:
            continue
        if r["we"] not in trades_by_ep:
            continue
        t = trades_by_ep[r["we"]]
        plat = t["platform"]
        outcome = r["outcomes"].get(plat)
        if outcome and outcome != t["side"]:
            u_n = len(r["up_votes"]); d_n = len(r["dn_votes"]); s_n = 5 - u_n - d_n
            same = r["up_votes"] if r["side"] == "UP" else r["dn_votes"]
            opp = r["dn_votes"] if r["side"] == "UP" else r["up_votes"]
            same_str = ",".join(f"{p}={a:.2f}" for p, a in same)
            opp_str = ",".join(f"{p}={a:.2f}" for p, a in opp) or "(none)"
            print(f"{r['we']:<12} {r['side']:<5} {plat:<5} {float(t['price']):<6.2f} {u_n}|{d_n}|{s_n}      {same_str:<35} {opp_str}")
    print()

    # Windows where bot DID NOT buy but where 3+/4+/5 platforms agreed on a direction
    print("=" * 72)
    print("WINDOWS NOT BOUGHT but with 3+ AGREEMENT — opportunity missed?")
    print("=" * 72)
    print(f"{'win':<12} {'side':<5} {'n_agree':<8} {'outcome':<10} {'cheapest':<15} {'would_win':<10}")
    missed_wins = missed_losses = 0
    for r in rows:
        if r["side"] is None or r["n_agree"] < 3:
            continue
        if r["we"] in trades_by_ep:
            continue  # was bought
        cheap = r["cheapest"]
        if not cheap:
            continue
        plat, price = cheap
        outcome = r["outcomes"].get(plat)
        if not outcome:
            continue
        would_win = "WIN" if outcome == r["side"] else "LOSS"
        if would_win == "WIN":
            missed_wins += 1
        else:
            missed_losses += 1
        print(f"{r['we']:<12} {r['side']:<5} {r['n_agree']}/5     {outcome:<10} {plat}@{price:.2f}        {would_win}")
    print(f"\nNot-bought-but-3plus-agree: {missed_wins} would-be-wins, {missed_losses} would-be-losses")


if __name__ == "__main__":
    main()
