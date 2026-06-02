#!/usr/bin/env python3
"""V3 report — slice v3_trades.csv + v3_outcomes.csv by various dimensions
for fine-tuning. Writes to /root/live/v3/v3_report.txt
"""
import csv, os
from collections import defaultdict
from datetime import datetime, timezone

TRADES = "/root/live/v3/v3_trades.csv"
OUTCOMES = "/root/live/v3/v3_outcomes.csv"
OUT = "/root/live/v3/v3_report.txt"
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
        rec = dict(t); rec["_outcome"]=outcome; rec["_won"]=won; rec["_pnl"]=this_pnl
        resolved.append(rec)

    lines = []
    lines.append(f"V3 REPORT — {datetime.now(tz=timezone.utc).isoformat()}")
    lines.append("=" * 80)
    lines.append(f"Total trades: {len(trades)}  resolved: {total}  pending: {pending}")
    if total == 0:
        out = "\n".join(lines); print(out)
        with open(OUT,"w") as fh: fh.write(out)
        return
    lines.append(f"Wins: {wins}  Losses: {losses}  Win%: {100*wins/total:.1f}%")
    lines.append(f"PnL: ${pnl:+.2f}  Per-trade: ${pnl/total:+.3f}")
    lines.append("")

    # Slice by signal_name
    lines.append("=" * 80)
    lines.append("SLICE 1 — by signal_name")
    lines.append("=" * 80)
    sig_buckets = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = sig_buckets[r["signal_name"]]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'signal':<28}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}{'per':<7}")
    for k,b in sorted(sig_buckets.items(), key=lambda x:-x[1][2]):
        wr=100*b[1]/b[0]; per=b[2]/b[0]
        lines.append(f"  {k:<28}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}{per:<+7.3f}")
    lines.append("")

    # Slice by spread within signal (sub-buckets of 5$)
    lines.append("=" * 80)
    lines.append("SLICE 2 — by spread sub-bucket (5$ granularity) per signal")
    lines.append("=" * 80)
    for sig_name in sorted(set(r["signal_name"] for r in resolved)):
        sub_buckets = defaultdict(lambda: [0,0,0.0])
        for r in resolved:
            if r["signal_name"] != sig_name: continue
            try: sp = float(r["spread"])
            except: continue
            sb = int(sp // 5) * 5
            label = f"{sb} to {sb+5}"
            b = sub_buckets[label]
            b[0]+=1
            if r["_won"]: b[1]+=1
            b[2]+=r["_pnl"]
        if not sub_buckets: continue
        lines.append(f"\n  {sig_name}:")
        lines.append(f"    {'sub-spread':<14}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}{'per'}")
        for k,b in sorted(sub_buckets.items(), key=lambda x:int(x[0].split()[0])):
            wr=100*b[1]/b[0]; per=b[2]/b[0]
            lines.append(f"    {k:<14}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}{per:<+.3f}")
    lines.append("")

    # Slice by anchor and target
    lines.append("=" * 80)
    lines.append("SLICE 3 — by (anchor, target) pair")
    lines.append("=" * 80)
    pair_buckets = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = pair_buckets[f"{r['anchor']}→{r['target']}"]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'pair':<12}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}{'per'}")
    for k,b in sorted(pair_buckets.items(), key=lambda x:-x[1][2]):
        wr=100*b[1]/b[0]; per=b[2]/b[0]
        lines.append(f"  {k:<12}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}{per:<+.3f}")
    lines.append("")

    # Slice by sec
    lines.append("=" * 80)
    lines.append("SLICE 4 — by sec_now")
    lines.append("=" * 80)
    sec_buckets = defaultdict(lambda: [0,0,0.0])
    for r in resolved:
        b = sec_buckets[r["sec_now"]]
        b[0]+=1
        if r["_won"]: b[1]+=1
        b[2]+=r["_pnl"]
    lines.append(f"  {'sec':<5}{'n':<5}{'wins':<5}{'win%':<7}{'PnL':<9}{'per'}")
    for k,b in sorted(sec_buckets.items()):
        wr=100*b[1]/b[0]; per=b[2]/b[0]
        lines.append(f"  {k:<5}{b[0]:<5}{b[1]:<5}{wr:<7.1f}{b[2]:<+9.2f}{per:<+.3f}")

    out = "\n".join(lines); print(out)
    with open(OUT,"w") as fh: fh.write(out)
    print(f"\nWritten to {OUT}")


if __name__ == "__main__":
    main()
