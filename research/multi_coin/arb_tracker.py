#!/usr/bin/env python3
"""
arb_tracker.py — live cross-platform arbitrage tracker (Polymarket + Kalshi BTC 15m).

Polls both recorders' combined_per_second.csv every 2 seconds.
For each direction (A=PolyUP+KalshiNO, B=PolyDOWN+KalshiYES) it tracks the
"in-opportunity" state. When state transitions True (cost drops below
THRESHOLD), it logs an OPP_START. When it transitions False, it logs an
OPP_END with the duration.

The result: a clean log of DISTINCT opportunities, no per-second double-
counting. Run for 24h, then count rows to see actual opportunity rate.

Output: /root/arb_tracker_log.csv

Usage:
  screen -dmS arb_tracker python3 /root/arb_tracker.py
"""
import csv
import os
import subprocess
import time
from datetime import datetime

K = "/root/data_kalshi_btc_15m/combined_per_second.csv"
P = "/root/data_btc_15m_research/combined_per_second.csv"
LOG = "/root/arb_tracker_log.csv"

THRESHOLD_COST = 0.92        # opportunities with cost<this are logged (gives ≥8% profit)
MAX_STRIKE_DIFF = 50         # only count if Kalshi strike close to Polymarket target
POLL_SEC = 2


def read_header(path):
    try:
        with open(path) as fh:
            return fh.readline().strip().split(",")
    except Exception:
        return None


def tail_last_row(path, header):
    """Use system `tail -1` for fast last-line access."""
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
        }
    except Exception:
        return None


def main():
    k_header = read_header(K)
    p_header = read_header(P)
    if not k_header or not p_header:
        print(f"ERROR: cannot read headers from {K} or {P}")
        return

    if not os.path.exists(LOG):
        with open(LOG, "w") as fh:
            fh.write(
                "ts,direction,event,cost,profit_pct,duration_sec,"
                "strike_diff,kalshi_ticker,poly_slug,poly_ask,kalshi_ask\n"
            )

    state = {"A": False, "B": False}
    start_ts = {"A": None, "B": None}
    start_cost = {"A": None, "B": None}
    start_meta = {"A": None, "B": None}

    print(f"arb_tracker started, threshold cost<{THRESHOLD_COST}")
    print(f"logging to {LOG}")
    print(f"polling every {POLL_SEC}s")
    print()

    while True:
        try:
            k = parse_kalshi(tail_last_row(K, k_header))
            p = parse_poly(tail_last_row(P, p_header))
            now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if k and p:
                strike_diff = (
                    abs(k["strike"] - p["tgt"])
                    if k["strike"] > 0 and p["tgt"] > 0
                    else 999
                )

                # Direction A: Poly UP + Kalshi NO
                cost_a = (
                    p["ua"] + k["na"]
                    if p["ua"] > 0 and k["na"] > 0
                    else 999
                )
                # Direction B: Poly DOWN + Kalshi YES
                cost_b = (
                    p["da"] + k["ya"]
                    if p["da"] > 0 and k["ya"] > 0
                    else 999
                )

                for direction, cost, poly_ask, kalshi_ask in [
                    ("A", cost_a, p.get("ua", 0), k.get("na", 0)),
                    ("B", cost_b, p.get("da", 0), k.get("ya", 0)),
                ]:
                    below = cost < THRESHOLD_COST and strike_diff < MAX_STRIKE_DIFF

                    if below and not state[direction]:
                        # Opportunity STARTED
                        state[direction] = True
                        start_ts[direction] = time.time()
                        start_cost[direction] = cost
                        start_meta[direction] = {
                            "strike_diff": strike_diff,
                            "kalshi_ticker": k["ticker"],
                            "poly_slug": p["slug"],
                            "poly_ask": poly_ask,
                            "kalshi_ask": kalshi_ask,
                        }
                        profit_pct = (1.0 - cost) * 100
                        line = (
                            f"{now_ts},{direction},START,{cost:.4f},"
                            f"{profit_pct:.1f},,{strike_diff:.0f},"
                            f"{k['ticker']},{p['slug']},"
                            f"{poly_ask:.4f},{kalshi_ask:.4f}\n"
                        )
                        with open(LOG, "a") as fh:
                            fh.write(line)
                        print(
                            f"{now_ts} {direction} START cost={cost:.3f} "
                            f"profit={profit_pct:.1f}% strike_diff=${strike_diff:.0f}"
                        )
                    elif (not below) and state[direction]:
                        # Opportunity ENDED
                        state[direction] = False
                        duration = (
                            time.time() - start_ts[direction]
                            if start_ts[direction]
                            else 0
                        )
                        meta = start_meta[direction] or {}
                        line = (
                            f"{now_ts},{direction},END,,,"
                            f"{duration:.0f},,,,,\n"
                        )
                        with open(LOG, "a") as fh:
                            fh.write(line)
                        print(
                            f"{now_ts} {direction} END   duration={duration:.0f}s"
                        )
                        start_ts[direction] = None
                        start_cost[direction] = None
                        start_meta[direction] = None
        except Exception as e:
            try:
                with open(LOG, "a") as fh:
                    fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},*,ERROR,,,,,,,{type(e).__name__}:{e},\n")
            except Exception:
                pass

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
