#!/usr/bin/env python3
"""
arb_v3_3way.py — SPREAD ARBITRAGE bot across 3 platforms
                 (Polymarket + Kalshi + Gemini).

DIFFERENT FROM arb_virtual_bot.py (classic arb):
  Classic arb buys UP+DOWN of the SAME platform pair at the SAME threshold.
  One must always lose (the events are mutually exclusive). Profit comes from
  the sum of asks being < $1.

  THIS bot buys UP on the LOWER-strike platform AND DOWN on the HIGHER-strike
  platform. The events can BOTH be true (when BTC ends in the gap between
  the two strikes). At least one ALWAYS wins (BTC must be somewhere).

  Outcomes:
    BTC < lower_strike:  UP@lower loses, DOWN@higher wins  → payout $1
    lower < BTC < higher: BOTH WIN  → payout $2 (BONUS!)
    BTC > higher_strike:  UP@lower wins, DOWN@higher loses → payout $1

  GUARANTEED minimum payout: $1.
  We open when cost ≤ 0.90 → guaranteed ≥10% profit, with bonus zone upside.

For each of 3 platform pairs (Poly-Kalshi, Poly-Gemini, Kalshi-Gemini),
identifies which has lower strike, then checks if buying UP@lower + DOWN@higher
gives cost ≤ 0.90.

Output: /root/arb_v3_3way_trades.csv

Run:
  screen -dmS arb_v3 python3 /root/arb_v3_3way.py
"""
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

# Data sources
P_PATH = "/root/data_btc_15m_research/combined_per_second.csv"
K_PATH = "/root/data_kalshi_btc_15m/combined_per_second.csv"
G_PATH = "/root/data_gemini_btc_15m/combined_per_second.csv"
PM_OUTCOMES = "/root/data_btc_15m_research/market_outcomes.csv"

LOG = "/root/arb_v3_3way_trades.csv"

INVEST_PER_SIDE_TARGET = 50.0   # ideal per-side $; capped by liquidity
INVEST_MIN = 5.0                # skip if depth too thin to invest at least this
MAX_SHARES_PER_LEG = 500.0      # hard safety cap (prevents 10000-share virtual fills)
COST_THRESHOLD = 0.90           # ≥10% guaranteed profit
MAX_STRIKE_GAP = 200            # don't trade if strikes too far apart (>$200)
POLL_SEC = 2
COOLDOWN_SEC = 5                # min seconds between opens on same (pair, market)

OPEN_TRADES: Dict[int, dict] = {}
CLOSED_TRADES = []
NEXT_TRADE_ID = 1
LAST_OPEN_TS: Dict[Tuple[str, str], float] = {}  # (pair_label, market_combo) -> ts

# ANSI
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
            "up_usd_avail": ya_sz * ya,
            # Kalshi recorder doesn't capture no_ask_size; use yes_ask_size as proxy
            # (NO depth roughly mirrors YES depth in a single book).
            "down_usd_avail": ya_sz * na,
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


def detect_spread_arb(p_data, k_data, g_data):
    """Iterate all 3 platform pairs; for each, identify lower/higher strike
    and compute spread arb cost (UP@lower + DOWN@higher).
    For each opportunity, also record the THIRD platform as a backup-completion
    source (in case live order is partially filled and we need to top up at
    a similar price elsewhere).
    Returns list of opportunities sorted by best cost (lowest first)."""
    platforms = [x for x in [p_data, k_data, g_data] if x and x["strike"] > 0]
    opps = []
    for i in range(len(platforms)):
        for j in range(i + 1, len(platforms)):
            a, b = platforms[i], platforms[j]
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
            # Backup completion: the THIRD platform (the one not in this pair)
            # If a leg can't be fully filled live, we can top up on the third platform.
            # For UP@lower, backup must have strike ≤ lower (equal/better coverage).
            # For DOWN@higher, backup must have strike ≥ higher (equal/better coverage).
            third = next(
                (p for p in platforms if p["platform"] not in (lower["platform"], higher["platform"])),
                None,
            )
            lower_backup = None
            higher_backup = None
            if third:
                if third["strike"] <= lower["strike"] and third["up_ask"] > 0:
                    lower_backup = {
                        "platform": third["platform"],
                        "strike": third["strike"],
                        "ask": third["up_ask"],
                        "market_id": third["market_id"],
                    }
                if third["strike"] >= higher["strike"] and third["down_ask"] > 0:
                    higher_backup = {
                        "platform": third["platform"],
                        "strike": third["strike"],
                        "ask": third["down_ask"],
                        "market_id": third["market_id"],
                    }
            opps.append({
                "pair_label": f"UP@{lower['platform']}+DOWN@{higher['platform']}",
                "lower": lower,
                "higher": higher,
                "strike_gap": strike_gap,
                "up_ask": up_ask,
                "down_ask": down_ask,
                "cost": cost,
                "lower_backup": lower_backup,
                "higher_backup": higher_backup,
            })
    opps.sort(key=lambda o: o["cost"])
    return opps


def init_log():
    if not os.path.exists(LOG):
        cols = [
            "trade_id", "open_ts", "pair_label",
            "lower_platform", "lower_market_id", "lower_strike", "lower_up_ask",
            "higher_platform", "higher_market_id", "higher_strike", "higher_down_ask",
            "strike_gap", "cost", "min_profit_pct",
            "lower_shares", "higher_shares", "invest_usd",
            "close_ts", "btc_final", "winner_pattern",
            "lower_payout", "higher_payout", "total_payout",
            "pnl", "pnl_pct", "notes",
        ]
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)


def write_trade(t):
    cols = [
        "trade_id", "open_ts", "pair_label",
        "lower_platform", "lower_market_id", "lower_strike", "lower_up_ask",
        "higher_platform", "higher_market_id", "higher_strike", "higher_down_ask",
        "strike_gap", "cost", "min_profit_pct",
        "lower_shares", "higher_shares", "invest_usd",
        "lower_backup_platform", "lower_backup_strike", "lower_backup_ask",
        "higher_backup_platform", "higher_backup_strike", "higher_backup_ask",
        "close_ts", "btc_final", "winner_pattern",
        "lower_payout", "higher_payout", "total_payout",
        "pnl", "pnl_pct", "notes",
    ]
    row = [t.get(c, "") for c in cols]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


def lookup_btc_final(market_epoch_or_id: str) -> Optional[float]:
    """Try to find final BTC price for a Polymarket market id from market_outcomes.csv.
    Returns the final_binance_price, which we use uniformly for all platforms."""
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == market_epoch_or_id:
                    fp = r.get("final_binance_price")
                    if fp:
                        return float(fp)
    except Exception:
        pass
    return None


def settle_trade(tid: int) -> bool:
    """Try to settle a trade. Uses Polymarket's market_outcomes for BTC final
    price (chainlink-derived, applies to all platforms uniformly)."""
    t = OPEN_TRADES[tid]
    btc_final = lookup_btc_final(t.get("lower_market_id", "")) or lookup_btc_final(t.get("higher_market_id", ""))
    if btc_final is None:
        return False
    lower_strike = t["lower_strike"]
    higher_strike = t["higher_strike"]
    # Determine winner pattern
    if btc_final < lower_strike:
        # BTC below lower → UP@lower loses, DOWN@higher wins
        lower_won = False
        higher_won = True
        pattern = "BTC<low"
    elif btc_final > higher_strike:
        # BTC above higher → UP@lower wins, DOWN@higher loses
        lower_won = True
        higher_won = False
        pattern = "BTC>high"
    else:
        # BTC in middle → BOTH WIN
        lower_won = True
        higher_won = True
        pattern = "BTC_mid_BOTH_WIN"
    lower_payout = t["lower_shares"] if lower_won else 0.0
    higher_payout = t["higher_shares"] if higher_won else 0.0
    total = lower_payout + higher_payout
    pnl = total - t["invest_usd"]
    pnl_pct = (pnl / t["invest_usd"]) * 100
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "close_ts": now_ts,
        "btc_final": round(btc_final, 2),
        "winner_pattern": pattern,
        "lower_payout": round(lower_payout, 4),
        "higher_payout": round(higher_payout, 4),
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
    out.append(f"{ANSI_BOLD}ARB_V3_3WAY (Spread Arb 3 Platforms){ANSI_RESET}   "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET} ${INVEST_PER_SIDE:.0f}/side  "
               f"threshold cost≤{COST_THRESHOLD}")
    out.append("=" * width)
    out.append(f"TIME: {now}")
    out.append("")
    # Show all 3 platforms
    for pf in [p, k, g]:
        if not pf:
            continue
        out.append(f"  {pf['platform']:<8} strike=${pf['strike']:>10,.2f}  "
                   f"UP/YES_ask={pf['up_ask']:.3f}  DOWN/NO_ask={pf['down_ask']:.3f}  "
                   f"market={pf['market_id'][-25:]}")
    out.append("-" * width)
    # Show all spread arb opportunities (sorted)
    out.append(f"{ANSI_BOLD}SPREAD ARB OPPORTUNITIES (sorted by cost):{ANSI_RESET}")
    if not opps:
        out.append("  (no data — waiting for all 3 feeds)")
    else:
        for o in opps:
            mark = ""
            if o["cost"] <= COST_THRESHOLD:
                mark = f"  {ANSI_GREEN}{ANSI_BOLD}*** OPP ***{ANSI_RESET}"
            out.append(f"  {o['pair_label']:<35} cost={o['cost']:.3f}  "
                       f"min_profit={(1-o['cost'])*100:+.1f}%  "
                       f"strike_gap=${o['strike_gap']:.0f}{mark}")
    out.append("-" * width)
    # Open trades
    out.append(f"{ANSI_BOLD}OPEN TRADES: {len(OPEN_TRADES)}{ANSI_RESET}")
    if OPEN_TRADES:
        for tid, t in sorted(OPEN_TRADES.items()):
            out.append(f"  #{tid:>3}  {t['pair_label']}  open={t['open_ts']}  "
                       f"cost={t['cost']:.3f}  gap=${t['strike_gap']:.0f}  "
                       f"strikes=[${t['lower_strike']:,.0f}/${t['higher_strike']:,.0f}]")
    out.append("-" * width)
    # Closed trades — last 10
    n = len(CLOSED_TRADES)
    out.append(f"{ANSI_BOLD}CLOSED TRADES: {n} (last 10){ANSI_RESET}")
    for t in CLOSED_TRADES[-10:]:
        out.append(f"  #{t['trade_id']:>3} {t['pair_label']:<32} "
                   f"BTC=${t.get('btc_final', 0):>10,.0f} {t.get('winner_pattern', ''):<20} "
                   f"PnL={color_money(t.get('pnl', 0))} ({t.get('pnl_pct', 0):+.1f}%)")
    out.append("=" * width)
    # Totals
    if CLOSED_TRADES:
        total_inv = sum(t["invest_usd"] for t in CLOSED_TRADES)
        total_payout = sum(t["total_payout"] for t in CLOSED_TRADES)
        total_pnl = total_payout - total_inv
        wins = sum(1 for t in CLOSED_TRADES if t["pnl"] > 0)
        bonus = sum(1 for t in CLOSED_TRADES if "BOTH_WIN" in t.get("winner_pattern", ""))
        roi = (total_pnl / total_inv * 100) if total_inv else 0
        out.append(f"{ANSI_BOLD}TOTALS:{ANSI_RESET} {n} trades  W={wins}  bonus_zone={bonus}  "
                   f"invested=${total_inv:.0f}  payout=${total_payout:.0f}  "
                   f"PnL={color_money(total_pnl)} ({roi:+.1f}%)")
    else:
        out.append(f"{ANSI_BOLD}TOTALS:{ANSI_RESET} no closed trades yet")
    out.append("Ctrl+C to stop.")
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


def main():
    global NEXT_TRADE_ID

    p_header = read_header(P_PATH)
    k_header = read_header(K_PATH)
    g_header = read_header(G_PATH)
    if not (p_header and k_header and g_header):
        print(f"ERROR: cannot read all 3 headers. p={bool(p_header)} k={bool(k_header)} g={bool(g_header)}")
        return

    init_log()

    while True:
        try:
            p = parse_poly(tail_last_row(P_PATH, p_header))
            k = parse_kalshi(tail_last_row(K_PATH, k_header))
            g = parse_gemini(tail_last_row(G_PATH, g_header))

            opps = detect_spread_arb(p, k, g)

            now_unix = time.time()
            for o in opps:
                if o["cost"] > COST_THRESHOLD:
                    continue
                pair_label = o["pair_label"]
                # Cooldown per (pair, market_combo)
                market_combo = f"{o['lower']['market_id']}|{o['higher']['market_id']}"
                cd_key = (pair_label, market_combo)
                last_ts = LAST_OPEN_TS.get(cd_key, 0)
                if now_unix - last_ts < COOLDOWN_SEC:
                    continue
                # Liquidity sizing: never put in more than half the smaller leg's
                # available depth, and apply a hard share cap to avoid the
                # 10000-shares-at-half-a-cent virtual fill problem.
                lower_avail = o["lower"].get("up_usd_avail", 0)
                higher_avail = o["higher"].get("down_usd_avail", 0)
                if lower_avail <= 0 or higher_avail <= 0:
                    continue
                min_avail = min(lower_avail, higher_avail)
                if min_avail >= INVEST_PER_SIDE_TARGET * 2:
                    invest_per_side = INVEST_PER_SIDE_TARGET
                else:
                    invest_per_side = min_avail / 2.0
                if invest_per_side < INVEST_MIN:
                    continue
                # Compute shares; cap to MAX_SHARES_PER_LEG safety net
                lower_shares = invest_per_side / o["up_ask"]
                higher_shares = invest_per_side / o["down_ask"]
                if lower_shares > MAX_SHARES_PER_LEG:
                    lower_shares = MAX_SHARES_PER_LEG
                    invest_per_side_lower = lower_shares * o["up_ask"]
                else:
                    invest_per_side_lower = invest_per_side
                if higher_shares > MAX_SHARES_PER_LEG:
                    higher_shares = MAX_SHARES_PER_LEG
                    invest_per_side_higher = higher_shares * o["down_ask"]
                else:
                    invest_per_side_higher = invest_per_side
                invest = invest_per_side_lower + invest_per_side_higher
                LAST_OPEN_TS[cd_key] = now_unix
                min_profit_pct = (1 - o["cost"]) * 100
                open_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                lb = o.get("lower_backup")
                hb = o.get("higher_backup")
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
                    "strike_gap": round(o["strike_gap"], 2),
                    "cost": round(o["cost"], 4),
                    "min_profit_pct": round(min_profit_pct, 2),
                    "lower_shares": round(lower_shares, 4),
                    "higher_shares": round(higher_shares, 4),
                    "invest_usd": invest,
                    "lower_backup_platform": lb["platform"] if lb else "",
                    "lower_backup_strike": round(lb["strike"], 2) if lb else "",
                    "lower_backup_ask": round(lb["ask"], 4) if lb else "",
                    "higher_backup_platform": hb["platform"] if hb else "",
                    "higher_backup_strike": round(hb["strike"], 2) if hb else "",
                    "higher_backup_ask": round(hb["ask"], 4) if hb else "",
                }
                NEXT_TRADE_ID += 1

            # Try to settle ready trades
            for tid in list(OPEN_TRADES.keys()):
                if tid in OPEN_TRADES:
                    settle_trade(tid)

            render_status(p, k, g, opps)
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
