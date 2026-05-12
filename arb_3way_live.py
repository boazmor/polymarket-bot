#!/usr/bin/env python3
"""arb_3way_live.py - 3-platform live arbitrage bot.

Monitors Polymarket + Predict.fun + Limitless 15-min BTC up/down markets.
Detects the cheapest of 4 hedged candidates per cycle (subset of v5_3way until
the Limitless recorder also captures the DOWN orderbook):

    A_POLY        PolyUP   + PredictNO   (cross-oracle, Chainlink vs Binance)
    B_POLY        PolyDOWN + PredictYES  (cross-oracle)
    A_LIM         LimUP    + PredictNO   (cross-oracle, Lim Chainlink vs Binance)
    LimUP_PolyDN  LimUP    + PolyDOWN    (same-oracle, both Chainlink)

Sizing per user spec:
    BASE_NOTIONAL_USD = 1.20 on the smaller-price leg
    shares = round(BASE / min_price, 2)
    if shares * max_price > MAX_SIDE_USD ($7.0) -> SKIP

Execution rule per user spec (12/05):
    if BOTH sides have depth >= 4 * shares*price -> fire FAK on both in parallel
    else -> sequential, thin-depth side first, size the second to the actual fill

Top-up if shortfall after 2 fires:
    try a same-outcome FAK BUY on the 3rd platform to close the gap

Emergency sell only if remaining excess > 10% of the larger side:
    FAK SELL the excess from the over-filled side

Window-block: after any unwind, skip the rest of the current 15-min window.
Stop-on-loss: exit immediately if a window closes with PnL < 0 after at least
one trade.

Usage:
    python3 -u arb_3way_live.py --max-trades-per-window 1 --invest 7.0
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, "/root")
from predict_trader import PredictTrader
from limitless_trader import LimitlessTrader

from dotenv import load_dotenv

ENV_PATH = "/root/live/btc_5m/.env"
load_dotenv(ENV_PATH, override=True)

P_DATA = "/root/data_btc_15m_research/combined_per_second.csv"
PR_LATEST_JSON = "/root/data_predict_btc_15m/latest.json"
PR_MARKETS = "/root/data_predict_btc_15m/markets.csv"
LIM_LATEST_JSON = "/root/data_limitless_btc_15m/latest.json"
LIM_MARKETS = "/root/data_limitless_btc_15m/markets.csv"

LIVE_TRADES = "/root/arb_3way_live_trades.csv"
LIVE_ORDERS = "/root/arb_3way_live_orders.csv"

BASE_NOTIONAL_USD = 1.20
MAX_SIDE_USD = 7.0
COST_THRESHOLD = 0.90
SINGLE_LEG_MAX_ASK = 0.80
MIN_DEPTH_USD = 5.0
PARALLEL_DEPTH_MULTIPLIER = 4.0
EXCESS_SELL_PCT = 0.10
COOLDOWN_SEC = 5
POLL_SEC = 0.2
MAX_FEED_AGE_SEC = 10
LIM_MAX_AGE_SEC = 20
SETTLE_WAIT_SEC = 60

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_BOLD = "\033[1m"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_order(stage, **kw):
    row = {"ts": now_iso(), "stage": stage, **kw}
    new = not os.path.exists(LIVE_ORDERS)
    with open(LIVE_ORDERS, "a", newline="") as f:
        if new:
            f.write("ts,stage," + ",".join(k for k in row if k not in ("ts", "stage")) + "\n")
        f.write(",".join(str(v).replace(",", ";")[:300] for v in row.values()) + "\n")
    line = f"[{row['ts'][11:19]}] {stage}: " + " ".join(
        f"{k}={str(v)[:60]}" for k, v in kw.items() if k != "raw"
    )
    print(line, flush=True)


def tail_last_row(path):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(8192, size)
        f.seek(-chunk, 2)
        block = f.read(chunk).decode("utf-8", errors="replace")
    lines = [ln for ln in block.split("\n") if ln.strip()]
    return lines[-1] if lines else None


def read_header(path):
    with open(path) as f:
        return f.readline().strip().split(",")


def to_dict(row, hdr):
    return dict(zip(hdr, row.split(",")))


def fnum(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def parse_poly(row, hdr):
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


def parse_predict_latest():
    try:
        with open(PR_LATEST_JSON) as f:
            d = json.load(f)
    except Exception:
        return None
    return {
        "epoch": int(d.get("ts_ms", 0) // 1000),
        "market_id": d.get("market_id", ""),
        "yes_ask": float(d.get("yes_ask", 0)),
        "yes_bid": float(d.get("yes_bid", 0)),
        "no_ask_implied": float(d.get("no_ask_implied", 0)),
        "yes_ask_usd": float(d.get("yes_ask_usd", 0)),
        "no_ask_usd": float(d.get("no_ask_usd_buyable", 0)),
    }


def parse_limitless_latest():
    try:
        with open(LIM_LATEST_JSON) as f:
            d = json.load(f)
    except Exception:
        return None
    return {
        "epoch": int(d.get("ts_ms", 0) // 1000),
        "market_id": d.get("market_id", ""),
        "slug": d.get("slug", ""),
        "up_ask": float(d.get("best_ask", 0)),
        "up_bid": float(d.get("best_bid", 0)),
        "up_ask_usd": float(d.get("best_ask_size_usd", 0)),
        "up_bid_usd": float(d.get("best_bid_size_usd", 0)),
    }


def get_current_predict_market_id():
    with open(PR_MARKETS) as f:
        rows = list(csv.reader(f))
    return int(rows[-1][1])


def get_current_limitless_market_slug():
    with open(LIM_MARKETS) as f:
        rows = list(csv.reader(f))
    return rows[-1][2]


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


def snapshot_wealth(pt, poly_client, lt):
    from web3 import Web3
    from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI
    addrs = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
    w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
    usdt = w3.eth.contract(address=Web3.to_checksum_address(addrs.USDT), abi=ERC20_ABI)
    eoa = Web3.to_checksum_address(pt.address)
    usdt_balance = usdt.functions.balanceOf(eoa).call() / 1e18

    positions = pt.get_positions()
    predict_pos_value = sum(float(p.get("valueUsd") or 0) for p in positions)

    poly_usdc = 0
    poly_positions_value = 0
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
        resp = poly_client.get_balance_allowance(params)
        poly_usdc = int(resp["balance"]) / 1e6
    except Exception:
        pass
    try:
        url = f"https://data-api.polymarket.com/positions?user={POLY_SAFE}&limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        for p in (data if isinstance(data, list) else []):
            cv = p.get("currentValue") or p.get("current_value") or 0
            try:
                poly_positions_value += float(cv)
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    lim_usdc = 0
    try:
        from web3 import Web3 as W3b
        rpc = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
        w3b = W3b(W3b.HTTPProvider(rpc))
        USDC = w3b.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
        OWNER = w3b.to_checksum_address(lt.address)
        abi = [{"constant": True, "inputs": [{"name": "a", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view", "type": "function"}]
        c = w3b.eth.contract(address=USDC, abi=abi)
        lim_usdc = c.functions.balanceOf(OWNER).call() / 1e6
    except Exception:
        pass

    total = usdt_balance + predict_pos_value + poly_usdc + poly_positions_value + lim_usdc
    return {
        "usdt": usdt_balance,
        "predict_positions": predict_pos_value,
        "poly_usdc": poly_usdc,
        "poly_positions": poly_positions_value,
        "lim_usdc": lim_usdc,
        "total": total,
    }


def build_candidates(p, pr, lim, poly_market, predict_meta):
    """Return list of viable candidate tuples (4 supported in v1).

    Each tuple: (direction, cost, legs)
      legs = list of dicts: {platform, side, ask, depth_usd, token, outcome_name}
    """
    cands = []
    if p and pr and pr["no_ask_implied"] > 0 and p["ua"] > 0:
        cands.append({
            "direction": "A_POLY",
            "cost": p["ua"] + pr["no_ask_implied"],
            "legs": [
                {"platform": "poly", "side": "BUY", "outcome": "Up",
                 "ask": p["ua"], "depth_usd": p["ua_usd"],
                 "token": poly_market["up_token"] if poly_market else None},
                {"platform": "predict", "side": "BUY", "outcome": "Down",
                 "ask": pr["no_ask_implied"], "depth_usd": pr["no_ask_usd"]},
            ],
        })
    if p and pr and pr["yes_ask"] > 0 and p["da"] > 0:
        cands.append({
            "direction": "B_POLY",
            "cost": p["da"] + pr["yes_ask"],
            "legs": [
                {"platform": "poly", "side": "BUY", "outcome": "Down",
                 "ask": p["da"], "depth_usd": p["da_usd"],
                 "token": poly_market["down_token"] if poly_market else None},
                {"platform": "predict", "side": "BUY", "outcome": "Up",
                 "ask": pr["yes_ask"], "depth_usd": pr["yes_ask_usd"]},
            ],
        })
    if lim and pr and lim["up_ask"] > 0 and pr["no_ask_implied"] > 0:
        cands.append({
            "direction": "A_LIM",
            "cost": lim["up_ask"] + pr["no_ask_implied"],
            "legs": [
                {"platform": "lim", "side": "BUY", "outcome": "yes",
                 "ask": lim["up_ask"], "depth_usd": lim["up_ask_usd"],
                 "slug": lim["slug"]},
                {"platform": "predict", "side": "BUY", "outcome": "Down",
                 "ask": pr["no_ask_implied"], "depth_usd": pr["no_ask_usd"]},
            ],
        })
    if lim and p and lim["up_ask"] > 0 and p["da"] > 0:
        cands.append({
            "direction": "LimUP_PolyDN",
            "cost": lim["up_ask"] + p["da"],
            "legs": [
                {"platform": "lim", "side": "BUY", "outcome": "yes",
                 "ask": lim["up_ask"], "depth_usd": lim["up_ask_usd"],
                 "slug": lim["slug"]},
                {"platform": "poly", "side": "BUY", "outcome": "Down",
                 "ask": p["da"], "depth_usd": p["da_usd"],
                 "token": poly_market["down_token"] if poly_market else None},
            ],
        })
    return cands


def pick_best(cands):
    viable = []
    for c in cands:
        if c["cost"] > COST_THRESHOLD:
            continue
        if any(l["ask"] > SINGLE_LEG_MAX_ASK for l in c["legs"]):
            continue
        if any(l["depth_usd"] < MIN_DEPTH_USD for l in c["legs"]):
            continue
        viable.append(c)
    if not viable:
        return None
    viable.sort(key=lambda c: c["cost"])
    return viable[0]


def size_trade(cand):
    """Apply BASE/CAP sizing rule. Returns (shares, max_p, min_p) or (None, _, _) if skip."""
    asks = [l["ask"] for l in cand["legs"]]
    min_p = min(asks)
    max_p = max(asks)
    if min_p <= 0 or max_p <= 0:
        return None, 0, 0
    shares = round(BASE_NOTIONAL_USD / min_p, 2)
    if shares * max_p > MAX_SIDE_USD:
        return None, max_p, min_p
    return shares, max_p, min_p


def can_fire_parallel(cand, shares):
    """True if BOTH legs have depth >= 4x the shares*price requested."""
    for l in cand["legs"]:
        required_usd = shares * l["ask"]
        if l["depth_usd"] < PARALLEL_DEPTH_MULTIPLIER * required_usd:
            return False
    return True


def place_poly(poly_client, OrderArgsV2, OrderType, leg, price, shares):
    t0 = time.time()
    try:
        args = OrderArgsV2(price=round(price, 4), size=round(shares, 4), side="BUY",
                           token_id=str(leg["token"]))
        resp = poly_client.create_and_post_order(args, order_type=OrderType.FAK)
        filled = resp and resp.get("status") == "matched"
        size_filled = float(resp.get("size_matched") or shares if filled else 0)
        return {"platform": "poly", "ok": filled, "size_filled": size_filled,
                "resp": resp, "ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"platform": "poly", "ok": False, "size_filled": 0,
                "error": f"{type(e).__name__}: {e}", "ms": (time.time() - t0) * 1000}


def place_predict(pt, predict_meta, current_predict_market, leg, price, shares):
    t0 = time.time()
    try:
        outcome_name = leg["outcome"]
        outcome = next(o for o in predict_meta["outcomes"] if o["name"] == outcome_name)
        resp = pt.place_limit(
            market_id=current_predict_market,
            outcome_token_id=outcome["onChainId"],
            side=leg["side"], price=price, shares=shares,
            is_neg_risk=predict_meta["isNegRisk"],
            is_yield_bearing=predict_meta["isYieldBearing"],
            fee_rate_bps=predict_meta["feeRateBps"],
        )
        ok = bool(resp.get("orderId")) and resp.get("code") == "OK"
        return {"platform": "predict", "ok": ok,
                "size_filled": shares if ok else 0,
                "resp": resp, "ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"platform": "predict", "ok": False, "size_filled": 0,
                "error": f"{type(e).__name__}: {e}", "ms": (time.time() - t0) * 1000}


def place_limitless(lt, leg, price, shares):
    t0 = time.time()
    try:
        size_usdc = round(shares * price, 4)
        slug = leg["slug"]
        market = lt.cache_market(slug)
        token_id = market.tokens.yes if leg["outcome"] == "yes" else market.tokens.no
        res = lt.place_fak_buy(market_slug=slug, token_id=str(token_id),
                               price=round(price, 4), size_usdc=size_usdc)
        filled_shares = float(res.get("filled_shares") or 0)
        ok = filled_shares > 0
        return {"platform": "lim", "ok": ok, "size_filled": filled_shares,
                "resp": res, "ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"platform": "lim", "ok": False, "size_filled": 0,
                "error": f"{type(e).__name__}: {e}", "ms": (time.time() - t0) * 1000}


def fire_leg(ctx, leg, shares):
    """Dispatch to platform-specific BUY function."""
    if leg["platform"] == "poly":
        return place_poly(ctx["poly_client"], ctx["OrderArgsV2"], ctx["OrderType"],
                          leg, leg["ask"], shares)
    if leg["platform"] == "predict":
        return place_predict(ctx["pt"], ctx["predict_meta"], ctx["predict_market_id"],
                             leg, leg["ask"], shares)
    if leg["platform"] == "lim":
        return place_limitless(ctx["lt"], leg, leg["ask"], shares)
    raise ValueError(f"unknown platform: {leg['platform']}")


def emergency_sell(ctx, leg, excess_shares):
    """Aggressive FAK sell of excess shares on the over-filled platform."""
    log_order("EMERGENCY_SELL_TRY", platform=leg["platform"], excess=excess_shares)
    try:
        if leg["platform"] == "poly":
            sell_args = ctx["OrderArgsV2"](
                price=round(max(leg["ask"] - 0.10, 0.01), 4),
                size=round(excess_shares, 4),
                side="SELL", token_id=str(leg["token"]),
            )
            resp = ctx["poly_client"].create_and_post_order(sell_args, order_type=ctx["OrderType"].FAK)
            log_order("EMERGENCY_SELL_POLY", status=(resp or {}).get("status"))
        elif leg["platform"] == "predict":
            outcome = next(o for o in ctx["predict_meta"]["outcomes"]
                           if o["name"] == leg["outcome"])
            resp = ctx["pt"].place_limit(
                market_id=ctx["predict_market_id"],
                outcome_token_id=outcome["onChainId"],
                side="SELL", price=max(round(leg["ask"] - 0.05, 4), 0.01),
                shares=round(excess_shares, 4),
                is_neg_risk=ctx["predict_meta"]["isNegRisk"],
                is_yield_bearing=ctx["predict_meta"]["isYieldBearing"],
                fee_rate_bps=ctx["predict_meta"]["feeRateBps"],
            )
            log_order("EMERGENCY_SELL_PREDICT", code=resp.get("code"))
        elif leg["platform"] == "lim":
            slug = leg["slug"]
            market = ctx["lt"].cache_market(slug)
            token_id = market.tokens.yes if leg["outcome"] == "yes" else market.tokens.no
            sell_price = max(round(leg["ask"] - 0.05, 4), 0.01)
            res = ctx["lt"].place_fak_sell(market_slug=slug, token_id=str(token_id),
                                            price=sell_price, size_shares=excess_shares)
            log_order("EMERGENCY_SELL_LIM", filled=res.get("filled_shares"))
    except Exception as e:
        log_order("EMERGENCY_SELL_ERROR", platform=leg["platform"],
                  err=f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-windows", type=int, default=9999)
    ap.add_argument("--max-trades-per-window", type=int, default=1)
    ap.add_argument("--invest", type=float, default=MAX_SIDE_USD)
    ap.add_argument("--stop-on-loss", action="store_true", default=True)
    ap.add_argument("--settle-wait-sec", type=int, default=SETTLE_WAIT_SEC)
    args = ap.parse_args()

    print(f"=== arb_3way_live starting at {now_iso()} ===", flush=True)
    print(f"invest_per_side=${args.invest}  max_trades_per_window={args.max_trades_per_window}", flush=True)

    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    lim_key = os.environ["LIMITLESS_API_KEY"]
    lim_sec = os.environ["LIMITLESS_API_SECRET"]

    pt = PredictTrader(api_key, pk, log_path="/root/arb_3way_live_predict.log")
    lt = LimitlessTrader(lim_key, lim_sec, pk, log_path="/root/arb_3way_live_lim.log")

    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
    poly_client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk, chain_id=137, signature_type=2, funder=POLY_SAFE,
    )
    poly_client.set_api_creds(poly_client.create_or_derive_api_key())
    print("--- 3 clients connected ---", flush=True)

    p_hdr = read_header(P_DATA)

    current_epoch = None
    trades_this_window = 0
    windows_done = 0
    last_open_ts = {}
    poly_market = None
    predict_market_id = None
    predict_meta = None
    lim_slug = None
    window_open_wealth = None
    window_has_unhedged = False
    cumulative_pnl = 0.0

    ctx = {
        "pt": pt, "lt": lt, "poly_client": poly_client,
        "OrderArgsV2": OrderArgsV2, "OrderType": OrderType,
        "predict_meta": None, "predict_market_id": None,
    }

    pool = ThreadPoolExecutor(max_workers=3)

    while windows_done < args.max_windows:
        try:
            p_row = tail_last_row(P_DATA)
            p = parse_poly(p_row, p_hdr)
            pr = parse_predict_latest()
            lim = parse_limitless_latest()

            now_e = int(time.time())
            window_epoch = (now_e // 900) * 900

            if window_epoch != current_epoch:
                if current_epoch is not None and window_open_wealth is not None:
                    print(f"\n>>> WINDOW {current_epoch} CLOSED. trades={trades_this_window}. waiting {args.settle_wait_sec}s...", flush=True)
                    time.sleep(args.settle_wait_sec)
                    close_snap = snapshot_wealth(pt, poly_client, lt)
                    wealth_delta = close_snap["total"] - window_open_wealth["total"]
                    window_pnl = wealth_delta if trades_this_window > 0 else 0.0
                    cumulative_pnl += window_pnl
                    log_order("WINDOW_CLOSE",
                              window=current_epoch, trades=trades_this_window,
                              wealth_before=round(window_open_wealth["total"], 4),
                              wealth_after=round(close_snap["total"], 4),
                              window_pnl=round(window_pnl, 4),
                              cum_pnl=round(cumulative_pnl, 4))
                    windows_done += 1
                    if args.stop_on_loss and window_pnl < 0:
                        print(f"\n!!! STOP: window PnL ${window_pnl:+.4f} < 0. cumulative ${cumulative_pnl:+.4f}", flush=True)
                        break
                    if windows_done >= args.max_windows:
                        break

                current_epoch = window_epoch
                trades_this_window = 0
                window_has_unhedged = False
                try:
                    poly_market = fetch_poly_market(window_epoch)
                except Exception as e:
                    poly_market = None
                    log_order("POLY_MARKET_FETCH_ERR", err=f"{type(e).__name__}: {e}")
                try:
                    predict_market_id = get_current_predict_market_id()
                    predict_meta = pt.get_market(predict_market_id)
                except Exception as e:
                    predict_meta = None
                    log_order("PREDICT_MARKET_ERR", err=f"{type(e).__name__}: {e}")
                try:
                    lim_slug = get_current_limitless_market_slug()
                    lt.cache_market(lim_slug)
                except Exception as e:
                    lim_slug = None
                    log_order("LIM_MARKET_ERR", err=f"{type(e).__name__}: {e}")
                ctx["predict_meta"] = predict_meta
                ctx["predict_market_id"] = predict_market_id
                window_open_wealth = snapshot_wealth(pt, poly_client, lt)
                print(f"\n>>> NEW WINDOW {window_epoch}  poly={poly_market['slug'] if poly_market else 'n/a'}  predict={predict_market_id}  lim={lim_slug}  wealth=${window_open_wealth['total']:.2f}", flush=True)

            p_age = now_e - (p["epoch"] if p else 0)
            pr_age = now_e - (pr["epoch"] if pr else 0)
            lim_age = now_e - (lim["epoch"] if lim else 0)
            fresh_p = p and p_age <= MAX_FEED_AGE_SEC
            fresh_pr = pr and pr_age <= MAX_FEED_AGE_SEC
            fresh_lim = lim and lim_age <= LIM_MAX_AGE_SEC

            if not (fresh_p and fresh_pr and (fresh_lim or True) and poly_market and predict_meta):
                time.sleep(POLL_SEC)
                continue
            if trades_this_window >= args.max_trades_per_window:
                time.sleep(POLL_SEC)
                continue
            if window_has_unhedged:
                time.sleep(POLL_SEC)
                continue

            cands = build_candidates(
                p, pr, lim if fresh_lim else None, poly_market, predict_meta,
            )
            best = pick_best(cands)
            if not best:
                time.sleep(POLL_SEC)
                continue

            key = (best["direction"], current_epoch)
            if time.time() - last_open_ts.get(key, 0) < COOLDOWN_SEC:
                time.sleep(POLL_SEC)
                continue

            shares, max_p, min_p = size_trade(best)
            if shares is None:
                log_order("SKIP_SIZE_OVER_CAP",
                          dir=best["direction"], min_p=min_p, max_p=max_p,
                          cap=MAX_SIDE_USD)
                time.sleep(POLL_SEC)
                continue

            log_order("OPPORTUNITY",
                      dir=best["direction"], cost=round(best["cost"], 4),
                      shares=shares,
                      legs=[(l["platform"], round(l["ask"], 4), round(l["depth_usd"], 2))
                            for l in best["legs"]])

            t_detect = time.time()
            parallel = can_fire_parallel(best, shares)

            if parallel:
                log_order("FIRE_PARALLEL", dir=best["direction"], shares=shares)
                futures = [pool.submit(fire_leg, ctx, leg, shares) for leg in best["legs"]]
                results = [f.result(timeout=30) for f in futures]
            else:
                thin_first = sorted(range(len(best["legs"])),
                                    key=lambda i: best["legs"][i]["depth_usd"])
                log_order("FIRE_SEQUENTIAL", dir=best["direction"], thin_first=thin_first[0],
                          shares=shares)
                first_idx = thin_first[0]
                second_idx = thin_first[1]
                first_res = fire_leg(ctx, best["legs"][first_idx], shares)
                actual = first_res["size_filled"] if first_res["ok"] else 0
                results = [None, None]
                results[first_idx] = first_res
                if actual > 0:
                    second_res = fire_leg(ctx, best["legs"][second_idx], round(actual, 4))
                    results[second_idx] = second_res
                else:
                    results[second_idx] = {"platform": best["legs"][second_idx]["platform"],
                                            "ok": False, "size_filled": 0,
                                            "error": "skipped_first_failed"}

            total_ms = (time.time() - t_detect) * 1000
            sizes = [r["size_filled"] for r in results]
            log_order("FILLS",
                      legs=[r["platform"] for r in results],
                      sizes=[round(s, 4) for s in sizes],
                      total_ms=round(total_ms, 1))

            larger = max(sizes)
            smaller = min(sizes)
            shortfall = larger - smaller
            if larger == 0:
                log_order("BOTH_LEGS_FAILED", dir=best["direction"])
                last_open_ts[key] = time.time()
                trades_this_window += 1
                time.sleep(POLL_SEC)
                continue

            if shortfall > 0:
                # Identify the under-filled leg and try to top up from the 3rd platform
                under_idx = sizes.index(smaller)
                under_leg = best["legs"][under_idx]
                third_platform = None
                for plat in ("lim", "poly", "predict"):
                    if plat == under_leg["platform"]:
                        continue
                    if plat in [l["platform"] for l in best["legs"]]:
                        continue
                    third_platform = plat
                    break
                if third_platform:
                    log_order("TOPUP_TRY", under=under_leg["platform"], shortfall=round(shortfall, 4),
                              third=third_platform)
                    # Build a synthetic leg for the third platform matching the under-filled outcome.
                    # Outcome semantics: we want the same direction-betting outcome as under_leg.
                    # Map to platform-specific identifiers.
                    third_leg = None
                    if third_platform == "lim" and lim and under_leg["outcome"] in ("Up", "yes"):
                        third_leg = {"platform": "lim", "side": "BUY", "outcome": "yes",
                                     "ask": lim["up_ask"], "slug": lim["slug"]}
                    elif third_platform == "poly" and poly_market:
                        token = poly_market["up_token"] if under_leg["outcome"] in ("Up", "yes") else poly_market["down_token"]
                        ask = p["ua"] if under_leg["outcome"] in ("Up", "yes") else p["da"]
                        third_leg = {"platform": "poly", "side": "BUY", "outcome": under_leg["outcome"],
                                     "ask": ask, "token": token}
                    elif third_platform == "predict" and predict_meta:
                        third_leg = {"platform": "predict", "side": "BUY",
                                     "outcome": "Up" if under_leg["outcome"] in ("Up", "yes") else "Down",
                                     "ask": pr["yes_ask"] if under_leg["outcome"] in ("Up", "yes") else pr["no_ask_implied"]}
                    if third_leg and third_leg.get("ask", 0) > 0 and third_leg["ask"] <= SINGLE_LEG_MAX_ASK:
                        topup_res = fire_leg(ctx, third_leg, round(shortfall, 4))
                        log_order("TOPUP_RESULT",
                                  platform=third_leg["platform"],
                                  filled=topup_res.get("size_filled"),
                                  ok=topup_res.get("ok"))
                        if topup_res["ok"]:
                            sizes[under_idx] += topup_res["size_filled"]
                            larger = max(sizes)
                            smaller = min(sizes)
                            shortfall = larger - smaller

            # Emergency-sell only if remaining excess > 10% of larger side
            if larger > 0 and (shortfall / larger) > EXCESS_SELL_PCT:
                over_idx = sizes.index(larger)
                over_leg = best["legs"][over_idx]
                log_order("EXCESS_OVER_10PCT",
                          over=over_leg["platform"], larger=round(larger, 4),
                          smaller=round(smaller, 4), shortfall=round(shortfall, 4))
                emergency_sell(ctx, over_leg, shortfall)
                window_has_unhedged = True
                log_order("WINDOW_BLOCKED", reason="emergency_sell", window=current_epoch)
            elif shortfall > 0:
                log_order("ACCEPT_SMALL_IMBALANCE",
                          larger=round(larger, 4), smaller=round(smaller, 4),
                          shortfall=round(shortfall, 4),
                          pct=round(shortfall/larger*100, 2))

            last_open_ts[key] = time.time()
            trades_this_window += 1

            new = not os.path.exists(LIVE_TRADES)
            with open(LIVE_TRADES, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow([
                        "trade_id", "open_ts", "direction", "window_epoch",
                        "cost", "shares_planned",
                        "leg0_platform", "leg0_size_filled", "leg0_ok",
                        "leg1_platform", "leg1_size_filled", "leg1_ok",
                        "parallel", "shortfall", "excess_action",
                    ])
                excess_action = "emergency_sell" if (larger and shortfall / larger > EXCESS_SELL_PCT) else \
                                ("accepted" if shortfall > 0 else "exact")
                w.writerow([
                    trades_this_window, now_iso(), best["direction"], current_epoch,
                    round(best["cost"], 4), shares,
                    results[0]["platform"], round(results[0]["size_filled"], 4), results[0]["ok"],
                    results[1]["platform"], round(results[1]["size_filled"], 4), results[1]["ok"],
                    parallel, round(shortfall, 4), excess_action,
                ])
            print(f"  >>> TRADE #{trades_this_window} dir={best['direction']} cost={best['cost']:.3f} fills={sizes}", flush=True)

        except KeyboardInterrupt:
            print("\nstopped by user", flush=True)
            break
        except Exception as e:
            log_order("MAIN_LOOP_ERROR", err=f"{type(e).__name__}: {e}")
            time.sleep(POLL_SEC)

        time.sleep(POLL_SEC)

    print(f"\n=== DONE. windows={windows_done} ===", flush=True)


if __name__ == "__main__":
    main()
