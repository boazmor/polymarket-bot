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

INVEST_PER_SIDE_TARGET = 50.0
INVEST_PER_SIDE_MIN = 15.0
DEPTH_USE_FRACTION = 0.5         # use 50% of min available depth
COST_THRESHOLD_OPEN = 0.80       # primary open threshold (≥20% min profit)
COST_THRESHOLD_COMPLETE = 0.85   # extended threshold when completing on 3rd
MAX_STRIKE_GAP = 200             # don't trade if strikes differ by more than this
COOLDOWN_SEC = 5
MAX_TRADES_PER_MARKET = 15
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


def detect_safe_arb(platforms: List[dict]) -> List[dict]:
    """For each pair, return ONLY the safe direction:
        UP from lower-strike platform + DOWN from higher-strike platform.
    Sorts by best (lowest) cost."""
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
            up_ask = lower["up_ask"]
            down_ask = higher["down_ask"]
            if up_ask <= 0 or down_ask <= 0 or up_ask >= 1 or down_ask >= 1:
                continue
            cost = up_ask + down_ask
            third = next(
                (p for p in valid if p["platform"] not in (lower["platform"], higher["platform"])),
                None,
            )
            opps.append({
                "pair_label": f"UP@{lower['platform']}+DOWN@{higher['platform']}",
                "lower": lower,
                "higher": higher,
                "third": third,
                "strike_gap": strike_gap,
                "up_ask": up_ask,
                "down_ask": down_ask,
                "cost": cost,
            })
    opps.sort(key=lambda o: o["cost"])
    return opps


def attempt_completion(target_shares: float, lower_filled: float, higher_filled: float,
                       opp: dict) -> Tuple[float, float, float, float, float, str]:
    """If either leg under-filled, try to complete on the third platform
    same side. Returns (final_lower_shares, final_higher_shares, third_lower_shares,
    third_higher_shares, total_cost_paid, completion_note).
    third_lower_shares = additional UP shares bought on third platform
    third_higher_shares = additional DOWN shares bought on third platform.
    """
    lower_short = target_shares - lower_filled
    higher_short = target_shares - higher_filled
    third = opp.get("third")

    third_lower_shares = 0.0
    third_higher_shares = 0.0
    third_lower_cost = 0.0
    third_higher_cost = 0.0
    note = ""

    if (lower_short > 0 or higher_short > 0) and third:
        if lower_short > 0 and third["up_ask"] > 0:
            new_cost = third["up_ask"] + opp["down_ask"]
            if new_cost <= COST_THRESHOLD_COMPLETE:
                affordable_shares = min(lower_short, third["up_usd_avail"] / max(third["up_ask"], 0.01))
                third_lower_shares = affordable_shares
                third_lower_cost = third_lower_shares * third["up_ask"]
        if higher_short > 0 and third["down_ask"] > 0:
            new_cost = opp["up_ask"] + third["down_ask"]
            if new_cost <= COST_THRESHOLD_COMPLETE:
                affordable_shares = min(higher_short, third["down_usd_avail"] / max(third["down_ask"], 0.01))
                third_higher_shares = affordable_shares
                third_higher_cost = third_higher_shares * third["down_ask"]
        if third_lower_shares > 0 or third_higher_shares > 0:
            note = f"COMPLETED_ON_{third['platform']}"

    final_lower = lower_filled + third_lower_shares
    final_higher = higher_filled + third_higher_shares

    # If still imbalanced, abort the whole trade (can't accept asymmetry per user spec)
    if abs(final_lower - final_higher) > 0.01:
        return 0.0, 0.0, 0.0, 0.0, 0.0, "ABORTED_imbalance"

    total_cost = (lower_filled * opp["up_ask"]
                  + higher_filled * opp["down_ask"]
                  + third_lower_cost + third_higher_cost)
    return final_lower, final_higher, third_lower_shares, third_higher_shares, total_cost, note


def init_log():
    if not os.path.exists(LOG):
        cols = [
            "trade_id", "open_ts", "pair_label",
            "lower_platform", "lower_market_id", "lower_strike", "lower_up_ask",
            "higher_platform", "higher_market_id", "higher_strike", "higher_down_ask",
            "third_platform", "third_strike",
            "strike_gap", "cost_open",
            "target_shares", "lower_shares_filled", "higher_shares_filled",
            "third_lower_shares", "third_higher_shares",
            "invest_total", "completion_note",
            "close_ts", "btc_final", "winner_pattern",
            "lower_payout", "higher_payout", "third_lower_payout", "third_higher_payout",
            "total_payout", "pnl", "pnl_pct",
        ]
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)


def write_trade(t):
    cols = [
        "trade_id", "open_ts", "pair_label",
        "lower_platform", "lower_market_id", "lower_strike", "lower_up_ask",
        "higher_platform", "higher_market_id", "higher_strike", "higher_down_ask",
        "third_platform", "third_strike",
        "strike_gap", "cost_open",
        "target_shares", "lower_shares_filled", "higher_shares_filled",
        "third_lower_shares", "third_higher_shares",
        "invest_total", "completion_note",
        "close_ts", "btc_final", "winner_pattern",
        "lower_payout", "higher_payout", "third_lower_payout", "third_higher_payout",
        "total_payout", "pnl", "pnl_pct",
    ]
    row = [t.get(c, "") for c in cols]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


def lookup_btc_final(market_id: str) -> Optional[float]:
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == market_id:
                    fp = r.get("final_binance_price")
                    if fp:
                        return float(fp)
    except Exception:
        pass
    return None


def settle_trade(tid: int) -> bool:
    """Settle using BTC final price from Polymarket's outcomes (Binance-derived)."""
    t = OPEN_TRADES[tid]
    btc_final = (lookup_btc_final(t.get("lower_market_id", ""))
                 or lookup_btc_final(t.get("higher_market_id", "")))
    if btc_final is None:
        return False
    lower_strike = t["lower_strike"]
    higher_strike = t["higher_strike"]
    if btc_final < lower_strike:
        lower_won = False
        higher_won = True
        pattern = "BTC<low"
    elif btc_final > higher_strike:
        lower_won = True
        higher_won = False
        pattern = "BTC>high"
    else:
        lower_won = True
        higher_won = True
        pattern = "BTC_mid_BOTH_WIN"
    lower_pay = t["lower_shares_filled"] if lower_won else 0.0
    higher_pay = t["higher_shares_filled"] if higher_won else 0.0
    # Third-platform completions are SAME side as their respective shorted leg
    third_lower_pay = t["third_lower_shares"] if lower_won else 0.0
    third_higher_pay = t["third_higher_shares"] if higher_won else 0.0
    total = lower_pay + higher_pay + third_lower_pay + third_higher_pay
    pnl = total - t["invest_total"]
    pnl_pct = (pnl / t["invest_total"]) * 100 if t["invest_total"] else 0
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "close_ts": now_ts,
        "btc_final": round(btc_final, 2),
        "winner_pattern": pattern,
        "lower_payout": round(lower_pay, 4),
        "higher_payout": round(higher_pay, 4),
        "third_lower_payout": round(third_lower_pay, 4),
        "third_higher_payout": round(third_higher_pay, 4),
        "total_payout": round(total, 4),
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
    })
    write_trade(t)
    CLOSED_TRADES.append(t)
    print(f"SETTLED #{tid} {t['pair_label']} pattern={pattern} pnl={color_money(pnl)} ({pnl_pct:+.1f}%)")
    del OPEN_TRADES[tid]
    return True


def render_status(p, k, g, opps):
    width = 110
    out = ["\033[H\033[2J\033[3J\033[?25l"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out.append(f"{ANSI_BOLD}ARB_V4_3WAY (Safe-direction + completion){ANSI_RESET}   "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET}  "
               f"target=${INVEST_PER_SIDE_TARGET:.0f}/side  cost_open<={COST_THRESHOLD_OPEN}")
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
    out.append(f"{ANSI_BOLD}SAFE-DIRECTION OPPORTUNITIES (UP from lower-strike + DOWN from higher-strike):{ANSI_RESET}")
    if not opps:
        out.append("  (waiting for all 3 feeds with valid strikes)")
    else:
        for o in opps:
            mark = ""
            if o["cost"] <= COST_THRESHOLD_OPEN:
                mark = f"  {ANSI_GREEN}{ANSI_BOLD}<- OPEN{ANSI_RESET}"
            elif o["cost"] <= COST_THRESHOLD_COMPLETE:
                mark = f"  {ANSI_YELLOW}<- completion-only{ANSI_RESET}"
            out.append(f"  {o['pair_label']:<35} cost={o['cost']:.3f}  "
                       f"min_profit={(1-o['cost'])*100:+.1f}%  "
                       f"strike_gap=${o['strike_gap']:.0f}{mark}")
    out.append("-" * width)
    out.append(f"{ANSI_BOLD}OPEN TRADES: {len(OPEN_TRADES)}{ANSI_RESET}")
    if OPEN_TRADES:
        for tid, t in sorted(OPEN_TRADES.items()):
            out.append(f"  #{tid:>3}  {t['pair_label']}  open={t['open_ts']}  "
                       f"cost={t['cost_open']:.3f}  shares={t['target_shares']:.1f}  "
                       f"completion={t.get('completion_note','none')}")
    out.append("-" * width)
    n = len(CLOSED_TRADES)
    out.append(f"{ANSI_BOLD}CLOSED: {n} (last 8){ANSI_RESET}")
    for t in CLOSED_TRADES[-8:]:
        out.append(f"  #{t['trade_id']:>3} {t['pair_label']:<32} "
                   f"BTC=${t.get('btc_final', 0):>9,.0f}  {t.get('winner_pattern',''):<20}  "
                   f"PnL={color_money(t.get('pnl', 0))} ({t.get('pnl_pct',0):+.1f}%)")
    out.append("=" * width)
    if CLOSED_TRADES:
        total_inv = sum(t["invest_total"] for t in CLOSED_TRADES)
        total_pay = sum(t["total_payout"] for t in CLOSED_TRADES)
        total_pnl = total_pay - total_inv
        wins = sum(1 for t in CLOSED_TRADES if t.get("pnl", 0) > 0)
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
            opps = detect_safe_arb([p, k, g])
            now_unix = time.time()
            for o in opps:
                if o["cost"] > COST_THRESHOLD_OPEN:
                    continue
                pair_label = o["pair_label"]
                market_combo = f"{o['lower']['market_id']}|{o['higher']['market_id']}"
                cd_key = (pair_label, market_combo)
                if now_unix - LAST_OPEN_TS.get(cd_key, 0) < COOLDOWN_SEC:
                    continue
                if MARKET_TRADE_COUNT.get(market_combo, 0) >= MAX_TRADES_PER_MARKET:
                    continue
                # Symmetric sizing
                lower_avail = o["lower"]["up_usd_avail"]
                higher_avail = o["higher"]["down_usd_avail"]
                if lower_avail <= 0 or higher_avail <= 0:
                    continue
                # 50% of min depth (USD)
                usable_per_side = min(lower_avail, higher_avail) * DEPTH_USE_FRACTION
                invest_per_side = min(INVEST_PER_SIDE_TARGET, usable_per_side)
                if invest_per_side < INVEST_PER_SIDE_MIN:
                    continue
                # Equal shares
                max_price = max(o["up_ask"], o["down_ask"])
                target_shares = invest_per_side / max_price
                # Compute actual fill (in simulation: filled = min(target, available_at_best))
                lower_max = lower_avail / max(o["up_ask"], 0.01)
                higher_max = higher_avail / max(o["down_ask"], 0.01)
                lower_filled = min(target_shares, lower_max * DEPTH_USE_FRACTION)
                higher_filled = min(target_shares, higher_max * DEPTH_USE_FRACTION)
                # If under-filled, attempt completion
                final_l, final_h, t_l, t_h, total_cost, note = attempt_completion(
                    target_shares, lower_filled, higher_filled, o
                )
                if note == "ABORTED_imbalance":
                    continue
                if final_l < 0.01:
                    continue
                LAST_OPEN_TS[cd_key] = now_unix
                MARKET_TRADE_COUNT[market_combo] = MARKET_TRADE_COUNT.get(market_combo, 0) + 1
                open_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                third = o.get("third") or {}
                OPEN_TRADES[NEXT_TRADE_ID] = {
                    "trade_id": NEXT_TRADE_ID,
                    "open_ts": open_ts,
                    "pair_label": pair_label,
                    "lower_platform": o["lower"]["platform"],
                    "lower_market_id": o["lower"]["market_id"],
                    "lower_strike": o["lower"]["strike"],
                    "lower_up_ask": round(o["up_ask"], 4),
                    "higher_platform": o["higher"]["platform"],
                    "higher_market_id": o["higher"]["market_id"],
                    "higher_strike": o["higher"]["strike"],
                    "higher_down_ask": round(o["down_ask"], 4),
                    "third_platform": third.get("platform", ""),
                    "third_strike": third.get("strike", ""),
                    "strike_gap": round(o["strike_gap"], 2),
                    "cost_open": round(o["cost"], 4),
                    "target_shares": round(target_shares, 4),
                    "lower_shares_filled": round(lower_filled, 4),
                    "higher_shares_filled": round(higher_filled, 4),
                    "third_lower_shares": round(t_l, 4),
                    "third_higher_shares": round(t_h, 4),
                    "invest_total": round(total_cost, 4),
                    "completion_note": note,
                }
                NEXT_TRADE_ID += 1
            for tid in list(OPEN_TRADES.keys()):
                if tid in OPEN_TRADES:
                    settle_trade(tid)
            render_status(p, k, g, opps)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
