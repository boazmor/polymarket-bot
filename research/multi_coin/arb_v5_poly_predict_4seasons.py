#!/usr/bin/env python3
"""
arb_v5_poly_predict_4seasons.py — Polymarket + Predict.fun bot with
4-platform awareness.

Trades ONLY on Polymarket + Predict.fun. But monitors all 4 platforms
(Poly, Kalshi, Gemini, Predict) to detect when one is an OUTLIER on the
strike. The outlier signal informs trade decisions.

Why this matters (lessons from V4):
- When GEMINI is the outlier (Kaiko slow), trades are risky.
- When POLY is the outlier (Chainlink fast), trades are gold.
- The other oracles (Kalshi internal, Pyth at Predict) give us cross-checks.

Logic:
1. Read all 4 platforms each second.
2. Compute strike outlier (which platform's strike is most different).
3. Compute Poly+Predict arb cost in both directions.
4. Open Poly+Predict trade only when:
   - Cost ≤ threshold (0.90 normal, 0.96 if positive direction with large gap)
   - Per-leg ≤ 0.80
   - Predict.fun is NOT the outlier (we don't trust Pyth diverging from rest)
   - In NEGATIVE direction, only trade if strike gap < 50 (low both-lose risk)

Predict.fun strike: not exposed in WebSocket. We use Polymarket's
target_chainlink_at_open as proxy. Both are fast oracles (Chainlink
RTDS vs Pyth) so they should be very close (~few dollars). Tag this
as "PREDICT_STRIKE_PROXY" in events.

Output: /root/arb_v5_4seasons_trades.csv

Run:
  screen -dmS arb_v5_4s python3 /root/arb_v5_poly_predict_4seasons.py
"""
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

P = "/root/data_btc_15m_research/combined_per_second.csv"
K = "/root/data_kalshi_btc_15m/combined_per_second.csv"
G = "/root/data_gemini_btc_15m/combined_per_second.csv"
PR = "/root/data_predict_btc_15m/combined_per_second.csv"
PYTH = "/root/data_pyth_btc/per_second.csv"
PM_OUTCOMES = "/root/data_btc_15m_research/market_outcomes.csv"
LOG = "/root/arb_v5_4seasons_trades.csv"

INVEST_PER_SIDE_TARGET = 100.0
INVEST_MIN = 5.0
COST_THRESHOLD_NORMAL = 0.90
COST_THRESHOLD_AGGRESSIVE = 0.96
SINGLE_LEG_MAX_ASK = 0.80
NEGATIVE_GAP_BLOCK = 50            # block negative direction when poly_strike vs predict_strike diff >= 50
AGGRESSIVE_GAP_THRESHOLD = 50
MAX_TRADES_PER_MARKET = 15
COOLDOWN_SEC = 5
POLL_SEC = 2
MAX_FEED_AGE_SEC = 30

OPEN_TRADES = {}
CLOSED_TRADES = []
NEXT_TRADE_ID = 1
ANSI_RESET = "\033[0m"; ANSI_GREEN = "\033[32m"; ANSI_RED = "\033[31m"
ANSI_BOLD = "\033[1m"; ANSI_CYAN = "\033[36m"; ANSI_YELLOW = "\033[33m"


def color_money(v):
    s = f"${v:+,.2f}"
    if v > 0: return f"{ANSI_GREEN}{s}{ANSI_RESET}"
    if v < 0: return f"{ANSI_RED}{s}{ANSI_RESET}"
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
        if not line: return None
        return dict(zip(header, line.split(",")))
    except Exception:
        return None


def parse_poly(row):
    if not row: return None
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


def parse_kalshi(row):
    if not row: return None
    try:
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "strike": float(row.get("floor_strike") or 0),
            "ya": float(row.get("yes_ask") or 0),
            "na": float(row.get("no_ask") or 0),
        }
    except Exception:
        return None


def parse_gemini(row):
    if not row: return None
    try:
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "strike": float(row.get("strike") or 0),
            "ya": float(row.get("yes_ask") or 0),
        }
    except Exception:
        return None


def parse_predict(row):
    if not row: return None
    try:
        ya = float(row.get("yes_ask") or 0)
        yb = float(row.get("yes_bid") or 0)
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "yes_ask": ya,
            "yes_bid": yb,
            "no_ask_implied": (1.0 - yb) if yb > 0 else 999,
            "yes_ask_usd": float(row.get("yes_ask_usd") or 0),
            "no_ask_usd": float(row.get("no_ask_usd_buyable") or 0),
            "market_id": row.get("market_id", "") or "",
        }
    except Exception:
        return None


def lookup_pyth_strike(market_open_epoch):
    """Returns Pyth's BTC/USD price at the given epoch (= Predict.fun's strike).
    Looks for the closest sample to the given second from the Pyth recorder."""
    if not market_open_epoch:
        return None
    try:
        out = subprocess.run(["tail", "-n", "2000", PYTH], capture_output=True, text=True, timeout=5)
        if not out.stdout: return None
        header = read_header(PYTH)
        if not header: return None
        best_diff = 999999; best_price = None
        for line in out.stdout.strip().split("\n"):
            values = line.split(",")
            if len(values) < len(header): continue
            row = dict(zip(header, values))
            try:
                e = int(row.get("epoch_sec") or 0)
                price = float(row.get("btc_price") or 0)
            except Exception:
                continue
            if price <= 0: continue
            diff = abs(e - market_open_epoch)
            if diff < best_diff:
                best_diff = diff; best_price = price
            if diff > 60 and best_price is not None:
                continue
        return best_price if best_diff <= 5 else None  # accept only if within 5 seconds
    except Exception:
        return None


def derive_market_open_epoch(predict_market_id, predict_epoch_now):
    """Approximate market open time by rounding down predict_epoch_now to nearest 15min boundary."""
    if not predict_epoch_now: return None
    return (predict_epoch_now // 900) * 900


def lookup_poly_winner(slug):
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == slug:
                    return r.get("winner_side") or None
    except Exception:
        pass
    return None


def lookup_predict_winner(market_id):
    if not market_id: return None
    try:
        out = subprocess.run(["tail", "-n", "5000", PR], capture_output=True, text=True, timeout=10)
        if not out.stdout: return None
        header = read_header(PR)
        if not header: return None
        for line in reversed(out.stdout.strip().split("\n")):
            values = line.split(",")
            if len(values) < len(header): continue
            row = dict(zip(header, values))
            if str(row.get("market_id", "")) != str(market_id): continue
            try:
                ya = float(row.get("yes_ask") or 0)
                yb = float(row.get("yes_bid") or 0)
            except Exception:
                continue
            if ya >= 0.97 and yb >= 0.97: return "YES"
            if ya <= 0.03 and yb <= 0.03 and ya > 0: return "NO"
        return None
    except Exception:
        return None


def find_outlier_strike(strikes):
    """strikes: dict of platform -> strike. Returns (outlier_platform, max_distance, outlier_value)."""
    valid = {p: s for p, s in strikes.items() if s and s > 0}
    if len(valid) < 3:
        return None, 0, 0
    items = list(valid.items())
    best_dist = 0; best_p = None
    for p, s in items:
        others = [s2 for p2, s2 in items if p2 != p]
        avg_other = sum(others) / len(others)
        dist = abs(s - avg_other)
        if dist > best_dist:
            best_dist = dist
            best_p = p
    return best_p, best_dist, valid.get(best_p, 0)


def write_trade_row(t):
    cols = [
        "trade_id", "open_ts", "direction",
        "poly_slug", "predict_market_id",
        "poly_strike", "kalshi_strike", "gemini_strike", "predict_strike_proxy",
        "outlier_platform", "outlier_distance",
        "poly_ask", "predict_ask",
        "cost", "cost_limit", "profit_pct_open",
        "shares", "invest_usd",
        "close_ts", "poly_winner", "predict_winner",
        "poly_payout", "predict_payout", "total_payout",
        "pnl", "pnl_pct", "winner_pattern",
    ]
    row = [t.get(c, "") for c in cols]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


def init_log():
    if not os.path.exists(LOG):
        cols = [
            "trade_id", "open_ts", "direction",
            "poly_slug", "predict_market_id",
            "poly_strike", "kalshi_strike", "gemini_strike", "predict_strike_proxy",
            "outlier_platform", "outlier_distance",
            "poly_ask", "predict_ask",
            "cost", "cost_limit", "profit_pct_open",
            "shares", "invest_usd",
            "close_ts", "poly_winner", "predict_winner",
            "poly_payout", "predict_payout", "total_payout",
            "pnl", "pnl_pct", "winner_pattern",
        ]
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)


def settle_trade_if_ready(tid):
    t = OPEN_TRADES[tid]
    poly_winner = lookup_poly_winner(t["poly_slug"])
    if poly_winner is None: return False
    predict_winner = lookup_predict_winner(t["predict_market_id"])
    if predict_winner is None: return False

    direction = t["direction"]
    if direction == "A":
        poly_won = (poly_winner == "UP")
        predict_won = (predict_winner == "NO")
    else:
        poly_won = (poly_winner == "DOWN")
        predict_won = (predict_winner == "YES")

    poly_pay = t["shares"] if poly_won else 0.0
    predict_pay = t["shares"] if predict_won else 0.0
    total = poly_pay + predict_pay
    pnl = total - t["invest_usd"]
    pnl_pct = (pnl / t["invest_usd"]) * 100 if t["invest_usd"] else 0

    if poly_won and predict_won: pattern = "BOTH_WIN_BONUS"
    elif poly_won: pattern = "POLY_WON_ONLY"
    elif predict_won: pattern = "PREDICT_WON_ONLY"
    else: pattern = "BOTH_LOST_DANGER"

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "close_ts": now_ts,
        "poly_winner": poly_winner, "predict_winner": predict_winner,
        "poly_payout": round(poly_pay, 4), "predict_payout": round(predict_pay, 4),
        "total_payout": round(total, 4), "pnl": round(pnl, 4), "pnl_pct": round(pnl_pct, 2),
        "winner_pattern": pattern,
    })
    write_trade_row(t)
    CLOSED_TRADES.append(t)
    print(f"SETTLED #{tid} {direction} {pattern} pnl={color_money(pnl)} ({pnl_pct:+.1f}%)")
    del OPEN_TRADES[tid]
    return True


def render_status(p, k, g, pr, outlier_info):
    width = 110
    out = ["\033[H\033[2J\033[3J\033[?25l"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.append(f"{ANSI_BOLD}ARB V5 4-SEASONS — Trades on Poly+Predict, watches all 4{ANSI_RESET}  "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET}  ${INVEST_PER_SIDE_TARGET:.0f}/side")
    out.append("=" * width)
    out.append(f"TIME: {now}")
    if p: out.append(f"  POLY    strike={p['tgt']:.0f}  UP={p['ua']:.3f}  DOWN={p['da']:.3f}")
    if k: out.append(f"  KALSHI  strike={k['strike']:.0f}  (info only)")
    if g: out.append(f"  GEMINI  strike={g['strike']:.0f}  (info only)")
    pr_strike_str = ""
    try:
        pr_strike_str = f" strike(Pyth)={lookup_pyth_strike((pr['epoch']//900)*900):.0f}" if pr else ""
    except Exception:
        pr_strike_str = ""
    if pr: out.append(f"  PREDICT YES={pr['yes_ask']:.3f}  bid={pr['yes_bid']:.3f}  NO_impl={pr['no_ask_implied']:.3f}{pr_strike_str}  market={pr['market_id']}")

    out_p, out_d, _ = outlier_info
    out.append(f"  OUTLIER: {out_p or '-'}  distance=${out_d:.0f}")
    out.append("-" * width)

    n = len(CLOSED_TRADES)
    if CLOSED_TRADES:
        inv = sum(float(t.get('invest_usd') or 0) for t in CLOSED_TRADES)
        pnl = sum(float(t.get('pnl') or 0) for t in CLOSED_TRADES)
        w = sum(1 for t in CLOSED_TRADES if (t.get('pnl') or 0) > 0)
        l = sum(1 for t in CLOSED_TRADES if (t.get('pnl') or 0) < 0)
        bonus = sum(1 for t in CLOSED_TRADES if t.get('winner_pattern')=='BOTH_WIN_BONUS')
        both_lost = sum(1 for t in CLOSED_TRADES if t.get('winner_pattern')=='BOTH_LOST_DANGER')
        roi = pnl/inv*100 if inv else 0
        out.append(f"{ANSI_BOLD}TOTALS:{ANSI_RESET} n={n} W={w} L={l} bonus={bonus} both_lost={both_lost}  inv=${inv:.0f} PnL={color_money(pnl)} ({roi:+.1f}%)")
    out.append(f"OPEN: {len(OPEN_TRADES)}")
    out.append("Ctrl+C to stop.")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def main():
    global NEXT_TRADE_ID
    p_h = read_header(P); k_h = read_header(K); g_h = read_header(G); pr_h = read_header(PR)
    if not all([p_h, k_h, g_h, pr_h]):
        print("ERROR: cannot read all 4 headers"); return
    init_log()

    last_open_ts = {}
    market_count = {}

    while True:
        try:
            p = parse_poly(tail_last_row(P, p_h))
            k = parse_kalshi(tail_last_row(K, k_h))
            g = parse_gemini(tail_last_row(G, g_h))
            pr = parse_predict(tail_last_row(PR, pr_h))

            now_e = int(time.time())
            ages = {
                "poly": now_e - p.get("epoch", 0) if p else 999,
                "predict": now_e - pr.get("epoch", 0) if pr else 999,
            }
            tradeable = ages["poly"] <= MAX_FEED_AGE_SEC and ages["predict"] <= MAX_FEED_AGE_SEC

            # Identify outlier across the 4 strikes (Predict now uses REAL Pyth strike)
            predict_strike = None
            if pr:
                # Find market open epoch (round down to 15-min boundary)
                market_open = derive_market_open_epoch(pr["market_id"], pr["epoch"])
                predict_strike = lookup_pyth_strike(market_open)
            strikes = {
                "POLY": p["tgt"] if p else 0,
                "KALSHI": k["strike"] if k else 0,
                "GEMINI": g["strike"] if g else 0,
                "PREDICT": predict_strike or (p["tgt"] if p else 0),  # fallback to Poly if Pyth unavailable
            }
            outlier_info = find_outlier_strike(strikes)

            if tradeable and p and pr:
                # Cost calc
                cost_a = p["ua"] + pr["no_ask_implied"] if (p["ua"]>0 and pr["no_ask_implied"]>0 and pr["no_ask_implied"]<999) else 999
                cost_b = p["da"] + pr["yes_ask"] if (p["da"]>0 and pr["yes_ask"]>0) else 999

                # Strike gap between Poly and Predict (proxy = same, so always 0). Real gap unknown.
                # For now we treat Poly+Predict gap as 0, making both directions "negative-direction-safe"
                # Actually with proxy=same, there's no positive/negative direction distinction.
                # Use Poly vs Kalshi gap as proxy for "market dispersion".
                kalshi_dist = abs((k["strike"] if k else 0) - (p["tgt"] if p else 0))
                gemini_dist = abs((g["strike"] if g else 0) - (p["tgt"] if p else 0))

                cands = [
                    ("A", cost_a, p["ua"], pr["no_ask_implied"], p["ua_usd"], pr["no_ask_usd"]),
                    ("B", cost_b, p["da"], pr["yes_ask"], p["da_usd"], pr["yes_ask_usd"]),
                ]
                # CRITICAL: gap between Poly strike and Predict strike (real Pyth)
                poly_predict_gap = abs((p["tgt"] if p else 0) - (predict_strike or 0)) if predict_strike else 0
                for direction, cost, p_ask, pr_ask, p_depth, pr_depth in cands:
                    if cost > COST_THRESHOLD_NORMAL: continue
                    if p_ask > SINGLE_LEG_MAX_ASK or pr_ask > SINGLE_LEG_MAX_ASK: continue
                    # CRITICAL FILTER: don't open if Poly+Predict strikes diverge significantly
                    # (this is what caused V5 basic to lose 39 trades = $1,675)
                    if predict_strike and poly_predict_gap >= NEGATIVE_GAP_BLOCK:
                        continue
                    # Skip if Predict strike unknown (we'd be flying blind)
                    if predict_strike is None:
                        continue
                    # Block if Gemini is heavy outlier (high dispersion = risky)
                    if outlier_info[0] == "GEMINI" and outlier_info[1] >= 80:
                        continue

                    # Sizing
                    min_depth = min(p_depth, pr_depth)
                    if min_depth <= 0: continue
                    invest_per_side = min(INVEST_PER_SIDE_TARGET, min_depth/2)
                    if invest_per_side < INVEST_MIN: continue
                    max_price = max(p_ask, pr_ask)
                    shares = invest_per_side / max_price
                    invest = shares * (p_ask + pr_ask)

                    market_id = (p["slug"], pr["market_id"])
                    if market_count.get(market_id, 0) >= MAX_TRADES_PER_MARKET: continue
                    key = (direction, market_id)
                    if time.time() - last_open_ts.get(key, 0) < COOLDOWN_SEC: continue

                    last_open_ts[key] = time.time()
                    market_count[market_id] = market_count.get(market_id, 0) + 1
                    OPEN_TRADES[NEXT_TRADE_ID] = {
                        "trade_id": NEXT_TRADE_ID,
                        "open_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": direction,
                        "poly_slug": p["slug"],
                        "predict_market_id": pr["market_id"],
                        "poly_strike": p["tgt"],
                        "kalshi_strike": k["strike"] if k else "",
                        "gemini_strike": g["strike"] if g else "",
                        "predict_strike_proxy": round(predict_strike, 2) if predict_strike else "",
                        "outlier_platform": outlier_info[0] or "",
                        "outlier_distance": round(outlier_info[1], 2),
                        "poly_ask": round(p_ask, 4),
                        "predict_ask": round(pr_ask, 4),
                        "cost": round(cost, 4),
                        "cost_limit": COST_THRESHOLD_NORMAL,
                        "profit_pct_open": round((1-cost)*100, 2),
                        "shares": round(shares, 4),
                        "invest_usd": round(invest, 4),
                    }
                    NEXT_TRADE_ID += 1

            for tid in list(OPEN_TRADES.keys()):
                if tid in OPEN_TRADES:
                    settle_trade_if_ready(tid)

            render_status(p, k, g, pr, outlier_info)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
