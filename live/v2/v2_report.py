#!/usr/bin/env python3
"""V2 report generator: read v2_trades.csv and v2_outcomes.csv,
slice by neighboring parameter values for fine-tuning.

Slices:
- by poly_side_ask buckets (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
- by sec
- by spread poly_target - lim_target buckets
- by buy platform

Output: /root/live/v2/v2_report.txt
"""
import csv, os, sys
from collections import defaultdict
from datetime import datetime, timezone

TRADES = "/root/live/v2/v2_trades.csv"
OUTCOMES = "/root/live/v2/v2_outcomes.csv"
OUT = "/root/live/v2/v2_report.txt"

INVEST = 2.0


def f(v):
    if v in (None, "", "None"): return None
    try: return float(v)
    except: return None


def main():
    if not os.path.exists(TRADES):
        print("no trades file"); return
    with open(TRADES) as fh:
        trades = list(csv.DictReader(fh))
    outs = {}
    if os.path.exists(OUTCOMES):
        with open(OUTCOMES) as fh:
            for r in csv.DictReader(fh): outs[r["window_epoch"]] = r

    total = wins = losses = pending = 0; pnl = 0.0
    rows_with_outcome = []
    for t in trades:
        we = t["window_epoch"]
        o = outs.get(we)
        if not o or not o.get("poly_outcome"): pending += 1; continue
        side = t["side"]; price = float(t["buy_price"])
        outcome = o["poly_outcome"]
        won = (outcome == side)
        invest = float(t["invest_usd"])
        if won: this_pnl = invest/price - invest; wins += 1
        else: this_pnl = -invest; losses += 1
        pnl += this_pnl; total += 1
        rec = dict(t)
        rec["_outcome"] = outcome; rec["_won"] = won; rec["_pnl"] = this_pnl
        rows_with_outcome.append(rec)

    lines = []
    lines.append(f"V2 REPORT — generated {datetime.now(tz=timezone.utc).isoformat()}")
    lines.append("=" * 80)
    lines.append(f"Total trades: {len(trades)}  resolved: {total}  pending: {pending}")
    if total == 0:
        lines.append("No resolved trades yet."); print("\n".join(lines))
        with open(OUT, "w") as fh: fh.write("\n".join(lines))
        return
    lines.append(f"Wins: {wins}  Losses: {losses}  Win%: {100*wins/total:.1f}%")
    lines.append(f"PnL: ${pnl:+.2f}  Per-trade: ${pnl/total:+.3f}")
    lines.append("")

    # Slice 1: by poly_side_ask buckets
    lines.append("=" * 80)
    lines.append("SLICE 1 — by poly_side_ask (the value at trade time)")
    lines.append("=" * 80)
    buckets = defaultdict(lambda: [0,0,0.0,0.0])  # [n, wins, pnl, invested]
    for r in rows_with_outcome:
        pa = f(r.get("poly_side_ask"))
        if pa is None: continue
        if pa < 0.70: bk = "<0.70"
        elif pa < 0.75: bk = "0.70-0.75"
        elif pa < 0.80: bk = "0.75-0.80"
        elif pa < 0.85: bk = "0.80-0.85"
        elif pa < 0.90: bk = "0.85-0.90"
        elif pa < 0.95: bk = "0.90-0.95"
        else: bk = "0.95+"
        b = buckets[bk]
        b[0] += 1
        if r["_won"]: b[1] += 1
        b[2] += r["_pnl"]
        b[3] += INVEST
    lines.append(f"  {'bucket':<12}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}")
    for bk in ["<0.70","0.70-0.75","0.75-0.80","0.80-0.85","0.85-0.90","0.90-0.95","0.95+"]:
        b = buckets[bk]
        if b[0] == 0: continue
        wr = 100*b[1]/b[0]; per = b[2]/b[0]
        lines.append(f"  {bk:<12}{b[0]:<6}{b[1]:<6}{wr:<8.1f}{b[2]:<+10.2f}{per:<+8.3f}")
    lines.append("")

    # Slice 2: by sec
    lines.append("=" * 80)
    lines.append("SLICE 2 — by sec_now (fire timing)")
    lines.append("=" * 80)
    sec_buckets = defaultdict(lambda: [0,0,0.0])
    for r in rows_with_outcome:
        sec = r.get("sec_now")
        try: sec = int(sec)
        except: continue
        if sec < 60: bk = "0-60"
        elif sec < 120: bk = "60-120"
        elif sec < 180: bk = "120-180"
        elif sec < 240: bk = "180-240"
        elif sec < 260: bk = "240-260"
        elif sec < 280: bk = "260-280"
        else: bk = "280+"
        b = sec_buckets[bk]
        b[0] += 1
        if r["_won"]: b[1] += 1
        b[2] += r["_pnl"]
    lines.append(f"  {'bucket':<12}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}")
    for bk in ["0-60","60-120","120-180","180-240","240-260","260-280","280+"]:
        b = sec_buckets[bk]
        if b[0] == 0: continue
        wr = 100*b[1]/b[0]; per = b[2]/b[0]
        lines.append(f"  {bk:<12}{b[0]:<6}{b[1]:<6}{wr:<8.1f}{b[2]:<+10.2f}{per:<+8.3f}")
    lines.append("")

    # Slice 3: by spread (poly_target - lim_target) bucket
    lines.append("=" * 80)
    lines.append("SLICE 3 — by BTC target spread (poly - lim, $)")
    lines.append("=" * 80)
    sp_buckets = defaultdict(lambda: [0,0,0.0])
    for r in rows_with_outcome:
        pt = f(r.get("poly_target")); lt = f(r.get("lim_target"))
        if pt is None or lt is None: continue
        spread = pt - lt
        if spread < -200: bk = "<-200"
        elif spread < -150: bk = "-200 to -150"
        elif spread < -100: bk = "-150 to -100"
        elif spread < -50: bk = "-100 to -50"
        elif spread < 0: bk = "-50 to 0"
        elif spread < 50: bk = "0 to 50"
        else: bk = "50+"
        b = sp_buckets[bk]
        b[0] += 1
        if r["_won"]: b[1] += 1
        b[2] += r["_pnl"]
    lines.append(f"  {'bucket':<15}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}")
    for bk in ["<-200","-200 to -150","-150 to -100","-100 to -50","-50 to 0","0 to 50","50+"]:
        b = sp_buckets[bk]
        if b[0] == 0: continue
        wr = 100*b[1]/b[0]; per = b[2]/b[0]
        lines.append(f"  {bk:<15}{b[0]:<6}{b[1]:<6}{wr:<8.1f}{b[2]:<+10.2f}{per:<+8.3f}")
    lines.append("")

    # Slice 4: by buy_plat
    lines.append("=" * 80)
    lines.append("SLICE 4 — by buy platform")
    lines.append("=" * 80)
    plat_buckets = defaultdict(lambda: [0,0,0.0])
    for r in rows_with_outcome:
        b = plat_buckets[r["buy_plat"]]
        b[0] += 1
        if r["_won"]: b[1] += 1
        b[2] += r["_pnl"]
    for plat, b in plat_buckets.items():
        wr = 100*b[1]/b[0]; per = b[2]/b[0]
        lines.append(f"  {plat:<8}n={b[0]:<5}wins={b[1]:<5}win%={wr:5.1f}  PnL=${b[2]:+.2f}  per=${per:+.3f}")
    lines.append("")

    # Slice 5: by combo_id (how many V1 combos matched)
    lines.append("=" * 80)
    lines.append("SLICE 5 — by combo_id (how many V1 combos matched at the fire moment)")
    lines.append("=" * 80)
    cid_buckets = defaultdict(lambda: [0,0,0.0])
    for r in rows_with_outcome:
        try: c = int(r.get("combo_id"))
        except: continue
        if c <= 2: bk = "1-2"
        elif c <= 5: bk = "3-5"
        elif c <= 10: bk = "6-10"
        else: bk = "10+"
        b = cid_buckets[bk]
        b[0] += 1
        if r["_won"]: b[1] += 1
        b[2] += r["_pnl"]
    lines.append(f"  {'matches':<10}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}")
    for bk in ["1-2","3-5","6-10","10+"]:
        b = cid_buckets[bk]
        if b[0] == 0: continue
        wr = 100*b[1]/b[0]; per = b[2]/b[0]
        lines.append(f"  {bk:<10}{b[0]:<6}{b[1]:<6}{wr:<8.1f}{b[2]:<+10.2f}{per:<+8.3f}")
    lines.append("")

    out = "\n".join(lines)
    print(out)
    with open(OUT, "w") as fh: fh.write(out)
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    main()
