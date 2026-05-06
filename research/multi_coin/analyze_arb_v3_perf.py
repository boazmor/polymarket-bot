#!/usr/bin/env python3
"""Quick perf summary for arb_v3_3way_trades.csv."""
import csv
from datetime import datetime

PATH = "/root/arb_v3_3way_trades.csv"

closed = []
opened_first = opened_last = None
all_trades = 0

with open(PATH) as fh:
    for r in csv.DictReader(fh):
        all_trades += 1
        ots = r.get("open_ts")
        if ots:
            t = datetime.strptime(ots, "%Y-%m-%d %H:%M:%S")
            if opened_first is None or t < opened_first:
                opened_first = t
            if opened_last is None or t > opened_last:
                opened_last = t
        if r.get("close_ts") and r.get("pnl"):
            try:
                closed.append({
                    "pair": r["pair_label"],
                    "pattern": r["winner_pattern"],
                    "cost": float(r["cost"]),
                    "pnl": float(r["pnl"]),
                    "pnl_pct": float(r["pnl_pct"]),
                    "invest": float(r["invest_usd"]),
                    "min_profit_pct": float(r.get("min_profit_pct", 0)),
                })
            except Exception:
                pass

print(f"Total trade rows in log: {all_trades}")
print(f"Closed (settled)       : {len(closed)}")
if opened_first:
    delta = opened_last - opened_first
    print(f"First trade open : {opened_first}")
    print(f"Last  trade open : {opened_last}")
    print(f"Span             : {delta}")

print()
print("PATTERN BREAKDOWN (closed trades):")
patterns = {}
for c in closed:
    p = c["pattern"]
    patterns.setdefault(p, {"n": 0, "pnl": 0.0})
    patterns[p]["n"] += 1
    patterns[p]["pnl"] += c["pnl"]
for p, v in sorted(patterns.items()):
    print(f"  {p:<25} {v['n']:>3} trades  pnl=$ {v['pnl']:+,.2f}")

print()
print("PAIR BREAKDOWN (closed trades):")
pairs = {}
for c in closed:
    p = c["pair"]
    pairs.setdefault(p, {"n": 0, "pnl": 0.0, "invest": 0.0})
    pairs[p]["n"] += 1
    pairs[p]["pnl"] += c["pnl"]
    pairs[p]["invest"] += c["invest"]
for p, v in sorted(pairs.items()):
    roi = v["pnl"] / v["invest"] * 100 if v["invest"] else 0
    print(f"  {p:<32} {v['n']:>3}  invest=${v['invest']:>9,.0f}  pnl=${v['pnl']:+,.0f}  roi={roi:+.1f}%")

print()
total_inv = sum(c["invest"] for c in closed)
total_pnl = sum(c["pnl"] for c in closed)
roi_overall = total_pnl / total_inv * 100 if total_inv else 0
n_w = sum(1 for c in closed if c["pnl"] > 0)
n_l = sum(1 for c in closed if c["pnl"] < 0)
print(f"OVERALL: {len(closed)} closed  W={n_w} L={n_l}")
print(f"  Total invested: $ {total_inv:,.2f}")
print(f"  Total pnl     : $ {total_pnl:+,.2f}  ROI={roi_overall:+.2f}%")
