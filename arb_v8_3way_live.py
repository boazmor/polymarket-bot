#!/usr/bin/env python3
"""arb_v8_3way_live.py — Phantom-consensus directional bot.

Strategy:
  When at least 2 of the 3 platforms (Polymarket, Limitless, Predict.fun)
  show one side dropping to <= CHEAP_THRESH, that side is being signalled
  as the LOSING outcome. Buy the OPPOSITE side on whichever platform
  offers the best (lowest) ask.

  - Single directional bet, not an arb.
  - Profit if prediction is correct: (1 - opp_ask) / opp_ask, typically
    25-100% for trades in the GOLD zone.
  - Empirical hit rate on 38h of recorder data: 100% on n=12 GOLD trades.

Filters:
  - cheap side ask <= 0.10 on >= 2 platforms (phantom consensus)
  - opposite side best ask in [OPP_MIN, OPP_MAX] = [0.50, 0.80]
  - single trade per market window
  - per-window cooldown

No freshness checks. We trust silence (orderbook quiet means unchanged) and
the WS reconnect handlers in ws_feeds/.

Usage:
  python3 arb_v8_3way_live.py --window 15m --invest 1.20 --dry-run
  python3 arb_v8_3way_live.py --window 1h  --invest 100  --live
"""

import argparse
import asyncio
import csv
import json
import os
import re
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

# Strategy parameters (per user request after recorder analysis)
CHEAP_THRESH = 0.10
OPP_MIN = 0.50          # opposite side must be <= 0.80 (>= 25% profit)
OPP_MAX = 0.80
MIN_PLATFORMS_AGREE = 2 # require 2+ platforms to agree
COOLDOWN_SEC = 30
SETTLE_WAIT_SEC = 60


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


def _tail_last_csv(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(8192, size)
            f.seek(-chunk, 2)
            block = f.read(chunk).decode("utf-8", errors="replace")
        lines = [ln for ln in block.split("\n") if ln.strip()]
        return lines[-1] if lines else None
    except FileNotFoundError:
        return None


def _read_kalshi_latest():
    last = _tail_last_csv("/root/data_kalshi_btc_15m/combined_per_second.csv")
    if not last:
        return None
    f = last.split(",")
    if len(f) < 16:
        return None
    try:
        yes_ask = float(f[11] or 0)
        no_ask = float(f[15] or 0)
        ts_age = 0  # could compute if needed
        return {"connected": True, "yes_ask": yes_ask, "yes_ask_usd": 0,
                "no_ask": no_ask, "no_ask_usd": 0}
    except (ValueError, IndexError):
        return None


def _read_gemini_latest():
    last = _tail_last_csv("/root/data_gemini_btc_15m/combined_per_second.csv")
    if not last:
        return None
    f = last.split(",")
    if len(f) < 17:
        return None
    try:
        yes_ask = float(f[10] or 0)
        no_ask = float(f[15] or 0)  # no_ask_implied
        return {"connected": True, "yes_ask": yes_ask, "yes_ask_usd": 0,
                "no_ask": no_ask, "no_ask_usd": 0}
    except (ValueError, IndexError):
        return None


def read_all_strikes(window_label):
    """Try to read the current strike/target on each platform from recorder
    files. Returns dict plat -> float price (or None)."""
    strikes = {"poly": None, "predict": None, "lim": None, "kalshi": None, "gemini": None}
    # Polymarket: target_chainlink_at_open from combined_per_second.csv column 12
    try:
        last = _tail_last_csv(f"/root/data_btc_{window_label}_research/combined_per_second.csv")
        if last:
            f = last.split(",")
            v = float(f[12] or 0)
            if v > 0:
                strikes["poly"] = v
    except (ValueError, IndexError):
        pass
    # Predict: strike from latest.json
    try:
        import json as _json
        with open(f"/root/data_predict_btc_{window_label}/latest.json") as fp:
            d = _json.load(fp)
        v = float(d.get("strike") or 0)
        if v > 0:
            strikes["predict"] = v
    except Exception:
        pass
    # Limitless: title field includes strike sometimes; skip for now
    if window_label == "15m":
        # Kalshi: floor_strike column 5
        try:
            last = _tail_last_csv("/root/data_kalshi_btc_15m/combined_per_second.csv")
            if last:
                f = last.split(",")
                v = float(f[5] or 0)
                if v > 0:
                    strikes["kalshi"] = v
        except (ValueError, IndexError):
            pass
        # Gemini: strike column 5
        try:
            last = _tail_last_csv("/root/data_gemini_btc_15m/combined_per_second.csv")
            if last:
                f = last.split(",")
                v = float(f[5] or 0)
                if v > 0:
                    strikes["gemini"] = v
        except (ValueError, IndexError):
            pass
    return strikes


def read_state_snapshot(include_extras=True):
    """Read STATE for the 3 WS-driven platforms + optionally Kalshi & Gemini
    via CSV tail. include_extras = True only when window is 15m."""
    poly = STATE.get("poly")
    pr = STATE.get("predict")
    lim = STATE.get("lim")
    snap = {
        "poly": {
            "connected": poly.connected,
            "yes_ask": poly.best_ask,
            "yes_ask_usd": poly.ask_depth_usd,
            "no_ask": poly.no_best_ask,
            "no_ask_usd": poly.no_ask_depth_usd,
        },
        "predict": {
            "connected": pr.connected,
            "yes_ask": pr.best_ask,
            "yes_ask_usd": pr.ask_depth_usd,
            "no_ask": pr.no_best_ask,
            "no_ask_usd": pr.no_ask_depth_usd,
        },
        "lim": {
            "connected": lim.connected,
            "yes_ask": lim.best_ask,
            "yes_ask_usd": lim.ask_depth_usd,
            "no_ask": lim.no_best_ask,
            "no_ask_usd": lim.no_ask_depth_usd,
        },
    }
    if include_extras:
        ks = _read_kalshi_latest()
        if ks:
            snap["kalshi"] = ks
        ge = _read_gemini_latest()
        if ge:
            snap["gemini"] = ge
    return snap


def update_window_memory(memory, snap):
    """Track which platforms have EVER seen a phantom on each side during
    the current window. Memory persists for the lifetime of the window."""
    for plat, s in snap.items():
        if not s["connected"]:
            continue
        if 0 < s["yes_ask"] <= CHEAP_THRESH:
            memory["yes_seen"].add(plat)
        if 0 < s["no_ask"] <= CHEAP_THRESH:
            memory["no_seen"].add(plat)


def classify(snap, memory):
    """Decide whether the current snapshot + window memory yields a tradable
    signal. A platform 'agrees' if it has shown a phantom AT ANY POINT in
    the window so far. Opposite side must be CURRENTLY in [OPP_MIN, OPP_MAX]
    so we can act on the price right now."""
    yes_seen = memory["yes_seen"]
    no_seen = memory["no_seen"]
    # Majority rule. A platform showing phantom on BOTH sides over the
    # window is dropped from the count (genuinely undecided). If both
    # sides have agreement, the larger side wins so long as it outvotes
    # the other by MIN_MARGIN.
    overlap = yes_seen & no_seen
    yes_only = yes_seen - overlap
    no_only  = no_seen - overlap
    MIN_MARGIN = 2  # majority must outvote opposite side by at least this
    if len(yes_only) >= MIN_PLATFORMS_AGREE and len(yes_only) - len(no_only) >= MIN_MARGIN:
        predicted = "DOWN"
        opp_side = "no"
        agreement = len(yes_only)
        phantom_plats = list(yes_only)
    elif len(no_only) >= MIN_PLATFORMS_AGREE and len(no_only) - len(yes_only) >= MIN_MARGIN:
        predicted = "UP"
        opp_side = "yes"
        agreement = len(no_only)
        phantom_plats = list(no_only)
    else:
        return None

    # Kalshi and Gemini contribute to the consensus signal only; we cannot
    # place orders on them, so they are excluded from the buy-platform
    # candidate set.
    TRADABLE_PLATS = ("poly", "predict", "lim")
    best_plat = None
    best_ask = 1.01
    best_depth = 0
    for plat in TRADABLE_PLATS:
        s = snap.get(plat)
        if not s or not s["connected"]:
            continue
        a = s[f"{opp_side}_ask"]
        d = s[f"{opp_side}_ask_usd"]
        if a is None or a <= 0 or a >= 1:
            continue
        if a < best_ask:
            best_ask = a
            best_plat = plat
            best_depth = d
    if best_plat is None:
        return None
    if not (OPP_MIN <= best_ask <= OPP_MAX):
        return None
    return {
        "predicted": predicted,
        "agreement": agreement,
        "opp_side": opp_side,
        "best_opp_plat": best_plat,
        "best_opp_ask": best_ask,
        "best_opp_depth_usd": best_depth,
        "phantom_plats": phantom_plats,
    }


def fetch_poly_market(epoch, window_label):
    if window_label == "15m":
        slug = f"btc-updown-15m-{epoch}"
    else:
        from datetime import datetime as _dt
        d = _dt.fromtimestamp(epoch, tz=timezone.utc)
        d_et = d.astimezone()  # TODO proper ET
        slug = f"bitcoin-up-or-down-{d.strftime('%B-%d-%Y-%-Iam-et').lower()}"  # placeholder
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


def buy_leg(ctx, plat, opp_side, predicted, invest_usd, log_order):
    """Place a single FAK buy order on `plat` for the opposite-side outcome.
    Returns dict with ok/size_filled."""
    if ctx["dry_run"]:
        log_order("DRY_RUN_BUY", plat=plat, opp_side=opp_side, predicted=predicted, invest=invest_usd)
        return {"ok": True, "size_filled": invest_usd, "dry_run": True}
    if plat == "poly":
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
        token = ctx["poly_market"]["down_token"] if opp_side == "no" else ctx["poly_market"]["up_token"]
        ask_price = STATE.get("poly").no_best_ask if opp_side == "no" else STATE.get("poly").best_ask
        # FAK at slightly above current ask to ensure fill
        price = round(min(ask_price + 0.005, 0.99), 4)
        shares = round(invest_usd / price, 4)
        args = OrderArgsV2(price=price, size=shares, side="BUY", token_id=str(token))
        try:
            resp = ctx["poly_client"].create_and_post_order(args, order_type=OrderType.FAK)
            matched = float(resp.get("size_matched") or 0)
            return {"ok": matched > 0, "size_filled": matched, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "predict":
        outcome_name = "Down" if opp_side == "no" else "Up"
        outcome = next(o for o in ctx["predict_meta"]["outcomes"] if o["name"] == outcome_name)
        ask = STATE.get("predict").no_best_ask if opp_side == "no" else STATE.get("predict").best_ask
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
            code = resp.get("code")
            accepted = code == "OK"
            raw = resp.get("raw") or resp
            filled = float(raw.get("filledSize") or raw.get("size_matched") or 0) if accepted else 0
            return {"ok": accepted and filled > 0, "size_filled": filled, "price": price, "resp": resp}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    if plat == "lim":
        slug = STATE.get("lim").slug
        market = ctx["lt"].cache_market(slug)
        token = market.tokens.no if opp_side == "no" else market.tokens.yes
        ask = STATE.get("lim").no_best_ask if opp_side == "no" else STATE.get("lim").best_ask
        price = round(min(ask + 0.005, 0.99), 4)
        try:
            res = ctx["lt"].place_fak_buy(market_slug=slug, token_id=str(token),
                                           price=price, size_usdc=round(invest_usd, 4))
            filled = float(res.get("filled_shares") or 0)
            return {"ok": filled > 0, "size_filled": filled, "price": price, "resp": res}
        except Exception as e:
            return {"ok": False, "size_filled": 0, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "size_filled": 0, "error": f"unknown_plat_{plat}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["15m", "1h"], required=True)
    ap.add_argument("--invest", type=float, default=1.20)
    ap.add_argument("--max-trades-per-window", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", default=False)
    args = ap.parse_args()

    window_sec = 900 if args.window == "15m" else 3600
    tag = f"{args.window}_${int(args.invest)}"
    orders_path = f"/root/arb_v8_3way_{tag}_orders.csv"
    log_order = make_log_fn(orders_path)

    print(f"=== arb_v8_3way ({args.window}) starting at {now_iso()} ===", flush=True)
    print(f"invest=${args.invest}  dry_run={args.dry_run}  "
          f"cheap<={CHEAP_THRESH}  opp_in=[{OPP_MIN},{OPP_MAX}]  "
          f"min_agree={MIN_PLATFORMS_AGREE}", flush=True)

    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    lim_key = os.environ["LIMITLESS_API_KEY"]
    lim_sec = os.environ["LIMITLESS_API_SECRET"]
    pt = PredictTrader(api_key, pk, log_path=f"/root/arb_v8_3way_{tag}_predict.log")
    lt = LimitlessTrader(lim_key, lim_sec, pk, log_path=f"/root/arb_v8_3way_{tag}_lim.log")
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
    trades_this_window = 0
    last_attempt_ts = 0
    POLL = 0.05
    window_memory = {"yes_seen": set(), "no_seen": set()}
    # Stop-on-2-loss tracking. When a trade fires we remember the predicted
    # outcome; at window close we check the bid of the side we bought.
    last_trade_predicted = None
    last_trade_plat = None
    last_trade_side = None
    consec_losses = 0

    while True:
        try:
            now_e = int(time.time())
            window_epoch = (now_e // window_sec) * window_sec

            if window_epoch != current_epoch:
                # Settle previous window before opening new one.
                if last_trade_predicted is not None:
                    time.sleep(45)
                    snap_close = read_state_snapshot(include_extras=(args.window == "15m"))
                    bid_after = snap_close.get(last_trade_plat, {}).get(f"{last_trade_side}_bid", 0) or \
                                (1 - snap_close.get(last_trade_plat, {}).get(f"{'no' if last_trade_side=='yes' else 'yes'}_ask", 0))
                    if bid_after >= 0.95:
                        log_order("OUTCOME_WIN", plat=last_trade_plat, side=last_trade_side, bid=bid_after)
                        consec_losses = 0
                    elif bid_after <= 0.05:
                        log_order("OUTCOME_LOSS", plat=last_trade_plat, side=last_trade_side, bid=bid_after)
                        consec_losses += 1
                    else:
                        log_order("OUTCOME_AMBIGUOUS", plat=last_trade_plat, side=last_trade_side, bid=bid_after)
                    last_trade_predicted = None
                    last_trade_plat = None
                    last_trade_side = None
                    if consec_losses >= 2:
                        stop_path = f"/root/{os.path.basename(__file__).replace('.py','')}.stopped"
                        with open(stop_path, "w") as sf:
                            sf.write(f"{now_iso()} stopped after {consec_losses} consec losses\n")
                        log_order("STOP_FILE_WRITTEN", path=stop_path,
                                  reason=f"{consec_losses}_consec_losses")
                        print(f"\n!!! STOPPED: {consec_losses} consec losses. delete {stop_path} to resume\n", flush=True)
                        return
                current_epoch = window_epoch
                trades_this_window = 0
                last_attempt_ts = 0
                window_memory = {"yes_seen": set(), "no_seen": set()}
                pm = fetch_poly_market(window_epoch, args.window)
                ctx["poly_market"] = pm
                if pm:
                    market_holders["poly_up_tokens"] = [pm["up_token"]]
                    market_holders["poly_down_tokens"] = [pm["down_token"]]
                # Predict + Limitless slug resolution happens via recorder
                # files - read them once per window.
                try:
                    with open(f"/root/data_predict_btc_{args.window}/markets.csv") as f:
                        rows = list(csv.reader(f))
                    pid = int(rows[-1][1])
                    market_holders["predict_market_id"] = pid
                    ctx["predict_market_id"] = pid
                    ctx["predict_meta"] = pt.get_market(pid)
                except Exception as e:
                    log_order("PRECACHE_PREDICT_FAIL", err=f"{type(e).__name__}: {e}")
                # Limitless — query /markets/active directly so we always
                # pick the currently-active market, not whatever the recorder
                # happened to log last (which lags 10-40s and may be expired).
                try:
                    _title_map = {"5m": "BTC Up or Down - 5 Min",
                                  "15m": "BTC Up or Down - 15 Min",
                                  "1h": "BTC Up or Down - 1 Hour",
                                  "1d": "BTC Up or Down - 1 Day"}
                    _wanted = _title_map.get(args.window)
                    import urllib.request as _u, json as _j
                    _req = _u.Request("https://api.limitless.exchange/markets/active",
                                      headers={"User-Agent": "arb-bot/1.0"})
                    with _u.urlopen(_req, timeout=4) as _r:
                        _data = _j.loads(_r.read())
                    _now_ms = int(time.time() * 1000)
                    _matches = [m for m in _data.get("data", [])
                                if m.get("title") == _wanted
                                and (m.get("expirationTimestamp") or 0) > _now_ms]
                    if not _matches:
                        raise ValueError(f"no active {args.window} BTC market")
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

            if trades_this_window >= args.max_trades_per_window:
                time.sleep(POLL)
                continue
            if time.time() - last_attempt_ts < COOLDOWN_SEC:
                time.sleep(POLL)
                continue

            snap = read_state_snapshot(include_extras=(args.window == "15m"))
            update_window_memory(window_memory, snap)
            decision = classify(snap, window_memory)
            if not decision:
                time.sleep(POLL)
                continue

            sec_in_window = int(time.time()) - current_epoch
            strikes = read_all_strikes(args.window)
            strike_vals = [v for v in strikes.values() if v is not None and v > 0]
            strike_spread = (max(strike_vals) - min(strike_vals)) if len(strike_vals) >= 2 else 0
            log_order("SIGNAL_DETECTED",
                      predicted=decision["predicted"],
                      agreement=decision["agreement"],
                      phantom_plats=decision["phantom_plats"],
                      opp_side=decision["opp_side"],
                      best_opp_plat=decision["best_opp_plat"],
                      best_opp_ask=round(decision["best_opp_ask"], 4),
                      best_opp_depth=round(decision["best_opp_depth_usd"], 2),
                      profit_pct=round((1 - decision["best_opp_ask"]) / decision["best_opp_ask"] * 100, 1),
                      sec_in_window=sec_in_window,
                      strike_poly=strikes.get("poly"),
                      strike_predict=strikes.get("predict"),
                      strike_lim=strikes.get("lim"),
                      strike_kalshi=strikes.get("kalshi"),
                      strike_gemini=strikes.get("gemini"),
                      strike_spread_usd=round(strike_spread, 2))

            # Scale invest by agreement strength:
            # 3 platforms = 1x, 4 = 2x, 5 = 3x.
            agreement = decision["agreement"]
            if agreement >= 5:
                multiplier = 3
            elif agreement >= 4:
                multiplier = 2
            else:
                multiplier = 1
            target_invest = args.invest * multiplier
            invest = min(target_invest, decision["best_opp_depth_usd"])
            if invest < 1.0:
                log_order("SKIP_DEPTH_INSUFFICIENT",
                          depth=decision["best_opp_depth_usd"], target=target_invest)
                last_attempt_ts = time.time()
                time.sleep(POLL)
                continue

            log_order("FIRE",
                      plat=decision["best_opp_plat"],
                      invest_usd=invest, agreement=agreement, multiplier=multiplier,
                      opp_side=decision["opp_side"])
            res = buy_leg(ctx, decision["best_opp_plat"], decision["opp_side"],
                          decision["predicted"], invest, log_order)
            log_order("FILL_RESULT",
                      plat=decision["best_opp_plat"], ok=res.get("ok"),
                      size_filled=round(float(res.get("size_filled") or 0), 4),
                      price=res.get("price"))
            last_attempt_ts = time.time()
            if res.get("ok"):
                last_trade_predicted = decision["predicted"]
                last_trade_plat = decision["best_opp_plat"]
                last_trade_side = decision["opp_side"]
            if res.get("ok"):
                trades_this_window += 1
            time.sleep(POLL)

        except KeyboardInterrupt:
            print("\nstopped by user", flush=True)
            break
        except Exception as e:
            log_order("MAIN_LOOP_ERROR", err=f"{type(e).__name__}: {e}")
            time.sleep(POLL)


if __name__ == "__main__":
    main()
