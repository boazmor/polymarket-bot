#!/usr/bin/env python3
"""arb_v5_live.py — V5 BASIC live trader.

Same opportunity-detection logic as arb_virtual_bot_v5.py, but actually
places orders on both Polymarket and Predict.fun.

Caps:
- MAX_TRADES_PER_WINDOW = 5
- INVEST_PER_SIDE = 2.0 USD (small test size)

Output:
- /root/arb_v5_live_trades.csv     — one row per closed trade (settled)
- /root/arb_v5_live_orders.csv     — every order request + response

Usage:
    python3 arb_v5_live.py --max-windows 1
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/root")
from predict_trader import PredictTrader

# Polymarket trading via existing wallet wrapper
from dotenv import load_dotenv

ENV_PATH = "/root/live/btc_5m/.env"
load_dotenv(ENV_PATH, override=True)

P_DATA = "/root/data_btc_15m_research/combined_per_second.csv"
PR_DATA = "/root/data_predict_btc_15m/combined_per_second.csv"
PR_MARKETS = "/root/data_predict_btc_15m/markets.csv"
LIVE_TRADES = "/root/arb_v5_live_trades.csv"
LIVE_ORDERS = "/root/arb_v5_live_orders.csv"

INVEST_PER_SIDE = 2.0
COST_THRESHOLD = 0.90
SINGLE_LEG_MAX_ASK = 0.80
MAX_TRADES_PER_WINDOW = 5
COOLDOWN_SEC = 5
POLL_SEC = 2
MAX_FEED_AGE_SEC = 10

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

# ---- helpers ----

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def log_order(stage, **kw):
    """Append one row per order action to arb_v5_live_orders.csv."""
    row = {"ts": now_iso(), "stage": stage, **kw}
    new = not os.path.exists(LIVE_ORDERS)
    with open(LIVE_ORDERS, "a", newline="") as f:
        if new:
            f.write("ts,stage," + ",".join(k for k in row if k not in ("ts", "stage")) + "\n")
        f.write(",".join(str(v).replace(",", ";")[:300] for v in row.values()) + "\n")
    print(f"[{row['ts'][11:19]}] {stage}: " +
          " ".join(f"{k}={str(v)[:60]}" for k, v in kw.items() if k != "raw"))


def tail_last_row(path):
    """Read just the last row from a CSV (efficient for tail-like access)."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(8192, size)
        f.seek(-chunk, 2)
        block = f.read(chunk).decode("utf-8", errors="replace")
    lines = [ln for ln in block.split("\n") if ln.strip()]
    return lines[-1] if lines else None


def header(path):
    with open(path) as f:
        return f.readline().strip().split(",")


def to_dict(row, hdr):
    parts = row.split(",")
    return dict(zip(hdr, parts))


def fnum(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def parse_poly(row, hdr):
    """Return dict with ua, da (asks), ua_usd, da_usd (depth), tgt, slug, epoch."""
    if not row:
        return None
    d = to_dict(row, hdr)
    return {
        "epoch": int(fnum(d.get("epoch_sec", 0))),
        "slug": d.get("market_slug", ""),
        "tgt": fnum(d.get("target_chainlink_at_open", d.get("target_price", 0))),
        "ua": fnum(d.get("up_ask", 0)),
        "da": fnum(d.get("down_ask", 0)),
        "ua_usd": fnum(d.get("up_usd_best", 0)),
        "da_usd": fnum(d.get("down_usd_best", 0)),
    }


def parse_predict(row, hdr):
    if not row:
        return None
    d = to_dict(row, hdr)
    return {
        "epoch": int(fnum(d.get("epoch_sec", 0))),
        "market_id": d.get("market_id", ""),
        "yes_ask": fnum(d.get("yes_ask", 0)),
        "yes_bid": fnum(d.get("yes_bid", 0)),
        "no_ask_implied": fnum(d.get("no_ask_implied", 0)),
        "yes_ask_usd": fnum(d.get("yes_ask_usd", 0)),
        "no_ask_usd": fnum(d.get("no_ask_usd_buyable", 0)),
    }


def fetch_poly_market(epoch):
    slug = f"btc-updown-15m-{epoch}"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if not data:
        return None
    m = data[0]
    token_ids = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
    outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    return {
        "slug": m["slug"],
        "up_token": token_ids[outcomes.index("Up")],
        "down_token": token_ids[outcomes.index("Down")],
    }


def get_current_predict_market_id():
    with open(PR_MARKETS) as f:
        rows = list(csv.reader(f))
    return int(rows[-1][1])


# ---- main ----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-windows", type=int, default=1, help="run at most N 15min windows")
    ap.add_argument("--max-trades-per-window", type=int, default=5)
    ap.add_argument("--invest", type=float, default=INVEST_PER_SIDE)
    args = ap.parse_args()

    print(f"=== arb_v5_live starting at {now_iso()} ===")
    print(f"invest_per_side=${args.invest}  max_trades_per_window={args.max_trades_per_window}  max_windows={args.max_windows}")

    # Init clients
    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    pt = PredictTrader(api_key, pk, log_path="/root/arb_v5_live_predict.log")

    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
    poly_client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=2,
        funder=POLY_SAFE,
    )
    poly_client.set_api_creds(poly_client.create_or_derive_api_key())
    print("--- both clients connected ---")

    p_hdr = header(P_DATA)
    pr_hdr = header(PR_DATA)

    # Per-window state
    current_epoch = None
    trades_this_window = 0
    windows_done = 0
    last_open_ts = {}
    current_poly_market = None
    current_predict_market = None
    current_predict_meta = None

    while windows_done < args.max_windows:
        try:
            p = parse_poly(tail_last_row(P_DATA), p_hdr)
            pr = parse_predict(tail_last_row(PR_DATA), pr_hdr)

            now_e = int(time.time())
            window_epoch = (now_e // 900) * 900

            # Detect window boundary
            if window_epoch != current_epoch:
                if current_epoch is not None:
                    windows_done += 1
                    print(f"\n>>> WINDOW {current_epoch} CLOSED. trades={trades_this_window}. windows_done={windows_done}/{args.max_windows}")
                    if windows_done >= args.max_windows:
                        break
                current_epoch = window_epoch
                trades_this_window = 0
                current_poly_market = fetch_poly_market(window_epoch)
                current_predict_market = get_current_predict_market_id()
                if current_predict_market:
                    current_predict_meta = pt.get_market(current_predict_market)
                print(f"\n>>> NEW WINDOW {window_epoch} predict_mid={current_predict_market} poly_slug={current_poly_market['slug'] if current_poly_market else 'n/a'}")

            p_age = now_e - p.get("epoch", 0) if p else 999
            pr_age = now_e - pr.get("epoch", 0) if pr else 999
            fresh = p_age <= MAX_FEED_AGE_SEC and pr_age <= MAX_FEED_AGE_SEC

            if not (p and pr and fresh and current_poly_market and current_predict_meta):
                time.sleep(POLL_SEC)
                continue

            if trades_this_window >= args.max_trades_per_window:
                time.sleep(POLL_SEC)
                continue

            # Opportunity check
            cost_a = p['ua'] + pr['no_ask_implied'] if (p['ua'] > 0 and pr['no_ask_implied'] > 0) else 999
            cost_b = p['da'] + pr['yes_ask'] if (p['da'] > 0 and pr['yes_ask'] > 0) else 999

            best = None
            for direction, cost, p_ask, pr_ask, p_token, pr_outcome_name in (
                ("A", cost_a, p['ua'], pr['no_ask_implied'], current_poly_market['up_token'], 'Down'),
                ("B", cost_b, p['da'], pr['yes_ask'], current_poly_market['down_token'], 'Up'),
            ):
                if cost > COST_THRESHOLD:
                    continue
                if p_ask > SINGLE_LEG_MAX_ASK or pr_ask > SINGLE_LEG_MAX_ASK:
                    continue
                if best is None or cost < best[1]:
                    best = (direction, cost, p_ask, pr_ask, p_token, pr_outcome_name)

            if not best:
                time.sleep(POLL_SEC)
                continue

            direction, cost, p_ask, pr_ask, poly_token, predict_outcome_name = best
            key = (direction, current_epoch)
            if time.time() - last_open_ts.get(key, 0) < COOLDOWN_SEC:
                time.sleep(POLL_SEC)
                continue

            # Compute symmetric shares for $invest budget
            max_price = max(p_ask, pr_ask)
            shares = round(args.invest / max_price, 2)
            if shares * max_price < 1.0:  # below typical platform mins
                time.sleep(POLL_SEC)
                continue

            predict_outcome = next(o for o in current_predict_meta["outcomes"]
                                   if o["name"] == predict_outcome_name)

            log_order("OPPORTUNITY", direction=direction, cost=cost,
                      p_ask=p_ask, pr_ask=pr_ask, shares=shares)

            # ---- Place POLY leg ----
            poly_resp = None
            try:
                args_poly = OrderArgsV2(
                    price=round(p_ask, 4),
                    size=round(shares, 4),
                    side="BUY",
                    token_id=str(poly_token),
                )
                poly_resp = poly_client.create_and_post_order(args_poly, order_type=OrderType.GTC)
                log_order("POLY_ORDER", price=p_ask, shares=shares,
                          response=json.dumps(poly_resp, default=str)[:200],
                          orderID=(poly_resp or {}).get("orderID"),
                          status=(poly_resp or {}).get("status"))
            except Exception as e:
                log_order("POLY_ERROR", err=str(e))
                time.sleep(POLL_SEC)
                continue

            if not (poly_resp and poly_resp.get("success")):
                log_order("POLY_FAILED", response=str(poly_resp)[:200])
                time.sleep(POLL_SEC)
                continue

            # ---- Place PREDICT leg ----
            pred_resp = pt.place_limit(
                market_id=current_predict_market,
                outcome_token_id=predict_outcome["onChainId"],
                side="BUY",
                price=pr_ask,
                shares=shares,
                is_neg_risk=current_predict_meta["isNegRisk"],
                is_yield_bearing=current_predict_meta["isYieldBearing"],
                fee_rate_bps=current_predict_meta["feeRateBps"],
            )
            log_order("PREDICT_ORDER", orderId=pred_resp.get("orderId"),
                      orderHash=(pred_resp.get("orderHash") or "")[:14],
                      code=pred_resp.get("code"))

            if not pred_resp.get("orderId"):
                # POLY filled but PREDICT failed — UNHEDGED EXPOSURE. Try to sell poly back.
                log_order("UNHEDGED_WARN", note="poly filled, predict failed; attempting poly close")
                # Best effort — submit a sell at the bid
                try:
                    sell_args = OrderArgsV2(
                        price=round(p_ask - 0.02, 4),  # sell at slightly under
                        size=round(shares, 4),
                        side="SELL",
                        token_id=str(poly_token),
                    )
                    sell_resp = poly_client.create_and_post_order(sell_args, order_type=OrderType.GTC)
                    log_order("POLY_UNWIND", response=json.dumps(sell_resp, default=str)[:200])
                except Exception as e:
                    log_order("POLY_UNWIND_ERROR", err=str(e))

            last_open_ts[key] = time.time()
            trades_this_window += 1

            # Write a trade record
            new = not os.path.exists(LIVE_TRADES)
            with open(LIVE_TRADES, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["trade_id", "open_ts", "direction", "window_epoch",
                                "poly_slug", "predict_market_id",
                                "poly_token", "predict_outcome", "predict_token",
                                "poly_ask", "predict_ask", "cost", "shares",
                                "poly_orderID", "predict_orderId", "predict_orderHash",
                                "poly_filled", "predict_filled"])
                w.writerow([trades_this_window, now_iso(), direction, current_epoch,
                            current_poly_market['slug'], current_predict_market,
                            str(poly_token)[:20], predict_outcome_name, predict_outcome["onChainId"][:20],
                            p_ask, pr_ask, cost, shares,
                            poly_resp.get("orderID"), pred_resp.get("orderId"),
                            (pred_resp.get("orderHash") or "")[:30],
                            poly_resp.get("status"), pred_resp.get("code")])

            print(f"  >>> TRADE #{trades_this_window} opened: dir={direction} shares={shares} cost={cost:.3f}")

        except KeyboardInterrupt:
            print("\nstopped by user")
            break
        except Exception as e:
            log_order("MAIN_LOOP_ERROR", err=f"{type(e).__name__}: {e}")
            time.sleep(POLL_SEC)

        time.sleep(POLL_SEC)

    print(f"\n=== DONE. windows={windows_done}, last_window_trades={trades_this_window} ===")


if __name__ == "__main__":
    main()
