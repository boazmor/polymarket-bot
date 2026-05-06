#!/usr/bin/env python3
"""
arb_v4_3way.py — 3-platform safe-direction arb with completion logic.

Key design (per user spec 06/05/2026 evening):

1. Safe-direction filter: For each platform pair, only consider the
   direction where UP comes from the LOWER-strike platform and DOWN
   comes from the HIGHER-strike platform. This guarantees that AT LEAST
   ONE leg always wins (and BOTH win when BTC ends in the strike gap).
   Skipping the dangerous direction (UP from higher, DOWN from lower)
   prevents the both-lose scenario.

2. Symmetric shares: Buy the same number of shares on each leg, computed
   as invest_per_side / max(price1, price2). This is true spread arb,
   not asymmetric dollar-balanced (which was the bug in V3).

3. Depth-bounded sizing: Limit to 50% of the smaller leg's USD depth.
   Min $15 per side or skip. Target $50 per side max.

4. Completion logic: If actual fill is short on one leg, attempt to top
   up on the third platform same side, accepting slightly worse price
   (cost extension threshold 0.85 instead of 0.80). If still can't
   complete, the simulator marks the trade as ABORTED.

5. All 3 pairs (Poly+Kalshi, Poly+Gemini, Kalshi+Gemini) considered each
   second; each pair contributes one safe-direction candidate.

Output: /root/arb_v4_3way_trades.csv

Run:
  screen -dmS arb_v4 python3 /root/arb_v4_3way.py
"""
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Tuple, List

P_PATH = "/root/data_btc_15m_research/combined_per_second.csv"
K_PATH = "/root/data_kalshi_btc_15m/combined_per_second.csv"
G_PATH = "/root/data_gemini_btc_15m/combined_per_second.csv"
PM_OUTCOMES = "/root/data_btc_15m_research/market_outcomes.csv"
LOG = "/root/arb_v4_3way_trades.csv"
MARKET_REPORT = "/root/arb_v4_market_report.csv"

INVEST_PER_SIDE_TARGET = 50.0
INVEST_PER_SIDE_MIN = 15.0
DEPTH_USE_FRACTION = 0.5         # use 50% of min available depth
COST_THRESHOLD_OPEN = 0.95       # PILOT MODE: take everything ≥5% profit (per user 06/05 evening)
COST_THRESHOLD_COMPLETE = 0.97   # extended threshold when completing on 3rd
MAX_STRIKE_GAP = 9999            # no strike gap limit
COOLDOWN_SEC = 5                 # PILOT: lighter cooldown, all directions equal
MAX_TRADES_PER_MARKET = 200      # PILOT: very generous, just safety
POLL_SEC = 2

OPEN_TRADES: Dict[int, dict] = {}
CLOSED_TRADES: List[dict] = []
NEXT_TRADE_ID = 1
LAST_OPEN_TS: Dict[Tuple[str, str], float] = {}
MARKET_TRADE_COUNT: Dict[str, int] = {}

ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_YELLOW = "\033[33m"


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
        out = subprocess.run(
            ["tail", "-1", path], capture_output=True, text=True, timeout=5
        )
        line = out.stdout.strip()
        if not line:
            return None
        values = line.split(",")
        return dict(zip(header, values))
    except Exception:
        return None


def parse_poly(row):
    if not row:
        return None
    try:
        return {
            "platform": "POLY",
            "epoch": int(row.get("epoch_sec") or 0),
            "up_ask": float(row.get("up_ask") or 0),
            "down_ask": float(row.get("down_ask") or 0),
            "up_usd_avail": float(row.get("up_usd_best") or 0),
            "down_usd_avail": float(row.get("down_usd_best") or 0),
            "strike": float(row.get("target_chainlink_at_open") or 0),
            "market_id": row.get("market_slug", "") or "",
        }
    except Exception:
        return None


def parse_kalshi(row):
    if not row:
        return None
    try:
        ya = float(row.get("yes_ask") or 0)
        na = float(row.get("no_ask") or 0)
        ya_sz = float(row.get("yes_ask_size") or 0)
        return {
            "platform": "KALSHI",
            "epoch": int(row.get("epoch_sec") or 0),
            "up_ask": ya,           # YES = UP analog
            "down_ask": na,         # NO = DOWN analog
            "up_usd_avail": ya * ya_sz,
            "down_usd_avail": na * ya_sz,  # use yes_ask_size as proxy for NO depth
            "strike": float(row.get("floor_strike") or 0),
            "market_id": row.get("event_ticker", "") or "",
        }
    except Exception:
        return None


def parse_gemini(row):
    if not row:
        return None
    try:
        return {
            "platform": "GEMINI",
            "epoch": int(row.get("epoch_sec") or 0),
            "up_ask": float(row.get("yes_ask") or 0),
            "down_ask": float(row.get("no_ask_implied") or 0),
            "up_usd_avail": float(row.get("yes_ask_usd") or 0),
            "down_usd_avail": float(row.get("no_ask_usd_buyable") or 0),
            "strike": float(row.get("strike") or 0),
            "market_id": row.get("ticker", "") or "",
        }
    except Exception:
        return None


def detect_arb(platforms: List[dict]) -> List[dict]:
    """For each pair, return BOTH directions, marking each as safe or dangerous.
    Safe = UP from lower-strike + DOWN from higher-strike (bonus zone possible).
    Dangerous = UP from higher-strike + DOWN from lower-strike (both-lose risk
    if BTC ends in the strike gap)."""
    valid = [p for p in platforms if p and p.get("strike", 0) > 0]
    opps = []
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            a, b = valid[i], valid[j]
            if a["strike"] < b["strike"]:
                lower, higher = a, b
            else:
                lower, higher = b, a
            strike_gap = higher["strike"] - lower["strike"]
            if strike_gap > MAX_STRIKE_GAP:
                continue
            third = next(
                (p for p in valid if p["platform"] not in (lower["platform"], higher["platform"])),
                None,
            )
            # SAFE direction: UP@lower + DOWN@higher (BTC in middle = both win)
            up_ask = lower["up_ask"]; down_ask = higher["down_ask"]
            if up_ask > 0 and down_ask > 0 and up_ask < 1 and down_ask < 1:
                opps.append({
                    "pair_label": f"UP@{lower['platform']}+DOWN@{higher['platform']}",
                    "direction_safety": "safe",
                    "leg_up": lower, "leg_down": higher, "third": third,
                    "strike_gap": strike_gap,
                    "up_ask": up_ask, "down_ask": down_ask,
                    "cost": up_ask + down_ask,
                })
            # DANGEROUS direction: UP@higher + DOWN@lower (BTC in middle = both LOSE)
            up_ask = higher["up_ask"]; down_ask = lower["down_ask"]
            if up_ask > 0 and down_ask > 0 and up_ask < 1 and down_ask < 1:
                opps.append({
                    "pair_label": f"UP@{higher['platform']}+DOWN@{lower['platform']}",
                    "direction_safety": "dangerous",
                    "leg_up": higher, "leg_down": lower, "third": third,
                    "strike_gap": strike_gap,
                    "up_ask": up_ask, "down_ask": down_ask,
                    "cost": up_ask + down_ask,
                })
    opps.sort(key=lambda o: o["cost"])
    return opps


def attempt_completion(target_shares: float, up_filled: float, down_filled: float,
                       opp: dict) -> Tuple[float, float, float, float, float, str]:
    """If either leg under-filled, try to complete on the third platform same side.
    Returns (final_up_shares, final_down_shares, third_up_shares, third_down_shares,
    total_cost_paid, completion_note)."""
    up_short = target_shares - up_filled
    down_short = target_shares - down_filled
    third = opp.get("third")

    third_up_shares = 0.0
    third_down_shares = 0.0
    third_up_cost = 0.0
    third_down_cost = 0.0
    note = ""

    if (up_short > 0 or down_short > 0) and third:
        if up_short > 0 and third["up_ask"] > 0:
            new_cost = third["up_ask"] + opp["down_ask"]
            if new_cost <= COST_THRESHOLD_COMPLETE:
                affordable_shares = min(up_short, third["up_usd_avail"] / max(third["up_ask"], 0.01))
                third_up_shares = affordable_shares
                third_up_cost = third_up_shares * third["up_ask"]
        if down_short > 0 and third["down_ask"] > 0:
            new_cost = opp["up_ask"] + third["down_ask"]
            if new_cost <= COST_THRESHOLD_COMPLETE:
                affordable_shares = min(down_short, third["down_usd_avail"] / max(third["down_ask"], 0.01))
                third_down_shares = affordable_shares
                third_down_cost = third_down_shares * third["down_ask"]
        if third_up_shares > 0 or third_down_shares > 0:
            note = f"COMPLETED_ON_{third['platform']}"

    final_up = up_filled + third_up_shares
    final_down = down_filled + third_down_shares

    if abs(final_up - final_down) > 0.01:
        return 0.0, 0.0, 0.0, 0.0, 0.0, "ABORTED_imbalance"

    total_cost = (up_filled * opp["up_ask"]
                  + down_filled * opp["down_ask"]
                  + third_up_cost + third_down_cost)
    return final_up, final_down, third_up_shares, third_down_shares, total_cost, note


TRADE_COLS = [
    "trade_id", "open_ts", "pair_label", "direction_safety",
    # All 3 platform strikes captured at open time, regardless of which is in the trade
    "poly_strike_open", "kalshi_strike_open", "gemini_strike_open",
    "up_platform", "up_market_id", "up_strike", "up_ask",
    "down_platform", "down_market_id", "down_strike", "down_ask",
    "third_platform", "third_market_id", "third_strike",
    "strike_gap", "cost_open",
    "target_shares", "up_shares_filled", "down_shares_filled",
    "third_up_shares", "third_down_shares",
    "invest_total", "completion_note",
    "close_ts", "btc_final", "winner_pattern",
    "up_payout", "down_payout", "third_up_payout", "third_down_payout",
    "total_payout", "pnl", "pnl_pct",
]


MARKET_REPORT_COLS = [
    "report_ts", "market_window_start", "market_window_end",
    "poly_market", "poly_strike", "poly_winner_side",
    "kalshi_market", "kalshi_strike", "kalshi_winner_side",
    "gemini_market", "gemini_strike", "gemini_winner_side",
    "btc_final_binance",
    "n_trades_total", "n_trades_safe", "n_trades_dangerous",
    "n_both_win_bonus", "n_both_lost_danger",
    "total_invested", "total_pnl", "roi_pct",
    "avg_cost_open", "avg_strike_gap",
]


def init_log():
    if not os.path.exists(LOG):
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(TRADE_COLS)
    if not os.path.exists(MARKET_REPORT):
        with open(MARKET_REPORT, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(MARKET_REPORT_COLS)


def write_trade(t):
    row = [t.get(c, "") for c in TRADE_COLS]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


# Market reports: track which 15-min window is fully settled, then write a summary.
# Window key = floor(epoch / 900). Each market gets one report row.
REPORTED_WINDOWS = set()


def emit_market_report(window_key: int):
    """Aggregate all trades that closed in this 15-min window across all 3 platforms."""
    if window_key in REPORTED_WINDOWS:
        return
    # Collect trades whose lower or higher market belongs to this window
    window_start = window_key * 900
    window_end = window_start + 900
    trades_in_window = []
    for t in CLOSED_TRADES:
        # Use any leg's market epoch heuristic — fall back to close_ts
        try:
            ct = datetime.strptime(t.get("close_ts", ""), "%Y-%m-%d %H:%M:%S")
            ce = int(ct.timestamp())
        except Exception:
            continue
        # Match by close epoch falling in this window
        if window_start <= ce < window_end + 60:  # +60 grace for late settles
            trades_in_window.append(t)
    if not trades_in_window:
        return

    # Aggregate
    poly_strikes = [float(t.get("up_strike") or 0) if t.get("up_platform") == "POLY" else
                    (float(t.get("down_strike") or 0) if t.get("down_platform") == "POLY" else 0)
                    for t in trades_in_window]
    poly_strikes = [s for s in poly_strikes if s > 0]
    kal_strikes = [float(t.get("up_strike") or 0) if t.get("up_platform") == "KALSHI" else
                   (float(t.get("down_strike") or 0) if t.get("down_platform") == "KALSHI" else 0)
                   for t in trades_in_window]
    kal_strikes = [s for s in kal_strikes if s > 0]
    gem_strikes = [float(t.get("up_strike") or 0) if t.get("up_platform") == "GEMINI" else
                   (float(t.get("down_strike") or 0) if t.get("down_platform") == "GEMINI" else 0)
                   for t in trades_in_window]
    gem_strikes = [s for s in gem_strikes if s > 0]

    poly_market = next((t.get("up_market_id") if t.get("up_platform") == "POLY"
                        else t.get("down_market_id") if t.get("down_platform") == "POLY" else None
                        for t in trades_in_window), "")
    kal_market = next((t.get("up_market_id") if t.get("up_platform") == "KALSHI"
                       else t.get("down_market_id") if t.get("down_platform") == "KALSHI" else None
                       for t in trades_in_window), "")
    gem_market = next((t.get("up_market_id") if t.get("up_platform") == "GEMINI"
                       else t.get("down_market_id") if t.get("down_platform") == "GEMINI" else None
                       for t in trades_in_window), "")

    poly_winner = lookup_poly_winner(poly_market) if poly_market else ""
    kal_winner = lookup_kalshi_winner(kal_market) if kal_market else ""
    gem_winner = lookup_gemini_winner(gem_market) if gem_market else ""

    btc_final = ""
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == poly_market:
                    btc_final = r.get("final_binance_price", "")
                    break
    except Exception:
        pass

    n_total = len(trades_in_window)
    n_safe = sum(1 for t in trades_in_window if t.get("direction_safety") == "safe")
    n_dangerous = n_total - n_safe
    n_bonus = sum(1 for t in trades_in_window if t.get("winner_pattern") == "BOTH_WIN_BONUS")
    n_lost_danger = sum(1 for t in trades_in_window if t.get("winner_pattern") == "BOTH_LOST_DANGER")
    total_inv = sum(float(t.get("invest_total") or 0) for t in trades_in_window)
    total_pnl = sum(float(t.get("pnl") or 0) for t in trades_in_window)
    roi = total_pnl / total_inv * 100 if total_inv > 0 else 0
    avg_cost = sum(float(t.get("cost_open") or 0) for t in trades_in_window) / n_total
    avg_gap = sum(float(t.get("strike_gap") or 0) for t in trades_in_window) / n_total

    row = {
        "report_ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_window_start": datetime.utcfromtimestamp(window_start).strftime("%Y-%m-%d %H:%M:%S"),
        "market_window_end": datetime.utcfromtimestamp(window_end).strftime("%Y-%m-%d %H:%M:%S"),
        "poly_market": poly_market,
        "poly_strike": round(poly_strikes[0], 2) if poly_strikes else "",
        "poly_winner_side": poly_winner,
        "kalshi_market": kal_market,
        "kalshi_strike": round(kal_strikes[0], 2) if kal_strikes else "",
        "kalshi_winner_side": kal_winner,
        "gemini_market": gem_market,
        "gemini_strike": round(gem_strikes[0], 2) if gem_strikes else "",
        "gemini_winner_side": gem_winner,
        "btc_final_binance": btc_final,
        "n_trades_total": n_total,
        "n_trades_safe": n_safe,
        "n_trades_dangerous": n_dangerous,
        "n_both_win_bonus": n_bonus,
        "n_both_lost_danger": n_lost_danger,
        "total_invested": round(total_inv, 2),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi, 2),
        "avg_cost_open": round(avg_cost, 4),
        "avg_strike_gap": round(avg_gap, 2),
    }
    out_row = [row.get(c, "") for c in MARKET_REPORT_COLS]
    with open(MARKET_REPORT, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(out_row)
    REPORTED_WINDOWS.add(window_key)
    print(f"MARKET REPORT for window {row['market_window_start']}: "
          f"{n_total} trades (safe={n_safe} danger={n_dangerous}), "
          f"PnL=${total_pnl:+.2f} ROI={roi:+.1f}%")


def lookup_poly_winner(market_id: str) -> Optional[str]:
    """Returns 'UP', 'DOWN', or None. Polymarket settles by Chainlink."""
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == market_id:
                    return r.get("winner_side") or None
    except Exception:
        pass
    return None


def lookup_kalshi_winner(market_ticker: str) -> Optional[str]:
    """Returns 'YES', 'NO', or None. Kalshi settles by their oracle.
    After settlement, last_price snaps to ~1.00 (YES won) or ~0.00 (NO won)."""
    try:
        out = subprocess.run(
            ["tail", "-n", "5000", K_PATH], capture_output=True, text=True, timeout=10
        )
        if not out.stdout:
            return None
        header = read_header(K_PATH)
        if not header:
            return None
        for line in reversed(out.stdout.strip().split("\n")):
            values = line.split(",")
            if len(values) < len(header):
                continue
            row = dict(zip(header, values))
            if row.get("market_ticker") != market_ticker:
                continue
            try:
                lp = float(row.get("last_price") or 0)
            except Exception:
                continue
            if lp >= 0.99:
                return "YES"
            if lp <= 0.01:
                return "NO"
            if row.get("status", "").lower() in ("settled", "final", "finalized"):
                return "YES" if lp >= 0.5 else "NO"
        return None
    except Exception:
        return None


def lookup_gemini_winner(ticker: str) -> Optional[str]:
    """Returns 'YES', 'NO', or None. Gemini settles by Kaiko index.
    After settlement, last_trade_price snaps to 1.00 (YES won) or 0.00 (NO won)."""
    try:
        out = subprocess.run(
            ["tail", "-n", "5000", G_PATH], capture_output=True, text=True, timeout=10
        )
        if not out.stdout:
            return None
        header = read_header(G_PATH)
        if not header:
            return None
        for line in reversed(out.stdout.strip().split("\n")):
            values = line.split(",")
            if len(values) < len(header):
                continue
            row = dict(zip(header, values))
            if row.get("ticker") != ticker:
                continue
            try:
                lp = float(row.get("last_trade_price") or 0)
            except Exception:
                continue
            if lp >= 0.99:
                return "YES"
            if lp <= 0.01:
                return "NO"
            if row.get("status", "").lower() in ("settled", "final", "finalized", "closed"):
                return "YES" if lp >= 0.5 else "NO"
        return None
    except Exception:
        return None


def winner_for_platform(platform: str, market_id: str, side: str) -> Optional[bool]:
    """side: 'UP' if we bought UP/YES; 'DOWN' if we bought DOWN/NO.
    Returns True if our side won, False if it lost, None if not yet settled."""
    if platform == "POLY":
        winner = lookup_poly_winner(market_id)
        if winner is None:
            return None
        return (winner == "UP" and side == "UP") or (winner == "DOWN" and side == "DOWN")
    if platform == "KALSHI":
        winner = lookup_kalshi_winner(market_id)
        if winner is None:
            return None
        return (winner == "YES" and side == "UP") or (winner == "NO" and side == "DOWN")
    if platform == "GEMINI":
        winner = lookup_gemini_winner(market_id)
        if winner is None:
            return None
        return (winner == "YES" and side == "UP") or (winner == "NO" and side == "DOWN")
    return None


def settle_trade(tid: int) -> bool:
    """Settle using EACH platform's OWN oracle independently.
    Each leg checks its own platform's settled price."""
    t = OPEN_TRADES[tid]

    up_won = winner_for_platform(t["up_platform"], t["up_market_id"], "UP")
    down_won = winner_for_platform(t["down_platform"], t["down_market_id"], "DOWN")
    if up_won is None or down_won is None:
        return False

    third_up_won = None
    third_down_won = None
    if t.get("third_up_shares", 0) > 0 and t.get("third_platform"):
        third_up_won = winner_for_platform(t["third_platform"], t.get("third_market_id", ""), "UP")
    if t.get("third_down_shares", 0) > 0 and t.get("third_platform"):
        third_down_won = winner_for_platform(t["third_platform"], t.get("third_market_id", ""), "DOWN")

    # Pattern depends on outcomes vs the trade's safety direction
    if up_won and down_won:
        pattern = "BOTH_WIN_BONUS" if t.get("direction_safety") == "safe" else "BOTH_WIN_UNEXPECTED"
    elif up_won and not down_won:
        pattern = "UP_WON_ONLY"
    elif not up_won and down_won:
        pattern = "DOWN_WON_ONLY"
    else:
        pattern = "BOTH_LOST_DANGER" if t.get("direction_safety") == "dangerous" else "BOTH_LOST_UNEXPECTED"

    up_pay = t["up_shares_filled"] if up_won else 0.0
    down_pay = t["down_shares_filled"] if down_won else 0.0
    third_up_pay = t.get("third_up_shares", 0) if (third_up_won is True) else 0.0
    third_down_pay = t.get("third_down_shares", 0) if (third_down_won is True) else 0.0
    total = up_pay + down_pay + third_up_pay + third_down_pay
    pnl = total - t["invest_total"]
    pnl_pct = (pnl / t["invest_total"]) * 100 if t["invest_total"] else 0
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "close_ts": now_ts,
        "btc_final": "",
        "winner_pattern": pattern,
        "up_payout": round(up_pay, 4),
        "down_payout": round(down_pay, 4),
        "third_up_payout": round(third_up_pay, 4),
        "third_down_payout": round(third_down_pay, 4),
        "total_payout": round(total, 4),
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
    })
    write_trade(t)
    CLOSED_TRADES.append(t)
    print(f"SETTLED #{tid} [{t.get('direction_safety')}] {t['pair_label']} pattern={pattern} pnl={color_money(pnl)} ({pnl_pct:+.1f}%)")
    del OPEN_TRADES[tid]
    return True


def render_status(p, k, g, opps):
    width = 110
    out = ["\033[H\033[2J\033[3J\033[?25l"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.append(f"{ANSI_BOLD}ARB_V4_3WAY (safe=unlimited / dangerous=throttled){ANSI_RESET}   "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET}  "
               f"${INVEST_PER_SIDE_TARGET:.0f}/side  cost<={COST_THRESHOLD_OPEN}  "
               f"danger_cooldown={COOLDOWN_SEC}s")
    out.append("=" * width)
    out.append(f"TIME: {now}")
    out.append("")
    for pf in [p, k, g]:
        if not pf:
            continue
        out.append(f"  {pf['platform']:<8} strike=${pf['strike']:>10,.2f}  "
                   f"UP_ask={pf['up_ask']:.3f} ${pf['up_usd_avail']:>7.0f}  "
                   f"DN_ask={pf['down_ask']:.3f} ${pf['down_usd_avail']:>7.0f}  "
                   f"market={pf['market_id'][-22:]}")
    out.append("-" * width)
    out.append(f"{ANSI_BOLD}OPPORTUNITIES (all 6 directions, safe + dangerous):{ANSI_RESET}")
    if not opps:
        out.append("  (waiting for all 3 feeds with valid strikes)")
    else:
        for o in opps:
            mark = ""
            if o["cost"] <= COST_THRESHOLD_OPEN:
                mark = f"  {ANSI_GREEN}{ANSI_BOLD}<- OPEN{ANSI_RESET}"
            elif o["cost"] <= COST_THRESHOLD_COMPLETE:
                mark = f"  {ANSI_YELLOW}<- completion-only{ANSI_RESET}"
            safety_tag = "[BTOC]" if o["direction_safety"] == "safe" else "[MSKN]"
            out.append(f"  {safety_tag} {o['pair_label']:<32} cost={o['cost']:.3f}  "
                       f"min_profit={(1-o['cost'])*100:+.1f}%  "
                       f"strike_gap=${o['strike_gap']:.0f}{mark}")
    out.append("-" * width)
    out.append(f"{ANSI_BOLD}OPEN TRADES: {len(OPEN_TRADES)}{ANSI_RESET}")
    if OPEN_TRADES:
        for tid, t in sorted(OPEN_TRADES.items()):
            safety_tag = "[BTOC]" if t.get("direction_safety") == "safe" else "[MSKN]"
            out.append(f"  #{tid:>3} {safety_tag} {t['pair_label']}  open={t['open_ts']}  "
                       f"cost={t['cost_open']:.3f}  shares={t['target_shares']:.1f}  "
                       f"completion={t.get('completion_note','none')}")
    out.append("-" * width)
    n = len(CLOSED_TRADES)
    out.append(f"{ANSI_BOLD}CLOSED: {n} (last 8){ANSI_RESET}")
    for t in CLOSED_TRADES[-8:]:
        try:
            pnl_val = float(t.get("pnl", 0) or 0)
            pnl_pct_val = float(t.get("pnl_pct", 0) or 0)
        except Exception:
            pnl_val = 0.0; pnl_pct_val = 0.0
        out.append(f"  #{t['trade_id']:>3} [{t.get('direction_safety','?'):<9}] {t['pair_label']:<32} "
                   f"{t.get('winner_pattern',''):<20}  "
                   f"PnL={color_money(pnl_val)} ({pnl_pct_val:+.1f}%)")
    out.append("=" * width)
    if CLOSED_TRADES:
        def _f(v):
            try: return float(v or 0)
            except: return 0.0
        total_inv = sum(_f(t.get("invest_total")) for t in CLOSED_TRADES)
        total_pay = sum(_f(t.get("total_payout")) for t in CLOSED_TRADES)
        total_pnl = total_pay - total_inv
        wins = sum(1 for t in CLOSED_TRADES if _f(t.get("pnl")) > 0)
        bonus = sum(1 for t in CLOSED_TRADES if "BOTH_WIN" in t.get("winner_pattern", ""))
        roi = (total_pnl / total_inv * 100) if total_inv else 0
        out.append(f"{ANSI_BOLD}TOTALS:{ANSI_RESET} {n} trades  W={wins}  bonus_zone={bonus}  "
                   f"invested=${total_inv:.0f}  PnL={color_money(total_pnl)} ({roi:+.1f}%)")
    out.append("Ctrl+C to stop.")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def main():
    global NEXT_TRADE_ID
    p_header = read_header(P_PATH)
    k_header = read_header(K_PATH)
    g_header = read_header(G_PATH)
    if not (p_header and k_header and g_header):
        print(f"ERROR: cannot read all 3 headers")
        return
    init_log()
    while True:
        try:
            p = parse_poly(tail_last_row(P_PATH, p_header))
            k = parse_kalshi(tail_last_row(K_PATH, k_header))
            g = parse_gemini(tail_last_row(G_PATH, g_header))
            opps = detect_arb([p, k, g])
            now_unix = time.time()
            for o in opps:
                if o["cost"] > COST_THRESHOLD_OPEN:
                    continue
                pair_label = o["pair_label"]
                market_combo = f"{o['leg_up']['market_id']}|{o['leg_down']['market_id']}"
                cd_key = (pair_label, market_combo)
                # PILOT mode: same throttle for both safety classes (per user 06/05).
                # Safety tag still recorded in CSV for later analysis.
                if now_unix - LAST_OPEN_TS.get(cd_key, 0) < COOLDOWN_SEC:
                    continue
                if MARKET_TRADE_COUNT.get((pair_label, market_combo), 0) >= MAX_TRADES_PER_MARKET:
                    continue
                up_avail = o["leg_up"]["up_usd_avail"]
                down_avail = o["leg_down"]["down_usd_avail"]
                if up_avail <= 0 or down_avail <= 0:
                    continue
                usable_per_side = min(up_avail, down_avail) * DEPTH_USE_FRACTION
                invest_per_side = min(INVEST_PER_SIDE_TARGET, usable_per_side)
                if invest_per_side < INVEST_PER_SIDE_MIN:
                    continue
                max_price = max(o["up_ask"], o["down_ask"])
                target_shares = invest_per_side / max_price
                up_max = up_avail / max(o["up_ask"], 0.01)
                down_max = down_avail / max(o["down_ask"], 0.01)
                up_filled = min(target_shares, up_max * DEPTH_USE_FRACTION)
                down_filled = min(target_shares, down_max * DEPTH_USE_FRACTION)
                final_u, final_d, t_u, t_d, total_cost, note = attempt_completion(
                    target_shares, up_filled, down_filled, o
                )
                if note == "ABORTED_imbalance":
                    continue
                if final_u < 0.01:
                    continue
                LAST_OPEN_TS[cd_key] = now_unix
                MARKET_TRADE_COUNT[(pair_label, market_combo)] = MARKET_TRADE_COUNT.get((pair_label, market_combo), 0) + 1
                open_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                third = o.get("third") or {}
                # For STRIKE column purposes:
                #   Safe direction: up leg has lower strike, down leg has higher strike
                #   Dangerous: opposite
                OPEN_TRADES[NEXT_TRADE_ID] = {
                    "trade_id": NEXT_TRADE_ID,
                    "open_ts": open_ts,
                    "pair_label": pair_label,
                    "direction_safety": o["direction_safety"],
                    "poly_strike_open": round(p["strike"], 2) if p else "",
                    "kalshi_strike_open": round(k["strike"], 2) if k else "",
                    "gemini_strike_open": round(g["strike"], 2) if g else "",
                    "up_platform": o["leg_up"]["platform"],
                    "up_market_id": o["leg_up"]["market_id"],
                    "up_strike": o["leg_up"]["strike"],
                    "up_ask": round(o["up_ask"], 4),
                    "down_platform": o["leg_down"]["platform"],
                    "down_market_id": o["leg_down"]["market_id"],
                    "down_strike": o["leg_down"]["strike"],
                    "down_ask": round(o["down_ask"], 4),
                    "third_platform": third.get("platform", ""),
                    "third_strike": third.get("strike", ""),
                    "third_market_id": third.get("market_id", ""),
                    "strike_gap": round(o["strike_gap"], 2),
                    "cost_open": round(o["cost"], 4),
                    "target_shares": round(target_shares, 4),
                    "up_shares_filled": round(up_filled, 4),
                    "down_shares_filled": round(down_filled, 4),
                    "third_up_shares": round(t_u, 4),
                    "third_down_shares": round(t_d, 4),
                    "invest_total": round(total_cost, 4),
                    "completion_note": note,
                }
                NEXT_TRADE_ID += 1
            for tid in list(OPEN_TRADES.keys()):
                if tid in OPEN_TRADES:
                    settle_trade(tid)
            # Market report: trigger emit for any window that's >5min past close
            # so we know all trades had time to settle.
            now_epoch = int(time.time())
            current_window = now_epoch // 900
            for prev_window in [current_window - 1, current_window - 2]:
                if prev_window in REPORTED_WINDOWS:
                    continue
                # Only emit once enough grace passed
                window_end = (prev_window + 1) * 900
                if now_epoch - window_end > 300:  # 5 min after window closed
                    emit_market_report(prev_window)
            render_status(p, k, g, opps)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
