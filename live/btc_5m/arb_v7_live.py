#!/usr/bin/env python3
"""arb_v7_live.py — V5 BASIC live trader.

Same opportunity-detection logic as arb_virtual_bot_v5.py, but actually
places orders on both Polymarket and Predict.fun.

Caps:
- MAX_TRADES_PER_WINDOW = 5
MIN_DEPTH_USD = 50.0  # require this much offered depth on EACH side before opening
- INVEST_PER_SIDE = 5.0 USD (small test size)

Output:
- /root/arb_v7_live_trades.csv     — one row per closed trade (settled)
- /root/arb_v7_live_orders.csv     — every order request + response

Usage:
    python3 arb_v7_live.py --max-windows 1
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

P_DATA = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
PR_DATA = "/root/data_predict_btc_5m/combined_per_second.csv"
PR_LATEST_JSON = "/root/data_predict_btc_5m/latest.json"
PR_MARKETS = "/root/data_predict_btc_5m/markets.csv"
LIVE_TRADES = "/root/arb_v7_live_trades.csv"
LIVE_ORDERS = "/root/arb_v7_live_orders.csv"

INVEST_PER_SIDE = 7.0
COST_THRESHOLD = 0.90   # tighter threshold = insurance reserve for unhedged tail risk
SINGLE_LEG_MAX_ASK = 0.80
MAX_TRADES_PER_WINDOW = 5
MIN_DEPTH_USD = 50.0  # require this much offered depth on EACH side before opening
COOLDOWN_SEC = 5
POLL_SEC = 0.1
MAX_FEED_AGE_SEC = 10

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

# ---- helpers ----

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def log_order(stage, **kw):
    """Append one row per order action to arb_v7_live_orders.csv."""
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


def parse_predict_latest_json(path):
    """Read latest.json written by Predict recorder on every WS event (~100ms cadence)."""
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return None
    return {
        "epoch": int(d.get("ts_ms", 0) // 1000),
        "ts_ms": d.get("ts_ms", 0),
        "market_id": d.get("market_id", ""),
        "yes_ask": float(d.get("yes_ask", 0)),
        "yes_bid": float(d.get("yes_bid", 0)),
        "no_ask_implied": float(d.get("no_ask_implied", 0)),
        "yes_ask_usd": float(d.get("yes_ask_usd", 0)),
        "no_ask_usd": float(d.get("no_ask_usd_buyable", 0)),
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
    slug = f"btc-updown-5m-{epoch}"
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

def snapshot_wealth(pt, poly_client):
    """Return total wealth: USDT on BNB + Predict positions value + Polymarket USDC + Polymarket positions value."""
    from web3 import Web3
    from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI
    addrs = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
    w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
    usdt = w3.eth.contract(address=Web3.to_checksum_address(addrs.USDT), abi=ERC20_ABI)
    eoa = Web3.to_checksum_address(pt.address)
    usdt_balance = usdt.functions.balanceOf(eoa).call() / 1e18

    # Predict positions
    positions = pt.get_positions()
    predict_pos_value = sum(float(p.get("valueUsd") or 0) for p in positions)

    # Polymarket USDC + open positions
    poly_usdc = 0
    poly_positions_value = 0
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
        resp = poly_client.get_balance_allowance(params)
        poly_usdc = int(resp["balance"]) / 1e6
    except Exception:
        pass
    # Polymarket position value via /data/positions API (proxy address is the Safe)
    try:
        import urllib.request, json as _json
        url = "https://data-api.polymarket.com/positions?user=0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B&limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        # Sum currentValue across open positions
        for p in (data if isinstance(data, list) else []):
            cv = p.get("currentValue") or p.get("current_value") or 0
            try:
                poly_positions_value += float(cv)
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    total = usdt_balance + predict_pos_value + poly_usdc + poly_positions_value
    return {
        "usdt": usdt_balance,
        "predict_positions": predict_pos_value,
        "poly_usdc": poly_usdc,
        "poly_positions": poly_positions_value,
        "total": total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-windows", type=int, default=9999,
                    help="hard cap on windows (default unlimited)")
    ap.add_argument("--max-trades-per-window", type=int, default=5)
    ap.add_argument("--invest", type=float, default=INVEST_PER_SIDE)
    ap.add_argument("--stop-on-loss", action="store_true", default=True,
                    help="exit if a window closes with negative PnL")
    ap.add_argument("--settle-wait-sec", type=int, default=60,
                    help="seconds to wait after window close for settlements")
    args = ap.parse_args()

    print(f"=== arb_v5_live starting at {now_iso()} ===")
    print(f"invest_per_side=${args.invest}  max_trades_per_window={args.max_trades_per_window}  max_windows={args.max_windows}")

    # Init clients
    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    pt = PredictTrader(api_key, pk, log_path="/root/arb_v7_live_predict.log")

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
    window_open_wealth = None  # wealth snapshot at start of current window
    window_has_unhedged = False
    cumulative_pnl = 0.0       # cumulative across all completed windows

    while windows_done < args.max_windows:
        try:
            p = parse_poly(tail_last_row(P_DATA), p_hdr)
            # Use latest.json (updated by Predict recorder on every WS event, ~100ms cadence)
            pr = parse_predict_latest_json(PR_LATEST_JSON)
            if pr is None:
                pr = parse_predict(tail_last_row(PR_DATA), pr_hdr)

            now_e = int(time.time())
            window_epoch = (now_e // 300) * 300

            # Detect window boundary
            if window_epoch != current_epoch:
                # Evaluate prior window's PnL (if any) before moving on
                if current_epoch is not None and window_open_wealth is not None:
                    print(f"\n>>> WINDOW {current_epoch} CLOSED. trades={trades_this_window}. waiting {args.settle_wait_sec}s for settlements...")
                    time.sleep(args.settle_wait_sec)
                    close_snap = snapshot_wealth(pt, poly_client)
                    wealth_delta = close_snap["total"] - window_open_wealth["total"]
                    window_pnl = wealth_delta if trades_this_window > 0 else 0.0
                    cumulative_pnl += window_pnl
                    log_order("WINDOW_CLOSE",
                              window=current_epoch,
                              trades=trades_this_window,
                              wealth_before=round(window_open_wealth["total"], 4),
                              wealth_after=round(close_snap["total"], 4),
                              window_pnl=round(window_pnl, 4),
                              cum_pnl=round(cumulative_pnl, 4))
                    windows_done += 1
                    if args.stop_on_loss and window_pnl < 0:
                        print(f"\n!!! STOP: window PnL ${window_pnl:+.4f} < 0. Exiting after {windows_done} windows. cumulative ${cumulative_pnl:+.4f}")
                        break
                    if windows_done >= args.max_windows:
                        break

                current_epoch = window_epoch
                trades_this_window = 0
                window_has_unhedged = False
                current_poly_market = fetch_poly_market(window_epoch)
                current_predict_market = get_current_predict_market_id()
                if current_predict_market:
                    current_predict_meta = pt.get_market(current_predict_market)
                window_open_wealth = snapshot_wealth(pt, poly_client)
                print(f"\n>>> NEW WINDOW {window_epoch} predict_mid={current_predict_market} "
                      f"poly_slug={current_poly_market['slug'] if current_poly_market else 'n/a'}  "
                      f"wealth=${window_open_wealth['total']:.2f}")

            p_age = now_e - p.get("epoch", 0) if p else 999
            pr_age = now_e - pr.get("epoch", 0) if pr else 999
            fresh = p_age <= MAX_FEED_AGE_SEC and pr_age <= MAX_FEED_AGE_SEC

            if not (p and pr and fresh and current_poly_market and current_predict_meta):
                time.sleep(POLL_SEC)
                continue

            if trades_this_window >= args.max_trades_per_window:
                time.sleep(POLL_SEC)
                continue
            if 'window_has_unhedged' in dir() and window_has_unhedged:
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

            if poly_depth_usd < MIN_DEPTH_USD or pred_depth_usd < MIN_DEPTH_USD:
                log_order("SKIP_LOW_DEPTH", poly_depth=poly_depth_usd, pred_depth=pred_depth_usd, min=MIN_DEPTH_USD)
                time.sleep(POLL_SEC)
                continue
            # New sizing rule: base = $1.20 on smaller side. Cap = $7 on bigger side.
            _min_p = min(p_ask, pr_ask)
            _max_p = max(p_ask, pr_ask)
            if _min_p <= 0 or _max_p <= 0:
                time.sleep(POLL_SEC)
                continue
            BASE_NOTIONAL_USD = 1.20
            MAX_SIDE_USD = 7.0
            _shares_planned = round(BASE_NOTIONAL_USD / _min_p, 2)
            _max_side_cost = _shares_planned * _max_p
            if _max_side_cost > MAX_SIDE_USD:
                log_order("SKIP_MAX_SIDE_OVER_CAP",
                          min_ask=_min_p, max_ask=_max_p,
                          shares=_shares_planned, max_side_cost=round(_max_side_cost, 4),
                          cap=MAX_SIDE_USD)
                time.sleep(POLL_SEC)
                continue
            # Use the shares from the new base/cap sizing logic computed above
            max_price = max(p_ask, pr_ask)
            shares = _shares_planned

            predict_outcome = next(o for o in current_predict_meta["outcomes"]
                                   if o["name"] == predict_outcome_name)

            t_detect = time.time()
            log_order("OPPORTUNITY", direction=direction, cost=cost,
                      p_ask=p_ask, pr_ask=pr_ask, shares=shares)

            # ---- Sequential submission, SMALLER-ASK side FIRST ----
            def do_poly():
                t0 = time.time()
                try:
                    args_poly = OrderArgsV2(
                        price=round(p_ask, 4),
                        size=round(shares, 4),
                        side="BUY",
                        token_id=str(poly_token),
                    )
                    resp = poly_client.create_and_post_order(args_poly, order_type=OrderType.GTC)
                    return resp, None, (time.time() - t0) * 1000
                except Exception as e:
                    return None, f"{type(e).__name__}: {e}", (time.time() - t0) * 1000

            def do_pred():
                t0 = time.time()
                try:
                    resp = pt.place_limit(
                        market_id=current_predict_market,
                        outcome_token_id=predict_outcome["onChainId"],
                        side="BUY",
                        price=pr_ask,
                        shares=shares,
                        is_neg_risk=current_predict_meta["isNegRisk"],
                        is_yield_bearing=current_predict_meta["isYieldBearing"],
                        fee_rate_bps=current_predict_meta["feeRateBps"],
                    )
                    return resp, None, (time.time() - t0) * 1000
                except Exception as e:
                    return None, f"{type(e).__name__}: {e}", (time.time() - t0) * 1000

            # Require min depth on both sides
            poly_depth = best[4] if best else 0  # ua_usd or da_usd from candidate tuple
            pred_depth = best[5] if best else 0  # no_ask_usd or yes_ask_usd
            # Recompute: best tuple is (direction, cost, p_ask, pr_ask, p_token, pr_outcome_name)
            # so depth is not in best — read from parsed data
            if direction == "A":
                poly_depth_usd = p["ua_usd"]
                pred_depth_usd = pr["no_ask_usd"]
            else:
                poly_depth_usd = p["da_usd"]
                pred_depth_usd = pr["yes_ask_usd"]
            poly_first = p_ask <= pr_ask
            if poly_first:
                poly_resp, poly_err, poly_ms = do_poly()
                pred_resp, pred_err, pred_ms = do_pred()
            else:
                pred_resp, pred_err, pred_ms = do_pred()
                poly_resp, poly_err, poly_ms = do_poly()

            pred_resp = pred_resp or {}
            total_ms = (time.time() - t_detect) * 1000
            log_order("SUBMIT_LATENCY",
                      first_side=("poly" if poly_first else "predict"),
                      poly_ms=round(poly_ms, 1),
                      pred_ms=round(pred_ms, 1),
                      total_ms=round(total_ms, 1))

            poly_filled = (poly_resp and poly_resp.get("status") == "matched")
            poly_live = (poly_resp and poly_resp.get("status") == "live")
            poly_orderID = (poly_resp or {}).get("orderID", "")
            pred_orderId = pred_resp.get("orderId")

            log_order("POLY_ORDER", err=poly_err,
                      orderID=poly_orderID,
                      status=(poly_resp or {}).get("status"),
                      filled=poly_filled)
            log_order("PREDICT_ORDER", err=pred_err,
                      orderId=pred_orderId,
                      code=pred_resp.get("code"))

            poly_ok = poly_filled  # only consider poly "ok" if actually FILLED, not just "live"
            pred_ok = bool(pred_orderId)

            # ---- Unwind logic: cancel/sell-market the leg that filled when the other failed ----
            if poly_ok and not pred_ok:
                log_order("UNHEDGED_WARN_POLY_FILLED",
                          note="poly filled but predict failed — selling poly at market")
                try:
                    # Use FAK at a price aggressive enough to cross the spread
                    sell_args = OrderArgsV2(
                        price=round(max(p_ask - 0.10, 0.01), 4),
                        size=round(shares, 4),
                        side="SELL",
                        token_id=str(poly_token),
                    )
                    sell_resp = poly_client.create_and_post_order(sell_args, order_type=OrderType.FAK)
                    log_order("POLY_UNWIND_SELL",
                              response=json.dumps(sell_resp, default=str)[:200],
                              status=(sell_resp or {}).get("status"))
                except Exception as e:
                    log_order("POLY_UNWIND_ERROR", err=f"{type(e).__name__}: {e}")
                window_has_unhedged = True
                log_order("WINDOW_BLOCKED", reason="poly_unwind", window=current_epoch)
            elif poly_live and not pred_ok:
                # Poly resting, predict failed — cancel the resting poly order
                log_order("CANCEL_POLY_RESTING",
                          note="poly live (resting), predict failed — canceling poly order")
                try:
                    cancel_resp = poly_client.cancel_orders([str(poly_orderID)])
                    log_order("POLY_CANCEL", response=json.dumps(cancel_resp, default=str)[:200])
                except Exception as e:
                    log_order("POLY_CANCEL_ERROR", err=f"{type(e).__name__}: {e}")
                # poly was just live, cancel succeeded — no exposure, no need to block
            elif not poly_ok and pred_ok:
                cap_price = round(0.90 - pr_ask, 4)
                log_order("UNHEDGED_PRED_FILLED_RETRY", cap_price=cap_price, pr_ask=pr_ask, shares=shares)
                retry_filled = False
                if cap_price > 0.10:
                    try:
                        retry_args = OrderArgsV2(price=cap_price, size=round(shares, 4), side="BUY", token_id=str(poly_token))
                        retry_resp = poly_client.create_and_post_order(retry_args, order_type=OrderType.GTC)
                        retry_filled = retry_resp and retry_resp.get("status") == "matched"
                        log_order("POLY_RETRY", status=(retry_resp or {}).get("status"), filled=retry_filled)
                    except Exception as e:
                        log_order("POLY_RETRY_ERROR", err=f"{type(e).__name__}: {e}")
                if not retry_filled:
                    # CRITICAL: cancel the retry order if it's still live, before selling predict
                    retry_orderID = (retry_resp or {}).get("orderID") if 'retry_resp' in dir() else None
                    if retry_orderID:
                        try:
                            cancel_resp = poly_client.cancel_orders([str(retry_orderID)])
                            log_order("RETRY_CANCEL", response=json.dumps(cancel_resp, default=str)[:200])
                        except Exception as e:
                            log_order("RETRY_CANCEL_ERROR", err=f"{type(e).__name__}: {e}")
                    log_order("PREDICT_UNWIND_SELL", note="retry failed, selling predict back")
                    try:
                        sell_price = max(round(pr_ask - 0.05, 4), 0.01)
                        sell_resp = pt.place_limit(market_id=current_predict_market, outcome_token_id=predict_outcome["onChainId"], side="SELL", price=sell_price, shares=shares, is_neg_risk=current_predict_meta["isNegRisk"], is_yield_bearing=current_predict_meta["isYieldBearing"], fee_rate_bps=current_predict_meta["feeRateBps"])
                        log_order("PREDICT_SELL_RESULT", code=sell_resp.get("code"), orderId=sell_resp.get("orderId"))
                    except Exception as e:
                        log_order("PREDICT_SELL_ERROR", err=f"{type(e).__name__}: {e}")

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
