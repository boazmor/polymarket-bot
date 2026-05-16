#!/usr/bin/env python3
"""arb_v9_live.py — Dip-buy strategy on 5-min BTC markets, LIVE.

Strategy (current default):
  Watch the FIRST 30 seconds of every 5-min window across all 3 tradable
  platforms (Polymarket / Predict.fun / Limitless).
  If a side's best_ask drops to <= MAX_PRICE (0.30) with depth >= MIN_DEPTH,
  BUY that side on the platform offering it.
  Trade size: $2 per trade for cautious live start.

  HOLD TO EXPIRY. No mid-window selling. Position resolves to $1 (win) or
  $0 (lose) at the window's settle time.

  After each buy, log the prevailing best_bid every BID_LOG_SEC seconds so
  later analysis can compute what selling at +40% / +50% would have done.
"""

import argparse
import asyncio
import csv
import json
import os
import sys
import time
import urllib.request
import urllib3
from datetime import datetime, timezone

HTTP_POOL = urllib3.PoolManager(
    maxsize=10, block=False,
    retries=urllib3.Retry(connect=1, read=0, backoff_factor=0.05),
    headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
)

sys.path.insert(0, "/root")
from predict_trader import PredictTrader
from limitless_trader import LimitlessTrader
from ws_feeds.state import STATE
from ws_feeds.runner import start_all_feeds

from dotenv import load_dotenv
load_dotenv("/root/live/btc_5m/.env", override=True)

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

# Strategy defaults (overridable via CLI args)
DEFAULT_MAX_PRICE = 0.30
DEFAULT_TIME_MAX_SEC = 30
DEFAULT_INVEST_USD = 2.0
WINDOW_SEC = 300
MIN_DEPTH = 20.0        # $20 sweet spot per V9 deep analysis
CONSENSUS_THRESH = 0.50 # second platform's same-side ask must be <= this
MIN_PLATFORMS_AGREE = 2 # require at least 2 platforms with cheap same-side
BID_LOG_SEC = 5
COOLDOWN_SEC = 5


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_log_fn(orders_path):
    def log_order(stage, **kw):
        row = {"ts": now_iso(), "stage": stage, **kw}
        new = not os.path.exists(orders_path)
        with open(orders_path, "a", newline="") as f:
            if new:
                f.write("ts,stage," + ",".join(k for k in row if k not in ("ts", "stage")) + "\n")
            f.write(",".join(str(v).replace(",", ";")[:300] for v in row.values()) + "\n")
        line = f"[{row['ts'][11:19]}] {stage}: " + " ".join(
            f"{k}={str(v)[:60]}" for k, v in kw.items()
        )
        print(line, flush=True)
    return log_order


def read_state_snapshot():
    poly = STATE.get("poly")
    pr = STATE.get("predict")
    lim = STATE.get("lim")
    return {
        "poly": {"connected": poly.connected,
                 "yes_ask": poly.best_ask, "yes_bid": poly.best_bid,
                 "yes_ask_usd": poly.ask_depth_usd, "yes_bid_usd": poly.bid_depth_usd,
                 "no_ask":  poly.no_best_ask, "no_bid": round(1.0 - poly.best_ask, 4) if poly.best_ask > 0 else 0,
                 "no_ask_usd": poly.no_ask_depth_usd},
        "predict": {"connected": pr.connected,
                    "yes_ask": pr.best_ask, "yes_bid": pr.best_bid,
                    "yes_ask_usd": pr.ask_depth_usd, "yes_bid_usd": pr.bid_depth_usd,
                    "no_ask": pr.no_best_ask, "no_bid": round(1.0 - pr.best_ask, 4) if pr.best_ask > 0 else 0,
                    "no_ask_usd": pr.no_ask_depth_usd},
        "lim": {"connected": lim.connected,
                "yes_ask": lim.best_ask, "yes_bid": lim.best_bid,
                "yes_ask_usd": lim.ask_depth_usd, "yes_bid_usd": lim.bid_depth_usd,
                "no_ask": lim.no_best_ask, "no_bid": round(1.0 - lim.best_ask, 4) if lim.best_ask > 0 else 0,
                "no_ask_usd": lim.no_ask_depth_usd},
    }


def find_dip(snap, max_price):
    """Find a buyable dip: a platform with ask in [0.02, max_price] AND
    depth >= MIN_DEPTH AND at least one OTHER platform showing the same
    side as cheap (ask <= CONSENSUS_THRESH). Returns the platform with
    the lowest such ask.
    Returns (plat, side, ask_price, ask_depth_usd, agreeing_count) or None."""
    best = None
    for plat, s in snap.items():
        if not s["connected"]:
            continue
        for side in ("yes", "no"):
            a = s[f"{side}_ask"]
            d = s[f"{side}_ask_usd"]
            if a is None or a <= 0.02 or a > max_price:
                continue
            if d < MIN_DEPTH:
                continue
            # consensus: count platforms (including this one) with same
            # side ask <= CONSENSUS_THRESH
            count = 0
            for plat2, s2 in snap.items():
                if not s2["connected"]:
                    continue
                a2 = s2[f"{side}_ask"]
                if a2 is not None and 0.01 < a2 <= CONSENSUS_THRESH:
                    count += 1
            if count < MIN_PLATFORMS_AGREE:
                continue
            if best is None or a < best[2]:
                best = (plat, side, a, d, count)
    return best


def fetch_poly_market_5m(epoch):
    slug = f"btc-updown-5m-{epoch}"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    try:
        r = HTTP_POOL.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=7.0))
        if r.status != 200:
            return None
        data = json.loads(r.data)
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
    except Exception:
        return None


def buy_leg(ctx, plat, side, invest_usd, log_order):
    """BUY side on plat for invest_usd at market FAK."""
    if ctx["dry_run"]:
        log_order("DRY_RUN_BUY", plat=plat, side=side, invest=invest_usd)
        # Simulate fill at current ask
        snap = read_state_snapshot()
        return {"ok": True, "size_filled": invest_usd / max(snap[plat][f"{side}_ask"], 0.01),
                "price": snap[plat][f"{side}_ask"], "dry_run": True}
    if plat == "poly":
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
        token = ctx["poly_market"]["up_token"] if side == "yes" else ctx["poly_market"]["down_token"]
        ask = STATE.get("poly").best_ask if side == "yes" else STATE.get("poly").no_best_ask
        price = round(min(ask + 0.005, 0.99), 4)
        shares = round(invest_usd / price, 4)
        try:
            args = OrderArgsV2(price=price, size=shares, side="BUY", token_id=str(token))
            resp = ctx["poly_client"].create_and_post_order(args, order_type=OrderType.FAK)
            matched = float(resp.get("size_matched") or 0)
            return {"ok": matched > 0, "size_filled": matched, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "predict":
        outcome_name = "Up" if side == "yes" else "Down"
        outcome = next(o for o in ctx["predict_meta"]["outcomes"] if o["name"] == outcome_name)
        ask = STATE.get("predict").best_ask if side == "yes" else STATE.get("predict").no_best_ask
        price = round(min(ask + 0.005, 0.99), 4)
        shares = round(invest_usd / price, 4)
        try:
            resp = ctx["pt"].place_limit(
                market_id=ctx["predict_market_id"],
                outcome_token_id=outcome["onChainId"],
                side="BUY", price=price, shares=shares,
                is_neg_risk=ctx["predict_meta"]["isNegRisk"],
                is_yield_bearing=ctx["predict_meta"]["isYieldBearing"],
                fee_rate_bps=ctx["predict_meta"]["feeRateBps"],
            )
            accepted = resp.get("code") == "OK"
            raw = resp.get("raw") or resp
            filled = float(raw.get("filledSize") or raw.get("size_matched") or 0) if accepted else 0
            return {"ok": accepted and filled > 0, "size_filled": filled, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "lim":
        slug = STATE.get("lim").slug
        market = ctx["lt"].cache_market(slug)
        token = market.tokens.yes if side == "yes" else market.tokens.no
        ask = STATE.get("lim").best_ask if side == "yes" else STATE.get("lim").no_best_ask
        price = round(min(ask + 0.005, 0.99), 4)
        try:
            res = ctx["lt"].place_fak_buy(market_slug=slug, token_id=str(token),
                                           price=price, size_usdc=round(invest_usd, 4))
            filled = float(res.get("filled_shares") or 0)
            return {"ok": filled > 0, "size_filled": filled, "price": price, "resp": res}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "size_filled": 0, "error": f"unknown_plat_{plat}"}


def sell_leg(ctx, plat, side, shares, log_order, target_price):
    """Sell shares of side on plat at target_price (or best bid)."""
    if ctx["dry_run"]:
        log_order("DRY_RUN_SELL", plat=plat, side=side, shares=shares, target_price=target_price)
        snap = read_state_snapshot()
        bid = snap[plat][f"{side}_bid"]
        return {"ok": True, "size_filled": shares, "price": bid, "dry_run": True}
    if plat == "poly":
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
        token = ctx["poly_market"]["up_token"] if side == "yes" else ctx["poly_market"]["down_token"]
        price = round(max(target_price - 0.005, 0.01), 4)
        try:
            args = OrderArgsV2(price=price, size=round(shares, 4), side="SELL", token_id=str(token))
            resp = ctx["poly_client"].create_and_post_order(args, order_type=OrderType.FAK)
            matched = float(resp.get("size_matched") or 0)
            return {"ok": matched > 0, "size_filled": matched, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "predict":
        outcome_name = "Up" if side == "yes" else "Down"
        outcome = next(o for o in ctx["predict_meta"]["outcomes"] if o["name"] == outcome_name)
        price = round(max(target_price - 0.005, 0.01), 4)
        try:
            resp = ctx["pt"].place_limit(
                market_id=ctx["predict_market_id"],
                outcome_token_id=outcome["onChainId"],
                side="SELL", price=price, shares=round(shares, 4),
                is_neg_risk=ctx["predict_meta"]["isNegRisk"],
                is_yield_bearing=ctx["predict_meta"]["isYieldBearing"],
                fee_rate_bps=ctx["predict_meta"]["feeRateBps"],
            )
            accepted = resp.get("code") == "OK"
            raw = resp.get("raw") or resp
            filled = float(raw.get("filledSize") or raw.get("size_matched") or 0) if accepted else 0
            return {"ok": accepted and filled > 0, "size_filled": filled, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "lim":
        slug = STATE.get("lim").slug
        market = ctx["lt"].cache_market(slug)
        token = market.tokens.yes if side == "yes" else market.tokens.no
        price = round(max(target_price - 0.005, 0.01), 4)
        try:
            res = ctx["lt"].place_fak_sell(market_slug=slug, token_id=str(token),
                                            price=price, size_shares=round(shares, 4))
            filled = float(res.get("filled_shares") or 0)
            return {"ok": filled > 0, "size_filled": filled, "price": price, "resp": res}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "size_filled": 0, "error": f"unknown_plat_{plat}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--invest", type=float, default=DEFAULT_INVEST_USD)
    ap.add_argument("--max-price", type=float, default=DEFAULT_MAX_PRICE)
    ap.add_argument("--time-max", type=int, default=DEFAULT_TIME_MAX_SEC)
    ap.add_argument("--dry-run", action="store_true", default=False)
    args = ap.parse_args()

    tag = f"5m_${int(args.invest)}_p{int(args.max_price*100)}_t{args.time_max}"
    orders_path = f"/root/arb_v9_live_{tag}_orders.csv"
    bid_log_path = f"/root/arb_v9_live_{tag}_bidlog.csv"
    log_order = make_log_fn(orders_path)

    print(f"=== arb_v9_live starting at {now_iso()} ===", flush=True)
    print(f"invest=${args.invest}  dry_run={args.dry_run}  "
          f"max_price<={args.max_price}  time_max={args.time_max}s  "
          f"strategy=HOLD_TO_EXPIRY", flush=True)

    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    lim_key = os.environ["LIMITLESS_API_KEY"]
    lim_sec = os.environ["LIMITLESS_API_SECRET"]
    pt = PredictTrader(api_key, pk, log_path=f"/root/arb_v9_dip_{tag}_predict.log")
    lt = LimitlessTrader(lim_key, lim_sec, pk, log_path=f"/root/arb_v9_dip_{tag}_lim.log")
    from py_clob_client_v2.client import ClobClient
    poly_client = ClobClient(host="https://clob.polymarket.com", key=pk,
                              chain_id=137, signature_type=2, funder=POLY_SAFE)
    try:
        poly_client.set_api_creds(poly_client.create_or_derive_api_key())
    except Exception as e:
        print(f"poly api_key warning: {e}", flush=True)
    print("--- 3 clients connected ---", flush=True)

    ctx = {
        "pt": pt, "lt": lt, "poly_client": poly_client,
        "predict_meta": None, "predict_market_id": None,
        "poly_market": None, "dry_run": args.dry_run,
    }

    market_holders = {
        "poly_up_tokens": [], "poly_down_tokens": [],
        "predict_market_id": None, "lim_slug": None,
    }
    feeds = start_all_feeds(
        poly_tokens_provider=lambda: (market_holders["poly_up_tokens"], market_holders["poly_down_tokens"]),
        predict_market_id_provider=lambda: market_holders["predict_market_id"],
        limitless_slug_provider=lambda: market_holders["lim_slug"],
        state=STATE,
    )
    print(f"--- {len(feeds)} WS feeds started ---", flush=True)

    current_epoch = None
    position = None  # dict if held, None otherwise
    POLL = 0.05
    consec_losses = 0
    pending_outcome = None  # set when position closes at window end

    while True:
        try:
            now_e = int(time.time())
            window_epoch = (now_e // WINDOW_SEC) * WINDOW_SEC
            sec_in_window = now_e - window_epoch

            if window_epoch != current_epoch:
                # Settle previous window's position. Wait 60s for the market
                # to resolve, then look at our side's bid: ~1 means won, ~0
                # means lost.
                if position:
                    log_order("WINDOW_END_HOLD",
                              plat=position["plat"], side=position["side"],
                              shares=round(position["shares"], 4),
                              buy_price=round(position["buy_price"], 4),
                              held_for_sec=now_e - position["buy_ts"])
                    time.sleep(60)
                    snap = read_state_snapshot()
                    bid_after = snap[position["plat"]][f"{position['side']}_bid"]
                    won = bid_after >= 0.95
                    lost = bid_after <= 0.05
                    if won:
                        log_order("OUTCOME_WIN", plat=position["plat"],
                                  side=position["side"], bid=bid_after,
                                  shares=position["shares"], buy_price=position["buy_price"])
                        consec_losses = 0
                    elif lost:
                        log_order("OUTCOME_LOSS", plat=position["plat"],
                                  side=position["side"], bid=bid_after,
                                  shares=position["shares"], buy_price=position["buy_price"])
                        consec_losses += 1
                    else:
                        log_order("OUTCOME_AMBIGUOUS", plat=position["plat"],
                                  side=position["side"], bid=bid_after,
                                  note="bid not at 0 or 1 - cannot determine")
                    position = None
                    if consec_losses >= 2:
                        stop_path = f"/root/arb_v9_live.stopped"
                        with open(stop_path, "w") as sf:
                            sf.write(f"{now_iso()} stopped after {consec_losses} consecutive losses\n")
                        log_order("STOP_FILE_WRITTEN", path=stop_path,
                                  reason=f"{consec_losses}_consecutive_losses")
                        print(f"\n!!! STOPPED: {consec_losses} consecutive losses. delete {stop_path} to resume\n", flush=True)
                        return
                current_epoch = window_epoch
                pm = fetch_poly_market_5m(window_epoch)
                ctx["poly_market"] = pm
                if pm:
                    market_holders["poly_up_tokens"] = [pm["up_token"]]
                    market_holders["poly_down_tokens"] = [pm["down_token"]]
                try:
                    with open("/root/data_predict_btc_5m/markets.csv") as f:
                        rows = list(csv.reader(f))
                    pid = int(rows[-1][1])
                    market_holders["predict_market_id"] = pid
                    ctx["predict_market_id"] = pid
                    ctx["predict_meta"] = pt.get_market(pid)
                except Exception as e:
                    log_order("PRECACHE_PREDICT_FAIL", err=f"{type(e).__name__}: {e}")
                # Limitless 5m — query /markets/active directly so we always
                # pick the currently-active market, not whatever the recorder
                # happened to log last (which lags 10-40s and may be expired).
                try:
                    import urllib.request as _u, json as _j
                    _req = _u.Request("https://api.limitless.exchange/markets/active",
                                      headers={"User-Agent": "arb-bot/1.0"})
                    with _u.urlopen(_req, timeout=4) as _r:
                        _data = _j.loads(_r.read())
                    _now_ms = int(time.time() * 1000)
                    _matches = [m for m in _data.get("data", [])
                                if m.get("title") == "BTC Up or Down - 5 Min"
                                and (m.get("expirationTimestamp") or 0) > _now_ms]
                    if not _matches:
                        raise ValueError("no active 5-min BTC market")
                    _matches.sort(key=lambda m: m["expirationTimestamp"])
                    slug = _matches[0]["slug"]
                    market_holders["lim_slug"] = slug
                    lt.cache_market(slug)
                except Exception as e:
                    market_holders["lim_slug"] = None
                    log_order("PRECACHE_LIM_FAIL", err=f"{type(e).__name__}: {e}")
                log_order("NEW_WINDOW", window=window_epoch,
                          poly=pm["slug"] if pm else "n/a",
                          predict=market_holders["predict_market_id"],
                          lim=market_holders["lim_slug"])

            # If position held, JUST LOG the bid timeline. No auto-sell.
            if position:
                snap = read_state_snapshot()
                bid = snap[position["plat"]][f"{position['side']}_bid"]
                # Append to bid log every BID_LOG_SEC
                if now_e - position.get("last_bid_log", 0) >= BID_LOG_SEC:
                    profit_pct = (bid - position["buy_price"]) / position["buy_price"] if position["buy_price"] > 0 and bid > 0 else 0
                    new = not os.path.exists(bid_log_path)
                    with open(bid_log_path, "a", newline="") as f:
                        if new:
                            f.write("ts,plat,side,buy_price,buy_ts,sec_since_buy,cur_bid,profit_pct\n")
                        f.write(f"{now_iso()},{position['plat']},{position['side']},"
                                f"{position['buy_price']:.4f},{position['buy_ts']},"
                                f"{now_e - position['buy_ts']},{bid:.4f},{profit_pct*100:.1f}\n")
                    position["last_bid_log"] = now_e
                time.sleep(POLL)
                continue

            # No position. Only buy in first args.time_max seconds.
            if sec_in_window > args.time_max:
                time.sleep(POLL)
                continue

            snap = read_state_snapshot()
            dip = find_dip(snap, args.max_price)
            if not dip:
                time.sleep(POLL)
                continue

            plat, side, ask_price, ask_depth, consensus_count = dip
            invest = min(args.invest, ask_depth) if ask_depth > 0 else args.invest
            if invest < 1.0:
                log_order("SKIP_DEPTH_INSUFFICIENT",
                          plat=plat, side=side, ask=ask_price, depth=ask_depth)
                time.sleep(POLL)
                continue

            log_order("DIP_DETECTED",
                      plat=plat, side=side, ask=round(ask_price, 4),
                      depth_usd=round(ask_depth, 2),
                      consensus=consensus_count,
                      sec_in=sec_in_window, invest=invest)
            res = buy_leg(ctx, plat, side, invest, log_order)
            log_order("BUY_RESULT",
                      plat=plat, ok=res.get("ok"),
                      size_filled=round(float(res.get("size_filled") or 0), 4),
                      price=res.get("price"))
            if res.get("ok") and res.get("size_filled", 0) > 0:
                position = {
                    "plat": plat, "side": side,
                    "shares": float(res["size_filled"]),
                    "buy_price": float(res["price"]),
                    "buy_ts": now_e,
                    "window_epoch": window_epoch,
                }
            time.sleep(POLL)

        except KeyboardInterrupt:
            print("\nstopped by user", flush=True)
            break
        except Exception as e:
            log_order("MAIN_LOOP_ERROR", err=f"{type(e).__name__}: {e}")
            time.sleep(POLL)


if __name__ == "__main__":
    main()
