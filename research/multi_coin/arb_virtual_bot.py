#!/usr/bin/env python3
"""
arb_virtual_bot.py — virtual cross-platform arbitrage bot.

When the cost of (Polymarket + Kalshi) drops to ≤ 0.90 (≥10% profit),
records a VIRTUAL BUY of $50 on each side. Then waits for both markets
to settle and writes the final PnL of the trade.

Output: /root/arb_virtual_trades.csv
Columns:
  trade_id           — sequential
  open_ts            — when the opp was detected and we virtually bought
  direction          — A (PolyUP+KalshiNO) or B (PolyDOWN+KalshiYES)
  poly_slug          — Polymarket market id
  kalshi_ticker      — Kalshi market ticker
  poly_ask           — price we paid on Polymarket
  kalshi_ask         — price we paid on Kalshi
  cost               — poly_ask + kalshi_ask
  profit_pct_open    — profit % at open (1.0 - cost) × 100
  poly_shares        — $50 / poly_ask
  kalshi_shares      — $50 / kalshi_ask
  invest_usd         — actual $ invested per side ($50 each = $100 total)
  poly_close_ts      — when poly settled
  kalshi_close_ts    — when kalshi settled
  poly_winner        — UP / DOWN / unknown
  kalshi_winner      — YES / NO / unknown
  poly_payout        — what Polymarket paid back
  kalshi_payout      — what Kalshi paid back
  total_payout       — sum
  pnl                — total_payout - $100
  pnl_pct            — pnl / 100
  notes              — any settlement issues

Designed to run on Germany alongside the tracker. Reads:
  /root/data_kalshi_btc_15m/combined_per_second.csv
  /root/data_btc_15m_research/combined_per_second.csv
  /root/data_btc_15m_research/market_outcomes.csv  ← for poly settlements

Run:
  screen -dmS arb_virtual python3 /root/arb_virtual_bot.py
"""
import csv
import os
import subprocess
import time
from datetime import datetime
from typing import Optional

K = "/root/data_kalshi_btc_15m/combined_per_second.csv"
P = "/root/data_btc_15m_research/combined_per_second.csv"
PM_OUTCOMES = "/root/data_btc_15m_research/market_outcomes.csv"
LOG = "/root/arb_virtual_trades.csv"

INVEST_PER_SIDE = 50.0
# Tiered entry: open up to 3 trades per (direction, market) at increasing
# profit thresholds. Once tier N is opened, lower tiers cannot re-open
# (only higher-profit tiers going forward).
TIER_COST_THRESHOLDS = [0.90, 0.85, 0.80]   # 10% / 15% / 20% profit
MAX_STRIKE_DIFF = 50
POLL_SEC = 2
# Refuse to open trades when the data feed is stale (last row older than this)
MAX_FEED_AGE_SEC = 30
# Refuse extreme imbalanced trades — abs(poly_ask - kalshi_ask) must be <= this.
# Empirical (19 trades, 05/05): kept trades made +$209, filtered ones made -$70.
MAX_PRICE_GAP = 0.4

OPEN_TRADES = {}  # trade_id -> trade dict
NEXT_TRADE_ID = 1


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


def parse_kalshi(row):
    if not row:
        return None
    try:
        return {
            "epoch": int(row.get("epoch_sec") or 0),
            "ya": float(row.get("yes_ask") or 0),
            "na": float(row.get("no_ask") or 0),
            "strike": float(row.get("floor_strike") or 0),
            "ticker": row.get("event_ticker", "") or "",
            "market_ticker": row.get("market_ticker", "") or "",
            "status": row.get("status", "") or "",
            "last_price": float(row.get("last_price") or 0),
            "close_time": row.get("close_time", "") or "",
        }
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
            "tgt": float(row.get("target_chainlink_at_open") or 0),
            "slug": row.get("market_slug", "") or "",
            "market_epoch": int(row.get("market_epoch") or 0),
        }
    except Exception:
        return None


def lookup_poly_outcome(slug: str):
    """Read market_outcomes.csv to find the winner_side for a slug.
    Returns (winner_side, final_price) or (None, None) if not found."""
    try:
        with open(PM_OUTCOMES) as fh:
            for r in csv.DictReader(fh):
                if r.get("market_slug") == slug:
                    return r.get("winner_side", ""), float(r.get("final_binance_price") or 0)
    except Exception:
        pass
    return None, None


def lookup_kalshi_outcome(ticker: str, market_ticker: str, strike: float):
    """Look at recent kalshi rows for the same ticker, find when status=final.
    Returns (winner_side, last_price) or (None, None)."""
    try:
        # Search backwards through the file (last few hundred lines is usually enough)
        out = subprocess.run(
            ["tail", "-n", "5000", K], capture_output=True, text=True, timeout=10
        )
        if not out.stdout:
            return None, None
        header = read_header(K)
        if not header:
            return None, None
        # Read backwards through the chunk to find latest matching row
        lines = out.stdout.strip().split("\n")
        for line in reversed(lines):
            values = line.split(",")
            if len(values) < len(header):
                continue
            row = dict(zip(header, values))
            # Match on market_ticker if available, else event_ticker
            if market_ticker and row.get("market_ticker") == market_ticker:
                lp = float(row.get("last_price") or 0)
                if lp >= 0.99:
                    return "YES", lp
                if lp <= 0.01:
                    return "NO", lp
                # status check
                if row.get("status", "").lower() in ("final", "settled", "finalized"):
                    return ("YES" if lp >= 0.5 else "NO"), lp
        return None, None
    except Exception:
        return None, None


def write_trade_row(trade):
    """Append a settled trade row to LOG."""
    cols = [
        "trade_id", "open_ts", "direction", "poly_slug", "kalshi_ticker",
        "poly_ask", "kalshi_ask", "cost", "profit_pct_open",
        "poly_shares", "kalshi_shares", "invest_usd",
        "poly_close_ts", "kalshi_close_ts",
        "poly_winner", "kalshi_winner",
        "poly_payout", "kalshi_payout", "total_payout",
        "pnl", "pnl_pct", "notes",
    ]
    row = [trade.get(c, "") for c in cols]
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(row)


def init_log():
    if not os.path.exists(LOG):
        cols = [
            "trade_id", "open_ts", "direction", "poly_slug", "kalshi_ticker",
            "poly_ask", "kalshi_ask", "cost", "profit_pct_open",
            "poly_shares", "kalshi_shares", "invest_usd",
            "poly_close_ts", "kalshi_close_ts",
            "poly_winner", "kalshi_winner",
            "poly_payout", "kalshi_payout", "total_payout",
            "pnl", "pnl_pct", "notes",
        ]
        with open(LOG, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(cols)


def settle_trade_if_ready(trade_id: int) -> bool:
    """Try to settle one open trade. Returns True if settled (and removed from OPEN_TRADES)."""
    t = OPEN_TRADES[trade_id]
    poly_winner, poly_final = lookup_poly_outcome(t["poly_slug"])
    if poly_winner is None:
        return False  # poly not settled yet
    # Kalshi settle — check by status / last_price
    kalshi_winner, kalshi_lp = lookup_kalshi_outcome(t["kalshi_ticker"], t.get("kalshi_market_ticker", ""), t.get("strike", 0))
    if kalshi_winner is None:
        # Fallback — derive from poly_final + strike
        if poly_final and t.get("strike"):
            kalshi_winner = "YES" if poly_final > t["strike"] else "NO"
            kalshi_lp = 1.0 if kalshi_winner == "YES" else 0.0
        else:
            return False  # truly not settled

    # Compute payouts
    direction = t["direction"]
    poly_payout = 0.0
    kalshi_payout = 0.0
    if direction == "A":  # bought PolyUP + KalshiNO
        if poly_winner == "UP":
            poly_payout = t["poly_shares"] * 1.0
        if kalshi_winner == "NO":
            kalshi_payout = t["kalshi_shares"] * 1.0
    elif direction == "B":  # bought PolyDOWN + KalshiYES
        if poly_winner == "DOWN":
            poly_payout = t["poly_shares"] * 1.0
        if kalshi_winner == "YES":
            kalshi_payout = t["kalshi_shares"] * 1.0

    total_payout = poly_payout + kalshi_payout
    pnl = total_payout - t["invest_usd"]
    pnl_pct = (pnl / t["invest_usd"]) * 100

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t.update({
        "poly_close_ts": now_ts,
        "kalshi_close_ts": now_ts,
        "poly_winner": poly_winner,
        "kalshi_winner": kalshi_winner,
        "poly_payout": round(poly_payout, 4),
        "kalshi_payout": round(kalshi_payout, 4),
        "total_payout": round(total_payout, 4),
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
        "notes": "",
    })
    write_trade_row(t)
    print(f"SETTLED trade #{trade_id} dir={direction} pnl=${pnl:+.2f} ({pnl_pct:+.1f}%) "
          f"[poly:{poly_winner} kalshi:{kalshi_winner}]")
    del OPEN_TRADES[trade_id]
    return True


CLOSED_TRADES = []  # list of settled trade dicts (in memory for display)
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


def render_status(latest_k, latest_p):
    """V3-style updating screen — full clear + redraw."""
    width = 110
    # Aggressive clear: home + clear screen + clear scrollback
    out = ["\033[H\033[2J\033[3J\033[?25l"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tiers_str = " / ".join(
        f"T{i+1}≤{t} ({(1-t)*100:.0f}%)" for i, t in enumerate(TIER_COST_THRESHOLDS)
    )
    out.append(f"{ANSI_BOLD}ARB_VIRTUAL_BOT{ANSI_RESET}   "
               f"mode={ANSI_CYAN}DRY-RUN{ANSI_RESET} "
               f"${INVEST_PER_SIDE:.0f}/side  total=${INVEST_PER_SIDE*2:.0f}/trade")
    out.append("=" * width)
    out.append(f"LOCAL TIME : {now}")
    out.append(f"TIERS      : {tiers_str}  | strike_diff < ${MAX_STRIKE_DIFF}")
    out.append("-" * width)

    # Live status
    if latest_k and latest_p:
        sd = (abs(latest_k["strike"] - latest_p["tgt"])
              if latest_k["strike"] > 0 and latest_p["tgt"] > 0 else 999)
        ca = (latest_p["ua"] + latest_k["na"]) if latest_p["ua"] > 0 and latest_k["na"] > 0 else 0
        cb = (latest_p["da"] + latest_k["ya"]) if latest_p["da"] > 0 and latest_k["ya"] > 0 else 0
        out.append(f"POLY  : {latest_p['slug'][-30:]:<30}  UP_ask={latest_p['ua']:.3f} "
                   f"DOWN_ask={latest_p['da']:.3f}  target=${latest_p['tgt']:,.2f}")
        out.append(f"KALSHI: {latest_k['ticker'][-30:]:<30}  YES_ask={latest_k['ya']:.3f} "
                   f"NO_ask={latest_k['na']:.3f}  strike=${latest_k['strike']:,.2f}")
        out.append(f"strike_diff=${sd:.0f}")

        def cost_line(label, c):
            if c <= 0 or c >= 999:
                return f"  {label} — no data"
            pct = (1 - c) * 100
            mark = ""
            tier_hits = [i+1 for i, thr in enumerate(TIER_COST_THRESHOLDS) if c <= thr]
            if tier_hits:
                tiers_label = "+".join(f"T{t}" for t in tier_hits)
                mark = f"  {ANSI_GREEN}{ANSI_BOLD}*** OPP {tiers_label} ***{ANSI_RESET}"
            elif c < 1.0:
                mark = f"  (below $1 but above tier-1 threshold)"
            else:
                mark = "  (above $1, no arb)"
            return f"  {label}  cost={c:.3f}  profit={pct:+.1f}%{mark}"

        out.append(cost_line("Direction A (PolyUP+KalshiNO):  ", ca))
        out.append(cost_line("Direction B (PolyDOWN+KalshiYES):", cb))
    else:
        out.append("waiting for data feeds...")
    out.append("-" * width)

    # Open trades
    out.append(f"{ANSI_BOLD}OPEN TRADES: {len(OPEN_TRADES)}{ANSI_RESET}")
    if OPEN_TRADES:
        for tid, t in sorted(OPEN_TRADES.items()):
            tier = t.get("tier", "")
            tier_str = f"T{tier}" if tier else ""
            out.append(f"  #{tid:>3} {tier_str:>3}  dir={t['direction']}  open={t['open_ts']}  "
                       f"cost={t['cost']:.3f} ({t['profit_pct_open']:+.1f}%)  "
                       f"poly@{t['poly_ask']:.3f} kalshi@{t['kalshi_ask']:.3f}  "
                       f"slug={t['poly_slug'][-12:]}")
    else:
        out.append("  (none)")
    out.append("-" * width)

    # Closed trades — last 10
    n_closed = len(CLOSED_TRADES)
    out.append(f"{ANSI_BOLD}CLOSED TRADES: {n_closed} (last 10 below){ANSI_RESET}")
    for t in CLOSED_TRADES[-10:]:
        out.append(f"  #{t['trade_id']:>3}  dir={t['direction']}  paid=${t['invest_usd']:.0f}  "
                   f"payout=${t['total_payout']:.2f}  PnL={color_money(t['pnl'])} "
                   f"({t['pnl_pct']:+.1f}%)  [poly:{t['poly_winner']} kalshi:{t['kalshi_winner']}]")
    out.append("=" * width)

    # Aggregates: today, this week, this month, all-time
    if CLOSED_TRADES:
        from datetime import datetime as _dt, timedelta as _td
        now_dt = _dt.now()
        today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - _td(days=now_dt.weekday())  # Mon = start of week
        month_start = today_start.replace(day=1)

        def agg(filtered):
            if not filtered:
                return (0, 0, 0, 0, 0.0, 0.0)
            n = len(filtered)
            w = sum(1 for t in filtered if t["pnl"] > 0)
            l = sum(1 for t in filtered if t["pnl"] < 0)
            p = sum(1 for t in filtered if abs(t["pnl"]) < 0.01)
            inv = sum(t["invest_usd"] for t in filtered)
            pnl = sum(t["pnl"] for t in filtered)
            return (n, w, l, p, inv, pnl)

        def parse_ts(s):
            try:
                return _dt.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

        today_t = [t for t in CLOSED_TRADES if (parse_ts(t.get("poly_close_ts","")) or now_dt) >= today_start]
        week_t  = [t for t in CLOSED_TRADES if (parse_ts(t.get("poly_close_ts","")) or now_dt) >= week_start]
        month_t = [t for t in CLOSED_TRADES if (parse_ts(t.get("poly_close_ts","")) or now_dt) >= month_start]
        all_t   = CLOSED_TRADES

        out.append(f"{ANSI_BOLD}AGGREGATES:{ANSI_RESET}")
        for label, sub in (("TODAY  ", today_t), ("WEEK   ", week_t), ("MONTH  ", month_t), ("ALLTIME", all_t)):
            n, w, l, p, inv, pnl = agg(sub)
            roi = (pnl/inv*100) if inv else 0
            wpct = (100*w/n) if n else 0
            out.append(f"  {label}: trades={n:>4}  W={w:>3} L={l:>3} P={p:>3}  "
                       f"({wpct:>3.0f}% win)  inv=${inv:>7.0f}  PnL={color_money(pnl)} ({roi:+.1f}%)")
    else:
        out.append(f"{ANSI_BOLD}AGGREGATES:{ANSI_RESET} no closed trades yet")
    out.append("Ctrl+C to stop.")

    sys_stdout = __import__("sys").stdout
    sys_stdout.write("\n".join(out) + "\n")
    sys_stdout.flush()


def main():
    global NEXT_TRADE_ID

    k_header = read_header(K)
    p_header = read_header(P)
    if not k_header or not p_header:
        print("ERROR: cannot read CSV headers")
        return

    init_log()

    # Tiered entry tracking: per (direction, market_id), which tier indices were already opened.
    opened_tiers = {}  # (direction, (slug, ticker)) -> set(tier_index)

    while True:
        try:
            k = parse_kalshi(tail_last_row(K, k_header))
            p = parse_poly(tail_last_row(P, p_header))

            # Freshness check — refuse to open trades if either feed is stale
            now_epoch = int(time.time())
            k_age = now_epoch - k.get("epoch", 0) if k else 999
            p_age = now_epoch - p.get("epoch", 0) if p else 999
            feeds_fresh = k_age <= MAX_FEED_AGE_SEC and p_age <= MAX_FEED_AGE_SEC

            if k and p and feeds_fresh:
                strike_diff = (
                    abs(k["strike"] - p["tgt"])
                    if k["strike"] > 0 and p["tgt"] > 0
                    else 999
                )
                cost_a = (p["ua"] + k["na"]) if p["ua"] > 0 and k["na"] > 0 else 999
                cost_b = (p["da"] + k["ya"]) if p["da"] > 0 and k["ya"] > 0 else 999

                for direction, cost, poly_ask, kalshi_ask in [
                    ("A", cost_a, p.get("ua", 0), k.get("na", 0)),
                    ("B", cost_b, p.get("da", 0), k.get("ya", 0)),
                ]:
                    if cost <= 0 or cost >= 999 or strike_diff >= MAX_STRIKE_DIFF:
                        continue
                    # Skip extreme price imbalances — empirically these lose money
                    if abs(poly_ask - kalshi_ask) > MAX_PRICE_GAP:
                        continue
                    market_id = (p["slug"], k["ticker"])
                    key = (direction, market_id)
                    if key not in opened_tiers:
                        opened_tiers[key] = set()
                    # Open every tier we now qualify for that we haven't opened yet
                    for tier_idx, thr in enumerate(TIER_COST_THRESHOLDS):
                        if tier_idx in opened_tiers[key]:
                            continue
                        if cost > thr:
                            continue
                        opened_tiers[key].add(tier_idx)
                        poly_shares = INVEST_PER_SIDE / poly_ask
                        kalshi_shares = INVEST_PER_SIDE / kalshi_ask
                        invest = INVEST_PER_SIDE * 2
                        profit_pct_open = (1.0 - cost) * 100
                        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        OPEN_TRADES[NEXT_TRADE_ID] = {
                            "trade_id": NEXT_TRADE_ID,
                            "open_ts": now_ts,
                            "direction": direction,
                            "tier": tier_idx + 1,
                            "tier_threshold": thr,
                            "poly_slug": p["slug"],
                            "kalshi_ticker": k["ticker"],
                            "kalshi_market_ticker": k.get("market_ticker", ""),
                            "strike": k["strike"],
                            "poly_ask": round(poly_ask, 4),
                            "kalshi_ask": round(kalshi_ask, 4),
                            "cost": round(cost, 4),
                            "profit_pct_open": round(profit_pct_open, 2),
                            "poly_shares": round(poly_shares, 4),
                            "kalshi_shares": round(kalshi_shares, 4),
                            "invest_usd": invest,
                        }
                        NEXT_TRADE_ID += 1

            # Settle any ready trades; on settle, append to CLOSED_TRADES
            ready_to_settle = list(OPEN_TRADES.keys())
            for tid in ready_to_settle:
                if tid not in OPEN_TRADES:
                    continue
                t_before = OPEN_TRADES[tid]
                if settle_trade_if_ready(tid):
                    # settle_trade_if_ready already wrote to CSV and removed from OPEN
                    # we need to re-fetch the settled trade dict — it was modified in place
                    CLOSED_TRADES.append(t_before)

            # Render screen
            render_status(k, p)

        except Exception as e:
            try:
                with open(LOG, "a") as fh:
                    fh.write(f"# error {datetime.now()}: {type(e).__name__}: {e}\n")
            except Exception:
                pass

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
