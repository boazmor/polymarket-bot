#!/usr/bin/env python3
"""V4 report — slice trades by signal, spread bucket, sec, target."""
import csv, os
from collections import defaultdict
from datetime import datetime, timezone

TRADES = "/root/live/v4/v4_trades.csv"
OUTCOMES = "/root/live/v4/v4_outcomes.csv"
OUT = "/root/live/v4/v4_report.txt"
INVEST = 2.0


def f(v):
    if v in (None, "", "None"): return None
    try: return float(v)
    except: return None


def main():
    if not os.path.exists(TRADES):
        print("no trades"); return
    with open(TRADES) as fh:
        trades = list(csv.DictReader(fh))
    outs = {}
    if os.path.exists(OUTCOMES):
        with open(OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                outs[r["window_epoch"]] = r
    resolved = []
    total = wins = losses = pending = 0; pnl = 0.0
    for t in trades:
        o = outs.get(t["window_epoch"])
        if not o or not o.get("target_outcome"):
            pending += 1; continue
        side = t["side"]; price = float(t["buy_price"]); invest = float(t["invest_usd"])
        outcome = o["target_outcome"]
        won = (outcome == side)
        if won: this_pnl = invest/price - invest; wins += 1
        else: this_pnl = -invest; losses += 1
        pnl += this_pnl; total += 1
        rec = dict(t); rec["_won"]=won; rec["_pnl"]=this_pnl; rec["_outcome"]=outcome
        resolved.append(rec)
    lines = [f"V4 REPORT — {datetime.now(tz=timezone.utc).isoformat()}",
             "=" * 80,
             f"Total trades: {len(trades)}  resolved: {total}  pending: {pending}"]
    if total == 0:
        out = "\n".join(lines); print(out)
        with open(OUT,"w") as fh: fh.write(out); return
    lines.append(f"Wins: {wins}  Losses: {losses}  Win%: {100*wins/total:.1f}%")
    lines.append(f"PnL: ${pnl:+.2f}  Per-trade: ${pnl/total:+.3f}")
    lines.append("")

    # By signal
    lines.append("="*80); lines.append("SLICE — by signal"); lines.append("="*80)
    bs = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = bs[r["signal_name"]]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'signal':<46}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}")
    for k,b in sorted(bs.items(), key=lambda x:-x[1][2]):
        wr=100*b[1]/b[0]
        lines.append(f"  {k:<46}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}")
    lines.append("")

    # By category
    lines.append("="*80); lines.append("SLICE — by category"); lines.append("="*80)
    cs = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = cs[r["category"]]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'category':<20}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}")
    for k,b in sorted(cs.items(), key=lambda x:-x[1][2]):
        wr=100*b[1]/b[0]
        lines.append(f"  {k:<20}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}")
    lines.append("")

    # By target+side
    lines.append("="*80); lines.append("SLICE — by target+side"); lines.append("="*80)
    ts = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = ts[f"{r['target']}_{r['side']}"]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'tgt_side':<12}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}")
    for k,b in sorted(ts.items(), key=lambda x:-x[1][2]):
        wr=100*b[1]/b[0]
        lines.append(f"  {k:<12}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}")
    lines.append("")

    # By sec
    lines.append("="*80); lines.append("SLICE — by sec_now"); lines.append("="*80)
    ss = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = ss[r["sec_now"]]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'sec':<5}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}")
    for k,b in sorted(ss.items()):
        wr=100*b[1]/b[0]
        lines.append(f"  {k:<5}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}")
    lines.append("")

    # By buy price
    lines.append("="*80); lines.append("SLICE — by buy price"); lines.append("="*80)
    pbs = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        try: p = float(r["buy_price"])
        except: continue
        if p < 0.30: bk = "<0.30"
        elif p < 0.40: bk = "0.30-0.40"
        elif p < 0.50: bk = "0.40-0.50"
        elif p < 0.60: bk = "0.50-0.60"
        elif p < 0.70: bk = "0.60-0.70"
        else: bk = "0.70+"
        b = pbs[bk]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'price':<12}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}")
    for k in ["<0.30","0.30-0.40","0.40-0.50","0.50-0.60","0.60-0.70","0.70+"]:
        b = pbs[k]
        if b[0]==0: continue
        wr=100*b[1]/b[0]
        lines.append(f"  {k:<12}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}")
    out = "\n".join(lines); print(out)
    with open(OUT,"w") as fh: fh.write(out)
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    main()
