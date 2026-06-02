#!/usr/bin/env python3
"""V3 EXTENDED report — for each configured signal, show what would have happened
in NEIGHBORING spread buckets (just above and below the active range), using the
raw recorder data.

This lets you SEE if expanding or contracting the signal range would be better.
"""
import csv, json, os
from collections import defaultdict
from datetime import datetime, timezone

POLY    = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLYOUT = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PRED    = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM     = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIMMK   = "/root/data_limitless_btc_5m/markets.csv"
OKX     = "/root/data_okx_btc_5m/combined_per_second.csv"

SIGNALS = "/root/live/v3/v3_signals.json"
OUT = "/root/live/v3/v3_report_extended.txt"
INVEST = 2.0
COMMISSION = 0.02


def f(v):
    if v in (None, "", "None"): return None
    try: return float(v)
    except: return None


def load_poly_outs():
    out = {}
    with open(POLYOUT) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r["market_epoch"])
                if r.get("winner_side") in ("UP","DOWN"):
                    out[ep] = r["winner_side"]
            except: pass
    return out


def load_pred():
    snaps = {}; last_bn = {}; strikes = {}
    with open(PRED) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r["market_open_epoch"]); sec = int(r["sec_from_open"])
            except: continue
            snaps[(ep, sec)] = {
                "up": f(r.get("yes_ask")), "down": f(r.get("no_ask_implied")),
                "target": f(r.get("strike")),
            }
            bn = f(r.get("binance_now")); tg = f(r.get("strike"))
            if bn is not None: last_bn[ep] = bn
            if tg is not None: strikes[ep] = tg
    outs = {}
    for ep, s in strikes.items():
        fin = last_bn.get(ep)
        if fin is None: continue
        outs[ep] = "UP" if fin > s else "DOWN"
    return snaps, outs


def load_lim():
    m = {}
    with open(LIMMK) as fh:
        for r in csv.DictReader(fh):
            try:
                mid = r["market_id"]; exp_ms = int(r["expirationTimestamp"])
                m[mid] = exp_ms // 1000 - 300
            except: pass
    snaps = {}; last_bn = {}; targets = {}
    with open(LIM) as fh:
        for r in csv.DictReader(fh):
            mid = r.get("market_id"); ep = m.get(mid)
            if ep is None: continue
            try: es = int(r["epoch_sec"])
            except: continue
            sec = es - ep
            if sec < 0 or sec > 320: continue
            snaps[(ep, sec)] = {
                "up": f(r.get("best_ask")), "down": f(r.get("no_best_ask")),
                "target": f(r.get("target_price")),
            }
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn[ep] = bn
            if tg is not None: targets[ep] = tg
    outs = {}
    for ep, tg in targets.items():
        fin = last_bn.get(ep)
        if fin is None: continue
        outs[ep] = "UP" if fin > tg else "DOWN"
    return snaps, outs


def load_okx():
    snaps = {}; last_bn = {}; targets = {}
    with open(OKX) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r.get("market_open_epoch") or 0); sec = int(r.get("sec_from_open") or -1)
            except: continue
            if ep <= 0 or sec < 0: continue
            snaps[(ep, sec)] = {
                "up": f(r.get("up_ask")), "down": f(r.get("down_ask")),
                "target": f(r.get("target_price")),
            }
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn[ep] = bn
            if tg is not None: targets[ep] = tg
    outs = {}
    for ep, tg in targets.items():
        fin = last_bn.get(ep)
        if fin is None: continue
        outs[ep] = "UP" if fin > tg else "DOWN"
    return snaps, outs


def load_poly_snaps():
    out = {}
    with open(POLY) as fh:
        for r in csv.DictReader(fh):
            try:
                ep = int(r["market_epoch"]); sec = int(r["sec_from_start"])
            except: continue
            out[(ep, sec)] = {
                "up": f(r.get("up_ask")), "down": f(r.get("down_ask")),
                "target": f(r.get("target_price")),
            }
    return out


print("Loading recorder data ...")
poly_outs = load_poly_outs()
poly_snaps = load_poly_snaps()
pred_snaps, pred_outs = load_pred()
lim_snaps, lim_outs = load_lim()
okx_snaps, okx_outs = load_okx()

PLATFORMS = {
    "poly": (poly_snaps, poly_outs), "pred": (pred_snaps, pred_outs),
    "lim":  (lim_snaps,  lim_outs),  "okx":  (okx_snaps,  okx_outs),
}

with open(SIGNALS) as fh:
    cfg = json.load(fh)

lines = [f"V3 EXTENDED REPORT — {datetime.now(tz=timezone.utc).isoformat()}",
         "=" * 80,
         "Shows what WOULD have happened in spread buckets neighbouring each",
         "configured signal range. Use to decide if widening or narrowing helps.",
         ""]


def score_bucket(anchor, target, sec, lo, hi, side):
    a_snaps, _ = PLATFORMS[anchor]
    t_snaps, t_outs = PLATFORMS[target]
    eps = sorted(ep for ep in t_outs.keys())
    fires = wins = 0; pnl = 0.0
    for ep in eps:
        a = a_snaps.get((ep, sec)); t = t_snaps.get((ep, sec))
        if not a or not t: continue
        at = a.get("target"); tt = t.get("target")
        if at is None or tt is None: continue
        spread = at - tt
        if not (lo <= spread < hi): continue
        ask = t.get("down") if side == "DOWN" else t.get("up")
        if ask is None or ask <= 0: continue
        outc = t_outs.get(ep)
        if outc is None: continue
        fires += 1
        if outc == side:
            pnl += INVEST/ask - INVEST - INVEST*COMMISSION
            wins += 1
        else:
            pnl -= INVEST + INVEST*COMMISSION
    return fires, wins, pnl


for sig in cfg["signals"]:
    name = sig["name"]; anchor = sig["anchor"]; target = sig["target"]
    sec = sig["sec"]; lo = sig["spread_lo"]; hi = sig["spread_hi"]
    side = sig["side"]
    lines.append("=" * 80)
    lines.append(f"SIGNAL: {name}")
    lines.append(f"  anchor={anchor} target={target} sec={sec} configured spread={lo} to {hi} side={side}")
    lines.append("=" * 80)
    lines.append(f"  {'spread bucket':<18}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}{'status'}")
    # Show 6 buckets around the configured one (3 below + active + 2 above)
    # Use 10$ granularity
    width = hi - lo
    # Define neighboring buckets at same width
    bucket_offsets = [-3, -2, -1, 0, 1, 2]
    for off in bucket_offsets:
        b_lo = lo + off * width
        b_hi = hi + off * width
        n, w, p = score_bucket(anchor, target, sec, b_lo, b_hi, side)
        if n == 0: continue
        wr = 100*w/n if n else 0
        per = p/n if n else 0
        status = " ← ACTIVE" if off == 0 else ""
        lines.append(f"  {f'{b_lo:.0f} to {b_hi:.0f}':<18}{n:<6}{w:<6}{wr:<8.1f}{p:<+10.2f}{per:<+8.3f}{status}")
    lines.append("")
    # Also try narrower sub-buckets within the active range
    lines.append(f"  Sub-buckets WITHIN active range (5$ slices):")
    lines.append(f"  {'sub-bucket':<18}{'n':<6}{'wins':<6}{'win%':<8}{'PnL':<10}{'per':<8}")
    step = 5
    cur = lo
    while cur < hi:
        b_lo = cur; b_hi = min(cur + step, hi)
        n, w, p = score_bucket(anchor, target, sec, b_lo, b_hi, side)
        if n > 0:
            wr = 100*w/n; per = p/n
            lines.append(f"  {f'{b_lo:.0f} to {b_hi:.0f}':<18}{n:<6}{w:<6}{wr:<8.1f}{p:<+10.2f}{per:<+8.3f}")
        cur += step
    lines.append("")

out = "\n".join(lines)
print(out)
with open(OUT,"w") as fh: fh.write(out)
print(f"\nWritten to {OUT}")
