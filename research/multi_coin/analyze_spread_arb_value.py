#!/usr/bin/env python3
"""Analyze arb_virtual_trades.csv to determine the value of the spread arb idea.

For each closed trade, determine whether BOTH legs won (bonus zone),
NEITHER leg won (disaster zone), or one won normally. This tells us
how often the new 3-way spread arb concept would have helped vs hurt.
"""
import csv
import sys

PATH = "/root/arb_virtual_trades.csv"

both = neither = normal_w = normal_l = 0
both_pnl = neither_pnl = normal_w_pnl = normal_l_pnl = 0.0
total_invest = total_pnl = 0.0

with open(PATH) as fh:
    for r in csv.DictReader(fh):
        if not r.get("total_payout"):
            continue
        try:
            inv = float(r["invest_usd"])
            pnl = float(r["pnl"])
        except Exception:
            continue
        d = r["direction"]
        pw = r["poly_winner"]
        kw = r["kalshi_winner"]
        poly_won = (d == "A" and pw == "UP") or (d == "B" and pw == "DOWN")
        kalshi_won = (d == "A" and kw == "NO") or (d == "B" and kw == "YES")
        total_invest += inv
        total_pnl += pnl
        if poly_won and kalshi_won:
            both += 1
            both_pnl += pnl
        elif not poly_won and not kalshi_won:
            neither += 1
            neither_pnl += pnl
        elif pnl >= 0:
            normal_w += 1
            normal_w_pnl += pnl
        else:
            normal_l += 1
            normal_l_pnl += pnl

n = both + neither + normal_w + normal_l
print(f"Total closed trades: {n}")
print(f"")
print(f"  BOTH WIN (bonus zone)    : {both:>3} trades  ({100*both/n:5.1f}%)  pnl=$ {both_pnl:+,.2f}")
print(f"  NEITHER WIN (dead zone)  : {neither:>3} trades  ({100*neither/n:5.1f}%)  pnl=$ {neither_pnl:+,.2f}")
print(f"  ONE WIN (profitable)     : {normal_w:>3} trades  ({100*normal_w/n:5.1f}%)  pnl=$ {normal_w_pnl:+,.2f}")
print(f"  ONE WIN (losing partial) : {normal_l:>3} trades  ({100*normal_l/n:5.1f}%)  pnl=$ {normal_l_pnl:+,.2f}")
print(f"")
print(f"Total invested: $ {total_invest:,.2f}")
print(f"Total pnl     : $ {total_pnl:+,.2f}  ROI={100*total_pnl/total_invest:+.2f}%")
print(f"")
print(">>> SPREAD ARB INSIGHT <<<")
print("")
print("If we had used the SPREAD ARB selection (only enter when our")
print("UP-leg is on the LOWER strike platform and DOWN-leg on the HIGHER):")
print(f"  - We would AVOID all NEITHER trades  (saves $ {-neither_pnl:+,.2f})")
print(f"  - We would KEEP all BOTH WIN trades  (the $ {both_pnl:+,.2f} bonus stays)")
print(f"  - We would KEEP normal one-win trades on safe direction")
print("")
print("To accurately determine which past trades were 'safe' vs 'danger'")
print("direction we need the actual strike prices at trade-open time.")
print("This script as-is shows aggregate impact; for per-trade direction")
print("classification we need a follow-up that joins on poly + kalshi")
print("combined_per_second.csv to retrieve strikes at open_ts.")
