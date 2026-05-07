#!/usr/bin/env python3
"""
arb_virtual_bot_v5.py — Polymarket + Predict.fun arbitrage simulator.

Mirror of V2 but with Predict.fun replacing Kalshi:
- Polymarket: Chainlink oracle, two-sided orderbook (UP and DOWN both buyable)
- Predict.fun: Pyth oracle, single-sided orderbook (YES). DOWN derived as 1 - yes_bid.

Direction A: PolyUP + PredictNO   (NO = sell YES; cost = poly_up_ask + (1 - predict_yes_bid))
Direction B: PolyDOWN + PredictYES (cost = poly_down_ask + predict_yes_ask)

Settlement: per-platform oracle (Polymarket via market_outcomes.csv winner_side;
Predict.fun via last_trade_price snapping to ~1.00 / ~0.00 after settlement).

Symmetric SHARES sizing per user spec (06/05): same share count on each leg.
Cost threshold ≤ 0.90. Per-leg max 0.80. $100/side target.

Output: /root/arb_v5_predict_trades.csv

Run:
  screen -dmS arb_v5 python3 /root/arb_virtual_bot_v5.py
"""
import csv
import os
import subprocess
import time
from datetime import datetime
from typing import Optional

P = "/root/data_btc_15m_research/combined_per_second.csv"
PR = "/root/data_predict_btc_15m/combined_per_second.csv"
PYTH = "/root/data_pyth_btc/per_second.csv"
PM_OUTCOMES = "/root/data_btc_15m_research/market_outcomes.csv"
LOG = "/root/arb_v5_predict_trades.csv"

INVEST_PER_SIDE_TARGET = 100.0
INVEST_MIN = 5.0
COST_THRESHOLD = 0.90
SINGLE_LEG_MAX_ASK = 0.80
MAX_TRADES_PER_MARKET = 15
COOLDOWN_SEC = 5
POLL_SEC = 2
MAX_FEED_AGE_SEC = 30

OPEN_TRADES = {}
CLOSED_TRADES = []
NEXT_TRADE_ID = 1
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"


def color_money(v):
    s = f"${v:+,.2f}"
    if v > 0:
        return f"{ANSI_GREEN}{s}{ANSI_RESET}"
    if v < 0:
        return f"{ANSI_RED}{s}{ANSI_RESET}"
    return s


def read_header(path):
    try:
        with open(path) as fh:
            return fh.readline().strip().split(",")
    except Exception:
        return None


def tail_last_row(path, header):
    try:
        out = subprocess.run(["tail", "-1", path], capture_output=True, text=True, timeout=5)
        line = out.stdout.strip()
        if not line:
            return None
        return dict(zip(header, line.split(",")))
    except Exception:
        return None


def parse_poly(row):
    if not row:
        return None
    try:
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "ua": float(row.get("up_ask") or 0),
            "da": float(row.get("down_ask") or 0),
            "ua_usd": float(row.get("up_usd_best") or 0),
            "da_usd": float(row.get("down_usd_best") or 0),
            "tgt": float(row.get("target_chainlink_at_open") or 0),
            "slug": row.get("market_slug", "") or "",
        }
    except Exception:
        return None


def parse_predict(row):
    if not row:
        return None
    try:
        ya = float(row.get("yes_ask") or 0)
        yb = float(row.get("yes_bid") or 0)
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "yes_ask": ya,
            "yes_bid": yb,
            "yes_ask_size": float(row.get("yes_ask_size") or 0),
            "yes_bid_size": float(row.get("yes_bid_size") or 0),
            "yes_ask_usd": float(row.get("yes_ask_usd") or 0),
            "no_ask_usd": float(row.get("no_ask_usd_buyable") or 0),
            # If yes_bid is 0, there's no one to sell YES to → can't get NO. Set to 999 to skip.
            "no_ask_implied": (1.0 - yb) if yb > 0 else 999,
            "market_id": row.get("market_id", "") or "",
        }
    except Exception:
        return None


def lookup_poly_winner(slug):
    """Polymarket settles via Chainlink (winner_side from market_outcomes.csv)."""
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == slug:
                    return r.get("winner_side") or None
    except Exception:
        pass
    return None


def lookup_pyth_target(market_open_epoch):
    """Returns the REAL Predict.fun target — i.e. Pyth's BTC/USD price at the
    moment the 15-min market opened. Predict.fun uses this Pyth snapshot as its
    strike, even though the public API does not expose it. We get it ourselves
    from the Pyth recorder. Accepts only samples within 5 seconds of open."""
    if not market_open_epoch:
        return None
    try:
        out = subprocess.run(["tail", "-n", "2000", PYTH], capture_output=True, text=True, timeout=5)
        if not out.stdout:
            return None
        header = read_header(PYTH)
        if not header:
            return None
        best_diff = 999999
        best_price = None
        for line in out.stdout.strip().split("\n"):
            values = line.split(",")
            if len(values) < len(header):
                continue
            row = dict(zip(header, values))
            try:
                e = int(row.get("epoch_sec") or 0)
                price = float(row.get("btc_price") or 0)
            except Exception:
                continue
            if price <= 0:
                continue
            diff = abs(e - market_open_epoch)
            if diff < best_diff:
                best_diff = diff
                best_price = price
        return best_price if best_diff <= 5 else None
    except Exception:
        return None


def derive_market_open_epoch(epoch_now):
    """Round down to nearest 15-min boundary — Predict.fun market opens on those
    boundaries, and Pyth's price at that exact second becomes the target."""
    if not epoch_now:
        return None
    return (int(epoch_now) // 900) * 900


def lookup_predict_winner(market_id):
    """Predict.fun settles via Pyth. After settlement, last_trade_price (or final
    yes_bid/yes_ask) collapses to ~1.00 (YES won = UP) or ~0.00 (NO won = DOWN).
    Search recent rows of recorder for this market_id."""
    if not market_id:
        return None
    try:
        out = subprocess.run(["tail", "-n", "5000", PR], capture_output=True, text=True, timeout=10)
        if not out.stdout:
            return None
        header = read_header(PR)
        if not header:
            return None
        # Look backwards through recent rows for this market_id, find the last "decisive" price
        for line in reversed(out.stdout.strip().split("\n")):
            values = line.split(",")
            if len(values) < len(header):
                continue
            row = dict(zip(header, values))
            if str(row.get("market_id", "")) != str(market_id):
                continue
            try:
                ya = float(row.get("yes_ask") or 0)
                yb = float(row.get("yes_bid") or 0)
            except Exception:
                continue
            # YES won: yes_bid near 1.00 (book often one-sided after settle)
            if yb >= 0.97:
                return "YES"
            # NO won: yes_ask near 0 (book often one-sided after settle)
            if ya > 0 and ya <= 0.03:
                return "NO"
        return None
    except Exception:
        return None


def write_trade_row(t):
    cols = [
        "trade_id", "open_ts", "direction",
        "poly_slug", "predict_market_id",
        "poly_target", "predict_target_real", "target_gap",
        "poly_ask", "predict_ask",
        "cost", "profit_pct_open",
        "shares", "invest_usd",
        "close_ts", "poly_winner", "predict_winner",
        "poly_payout", "predict_payout", "total_payout",
        "pnl", "pnl_pct", "winner_pattern", "notes",
    ]
    row = [t.get(c, "") for c in cols]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


def init_log():
    if not os.path.exists(LOG):
        cols = [
            "trade_id", "open_ts", "direction",
            "poly_slug", "predict_market_id",
            "poly_strike", "predict_market_id_str",
            "poly_ask", "predict_ask",
            "cost", "profit_pct_open",
            "shares", "invest_usd",
            "close_ts", "poly_winner", "predict_winner",
            "poly_payout", "predict_payout", "total_payout",
            "pnl", "pnl_pct", "winner_pattern", "notes",
        ]
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)


def settle_trade_if_ready(tid):
    t = OPEN_TRADES[tid]
    poly_winner = lookup_poly_winner(t["poly_slug"])
    if poly_winner is None:
        return False
    predict_winner = lookup_predict_winner(t["predict_market_id"])
    if predict_winner is None:
        return False

    direction = t["direction"]
    poly_won = False
    predict_won = False
    if direction == "A":  # PolyUP + PredictNO
        poly_won = (poly_winner == "UP")
        predict_won = (predict_winner == "NO")
    else:  # B: PolyDOWN + PredictYES
        poly_won = (poly_winner == "DOWN")
        predict_won = (predict_winner == "YES")

    poly_pay = t["shares"] if poly_won else 0.0
    predict_pay = t["shares"] if predict_won else 0.0
    total = poly_pay + predict_pay
    pnl = total - t["invest_usd"]
    pnl_pct = (pnl / t["invest_usd"]) * 100 if t["invest_usd"] else 0

    if poly_won and predict_won:
        pattern = "BOTH_WIN_BONUS"
    elif poly_won and not predict_won:
        pattern = "POLY_WON_ONLY"
    elif not poly_won and predict_won:
        pattern = "PREDICT_WON_ONLY"
    else:
        pattern = "BOTH_LOST_DANGER"

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "close_ts": now_ts,
        "poly_winner": poly_winner,
        "predict_winner": predict_winner,
        "poly_payout": round(poly_pay, 4),
        "predict_payout": round(predict_pay, 4),
        "total_payout": round(total, 4),
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
        "winner_pattern": pattern,
    })
    write_trade_row(t)
    CLOSED_TRADES.append(t)
    print(f"SETTLED #{tid} {direction} {pattern} pnl={color_money(pnl)} ({pnl_pct:+.1f}%)")
    del OPEN_TRADES[tid]
    return True


def render_status(p, pr):
    width = 90
    out = ["\033[H\033[2J\033[3J\033[?25l"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.append(f"{ANSI_BOLD}ARB_V5 — Polymarket + Predict.fun{ANSI_RESET}  "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET}  ${INVEST_PER_SIDE_TARGET:.0f}/side  cost<={COST_THRESHOLD}")
    out.append("=" * width)
    out.append(f"TIME : {now}")
    out.append("-" * width)
    if p and pr:
        # Real Predict target via Pyth at the 15-min boundary
        pyth_target = lookup_pyth_target(derive_market_open_epoch(pr.get('epoch')))
        gap_str = ""
        if pyth_target is not None and p['tgt']:
            gap_str = f"  gap=${abs(p['tgt']-pyth_target):.0f}"
        out.append(f"  POLY    UP={p['ua']:.3f} DOWN={p['da']:.3f}  target={p['tgt']:.0f}  market={p['slug'][-25:]}")
        pyth_str = f"{pyth_target:.0f}" if pyth_target else "n/a"
        out.append(f"  PREDICT YES={pr['yes_ask']:.3f} bid={pr['yes_bid']:.3f}  NO_implied={pr['no_ask_implied']:.3f}  pyth_target={pyth_str}{gap_str}")
        cost_a = p['ua'] + pr['no_ask_implied']
        cost_b = p['da'] + pr['yes_ask']
        def mark(c):
            if c <= COST_THRESHOLD:
                return f"{ANSI_GREEN}{ANSI_BOLD}cost={c:.3f} +{(1-c)*100:.1f}% [OPEN]{ANSI_RESET}"
            return f"cost={c:.3f} {(1-c)*100:+.1f}%"
        out.append(f"  DIR-A PolyUP+PredictNO   {mark(cost_a)}")
        out.append(f"  DIR-B PolyDOWN+PredictYES {mark(cost_b)}")
    out.append("-" * width)
    if OPEN_TRADES:
        out.append(f"{ANSI_BOLD}OPEN ({len(OPEN_TRADES)}):{ANSI_RESET}")
        for tid, t in sorted(OPEN_TRADES.items()):
            out.append(f"  #{tid:>3} {t['direction']} cost={t['cost']:.3f} ({t['profit_pct_open']:+.1f}%) "
                       f"shares={t['shares']:.1f} invest=${t['invest_usd']:.0f}")
    out.append("-" * width)
    n = len(CLOSED_TRADES)
    out.append(f"{ANSI_BOLD}CLOSED ({n}, last 5):{ANSI_RESET}")
    for t in CLOSED_TRADES[-5:]:
        out.append(f"  #{t['trade_id']:>3} {t['direction']} {t.get('winner_pattern',''):<20} PnL={color_money(t.get('pnl',0))} ({t.get('pnl_pct',0):+.0f}%)")
    out.append("-" * width)
    if CLOSED_TRADES:
        inv = sum(float(t.get('invest_usd') or 0) for t in CLOSED_TRADES)
        pnl = sum(float(t.get('pnl') or 0) for t in CLOSED_TRADES)
        w = sum(1 for t in CLOSED_TRADES if (t.get('pnl') or 0) > 0)
        l = sum(1 for t in CLOSED_TRADES if (t.get('pnl') or 0) < 0)
        bonus = sum(1 for t in CLOSED_TRADES if t.get('winner_pattern')=='BOTH_WIN_BONUS')
        both_lost = sum(1 for t in CLOSED_TRADES if t.get('winner_pattern')=='BOTH_LOST_DANGER')
        roi = pnl/inv*100 if inv else 0
        out.append(f"{ANSI_BOLD}TOTALS:{ANSI_RESET} n={n} W={w} L={l} bonus={bonus} both_lost={both_lost}  inv=${inv:.0f} PnL={color_money(pnl)} ({roi:+.1f}%)")
    out.append("Ctrl+C to stop.")
    import sys
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def main():
    global NEXT_TRADE_ID
    p_header = read_header(P)
    pr_header = read_header(PR)
    if not p_header or not pr_header:
        print("ERROR: cannot read CSV headers")
        return
    init_log()

    last_open_ts = {}
    market_count = {}

    while True:
        try:
            p = parse_poly(tail_last_row(P, p_header))
            pr = parse_predict(tail_last_row(PR, pr_header))

            now_e = int(time.time())
            p_age = now_e - p.get("epoch", 0) if p else 999
            pr_age = now_e - pr.get("epoch", 0) if pr else 999
            fresh = p_age <= MAX_FEED_AGE_SEC and pr_age <= MAX_FEED_AGE_SEC

            if p and pr and fresh:
                # Direction A: PolyUP + PredictNO
                # Direction B: PolyDOWN + PredictYES
                cost_a = p['ua'] + pr['no_ask_implied'] if (p['ua']>0 and pr['no_ask_implied']>0) else 999
                cost_b = p['da'] + pr['yes_ask'] if (p['da']>0 and pr['yes_ask']>0) else 999
                # depth: poly side has up_usd_best/down_usd_best; predict has yes_ask_usd / no_ask_usd
                cands = [
                    ("A", cost_a, p['ua'], pr['no_ask_implied'], p['ua_usd'], pr['no_ask_usd']),
                    ("B", cost_b, p['da'], pr['yes_ask'], p['da_usd'], pr['yes_ask_usd']),
                ]
                for direction, cost, p_ask, pr_ask, p_depth, pr_depth in cands:
                    if cost > COST_THRESHOLD: continue
                    if p_ask > SINGLE_LEG_MAX_ASK or pr_ask > SINGLE_LEG_MAX_ASK: continue
                    # Symmetric shares: invest/max_price
                    # Depth: 50% of min depth
                    min_depth = min(p_depth, pr_depth)
                    if min_depth <= 0: continue
                    invest_per_side = min(INVEST_PER_SIDE_TARGET, min_depth / 2)
                    if invest_per_side < INVEST_MIN: continue
                    max_price = max(p_ask, pr_ask)
                    shares = invest_per_side / max_price
                    invest = shares * (p_ask + pr_ask)
                    market_id = (p['slug'], pr['market_id'])
                    if market_count.get(market_id, 0) >= MAX_TRADES_PER_MARKET: continue
                    key = (direction, market_id)
                    if time.time() - last_open_ts.get(key, 0) < COOLDOWN_SEC: continue

                    # Look up REAL Predict.fun target via Pyth (the price Pyth saw
                    # at the 15-min market open boundary). This replaces the old
                    # proxy that assumed Predict's target equals Poly's target.
                    market_open_epoch = derive_market_open_epoch(pr.get('epoch'))
                    predict_target_real = lookup_pyth_target(market_open_epoch)
                    target_gap = ""
                    if predict_target_real is not None and p['tgt']:
                        target_gap = round(abs(p['tgt'] - predict_target_real), 2)

                    last_open_ts[key] = time.time()
                    market_count[market_id] = market_count.get(market_id, 0) + 1
                    OPEN_TRADES[NEXT_TRADE_ID] = {
                        "trade_id": NEXT_TRADE_ID,
                        "open_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": direction,
                        "poly_slug": p['slug'],
                        "predict_market_id": pr['market_id'],
                        "poly_target": p['tgt'],
                        "predict_target_real": (round(predict_target_real, 2)
                                                if predict_target_real is not None else ""),
                        "target_gap": target_gap,
                        "poly_ask": round(p_ask, 4),
                        "predict_ask": round(pr_ask, 4),
                        "cost": round(cost, 4),
                        "profit_pct_open": round((1-cost)*100, 2),
                        "shares": round(shares, 4),
                        "invest_usd": round(invest, 4),
                    }
                    NEXT_TRADE_ID += 1

            for tid in list(OPEN_TRADES.keys()):
                if tid in OPEN_TRADES:
                    settle_trade_if_ready(tid)

            render_status(p, pr)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
