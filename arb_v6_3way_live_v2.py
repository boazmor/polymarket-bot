#!/usr/bin/env python3
"""arb_v6_3way_live_v2.py - Phase 2 incremental speedup + Phase 1.5 safety (1-HOUR).

INCREMENTAL changes over v1 (arb_v5_3way_live.py). Keeps the same sync main
loop and ThreadPoolExecutor pattern for low refactor risk, but adds:

  A. 403 / geoblock detection -> block the window immediately so the bot
     stops wasting cycles attempting orders that will keep failing.
  B. Predict position reconciliation when fill_confidence == 'assumed_full':
     query positions API right after submission and verify the position
     delta matches the requested shares.
  C. Pre-cache market metadata in PARALLEL at window open via
     ThreadPoolExecutor.map instead of three serial fetches.
  D. Reduce f.result timeout from 30s to 1.5s for fail-fast.
  E. Skip end-of-loop sleep after a trade fires (lets the next opportunity
     be detected immediately instead of dead-time 200ms wait).
  F. Optional: use urllib3 PoolManager for keep-alive on Poly market fetch.

Safety properties of v1 are preserved in full:
  - 6 candidate pairs; cross_oracle auto-derived
  - Top-up only on oracle-matching third platform
  - 60s pre-close block for cross-oracle pairs
  - EXCESS_SELL_PCT = 0.05
  - emergency_sell returns verified result
  - snapshot_wealth validity flag
  - --dry-run mode
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Change F: single global PoolManager for keep-alive HTTPS to Polymarket
# gamma + data APIs. Reuses TLS sessions across calls instead of paying the
# full handshake (~30-80ms) on every market fetch + position query.
HTTP_POOL = urllib3.PoolManager(
    maxsize=10,
    block=False,
    # AI3 fix: connect retry only, NO read retries — read retries can turn a
    # 200ms hiccup into a 2-4 second stall.
    retries=urllib3.Retry(connect=1, read=0, backoff_factor=0.05),
    headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
)

sys.path.insert(0, "/root")
from predict_trader import PredictTrader
from limitless_trader import LimitlessTrader

from dotenv import load_dotenv

ENV_PATH = "/root/live/btc_5m/.env"
load_dotenv(ENV_PATH, override=True)

P_DATA = "/root/data_btc_1h_research/combined_per_second.csv"
PR_LATEST_JSON = "/root/data_predict_btc_1h/latest.json"
PR_MARKETS = "/root/data_predict_btc_1h/markets.csv"
LIM_LATEST_JSON = "/root/data_limitless_btc_1h/latest.json"
LIM_MARKETS = "/root/data_limitless_btc_1h/markets.csv"

LIVE_TRADES = "/root/arb_v6_3way_live_trades.csv"
LIVE_ORDERS = "/root/arb_v6_3way_live_orders.csv"

BASE_NOTIONAL_USD = 1.20
MAX_SIDE_USD = 7.0
COST_THRESHOLD = 0.90
SINGLE_LEG_MAX_ASK = 0.80
MIN_DEPTH_USD = 5.0
PARALLEL_DEPTH_MULTIPLIER = 4.0
EXCESS_SELL_PCT = 0.05
LAST_SECONDS_BLOCK_CROSS_ORACLE = 60
COOLDOWN_SEC = 5
POLL_SEC = 0.2
MAX_FEED_AGE_SEC = 10
LIM_MAX_AGE_SEC = 20
SETTLE_WAIT_SEC = 60
WINDOW_SEC = 3600
# Phase 2 tight timeouts: fail-fast on unresponsive platforms so the bot
# doesn't hang for 30s waiting on a single bad API call. Polymarket order
# placement is normally 100-300ms; 1.5s allows 5x slowdown headroom.
ORDER_TIMEOUT_SEC = 0.8  # tightened from 1.5s after AI review: FAK that doesn't fill in 500-800ms is stale anyway
POST_TRADE_MIN_SLEEP = 0.05  # floor for Change E so we don't busy-spin on a malformed feed
# Phase 2 quote-freshness gates (per AI freshness review):
MAX_QUOTE_AGE_MS = 80   # reject FAK if any leg's last update >80ms old
MAX_QUOTE_AGE_SKEW_MS = 50  # reject if leg ages differ by >50ms (asymmetric staleness)
# LATE_FILL_GRACE_MS removed — late-fill probe deferred to Phase 3

ORACLE_BY_PLATFORM = {
    "poly": "chainlink",
    "lim": "chainlink",
    "predict": "binance",
}

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
    ts_ms = int(d.get("ts_ms", 0))
    return {
        "epoch": ts_ms // 1000,
        "ts_ms": ts_ms,  # ms-precision recorder write timestamp for freshness check
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
    up_ask = float(d.get("best_ask", 0))
    up_bid = float(d.get("best_bid", 0))
    up_ask_usd = float(d.get("best_ask_size_usd", 0))
    up_bid_usd = float(d.get("best_bid_size_usd", 0))
    # NO outcome: CTF complement of YES. NO_ask is the inverse of UP_bid (the price
    # someone is willing to BUY UP at = the price someone implicitly OFFERS NO at).
    # Prefer explicit no_best_ask from recorder if available.
    down_ask = float(d.get("no_best_ask", round(1.0 - up_bid, 4) if up_bid > 0 else 0))
    down_ask_usd = float(d.get("no_best_ask_size_usd", up_bid_usd))
    ts_ms = int(d.get("ts_ms", 0))
    return {
        "epoch": ts_ms // 1000,
        "ts_ms": ts_ms,  # ms-precision recorder write timestamp for freshness check
        "market_id": d.get("market_id", ""),
        "slug": d.get("slug", ""),
        "up_ask": up_ask,
        "up_bid": up_bid,
        "up_ask_usd": up_ask_usd,
        "up_bid_usd": up_bid_usd,
        "down_ask": down_ask,
        "down_ask_usd": down_ask_usd,
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
    from datetime import timedelta
    utc_dt = datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)
    et_offset = 4 if 3 <= utc_dt.month <= 11 else 5
    et_dt = utc_dt - timedelta(hours=et_offset)
    month = et_dt.strftime("%B").lower()
    hour_12 = et_dt.hour % 12 or 12
    ampm = "am" if et_dt.hour < 12 else "pm"
    slug = f"bitcoin-up-or-down-{month}-{et_dt.day}-{et_dt.year}-{hour_12}{ampm}-et"
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    # Change F: use shared PoolManager for keep-alive
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


def snapshot_wealth(pt, poly_client, lt):
    """Return wealth snapshot with explicit validity flag.

    A snapshot is `valid` only if ALL five sources returned successfully.
    The caller MUST treat invalid snapshots as missing data (not as a loss):
    do not compute PnL, do not trigger stop-on-loss, log and continue.
    """
    missing = []
    usdt_balance = 0.0
    try:
        from web3 import Web3
        from predict_sdk import ChainId, ADDRESSES_BY_CHAIN_ID, RPC_URLS_BY_CHAIN_ID, ERC20_ABI
        addrs = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
        w3 = Web3(Web3.HTTPProvider(RPC_URLS_BY_CHAIN_ID[ChainId.BNB_MAINNET]))
        usdt = w3.eth.contract(address=Web3.to_checksum_address(addrs.USDT), abi=ERC20_ABI)
        eoa = Web3.to_checksum_address(pt.address)
        usdt_balance = usdt.functions.balanceOf(eoa).call() / 1e18
    except Exception as e:
        missing.append(f"bnb_usdt:{type(e).__name__}")

    predict_pos_value = 0.0
    try:
        positions = pt.get_positions()
        predict_pos_value = sum(float(p.get("valueUsd") or 0) for p in positions)
    except Exception as e:
        missing.append(f"predict_positions:{type(e).__name__}")

    poly_usdc = 0.0
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=2)
        resp = poly_client.get_balance_allowance(params)
        poly_usdc = int(resp["balance"]) / 1e6
    except Exception as e:
        missing.append(f"poly_usdc:{type(e).__name__}")

    poly_positions_value = 0.0
    try:
        url = f"https://data-api.polymarket.com/positions?user={POLY_SAFE}&limit=100"
        # Change F: shared PoolManager for keep-alive
        r = HTTP_POOL.request("GET", url, timeout=urllib3.Timeout(connect=3.0, read=7.0))
        data = json.loads(r.data) if r.status == 200 else []
        for p in (data if isinstance(data, list) else []):
            cv = p.get("currentValue") or p.get("current_value") or 0
            try:
                poly_positions_value += float(cv)
            except (TypeError, ValueError):
                pass
    except Exception as e:
        missing.append(f"poly_positions:{type(e).__name__}")

    lim_usdc = 0.0
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
    except Exception as e:
        missing.append(f"lim_usdc:{type(e).__name__}")

    total = usdt_balance + predict_pos_value + poly_usdc + poly_positions_value + lim_usdc
    return {
        "usdt": usdt_balance,
        "predict_positions": predict_pos_value,
        "poly_usdc": poly_usdc,
        "poly_positions": poly_positions_value,
        "lim_usdc": lim_usdc,
        "total": total,
        "valid": not missing,
        "missing": missing,
    }


def _derive_cross_oracle(legs):
    """cross_oracle is True iff the two legs settle under different oracles.

    Derived automatically from each leg's oracle field so a typo cannot bypass
    the late-window safety filter.
    """
    oracles = {l.get("oracle") for l in legs}
    return len(oracles) > 1


def build_candidates(p, pr, lim, poly_market, predict_meta):
    """Return list of viable candidate tuples.

    Each candidate: {direction, cost, legs, cross_oracle}
      cross_oracle is derived from leg oracles, not hardcoded.
    """
    cands = []
    if p and pr and pr["no_ask_implied"] > 0 and p["ua"] > 0:
        cands.append({
            "direction": "A_POLY",
            "cost": p["ua"] + pr["no_ask_implied"],
            "legs": [
                {"platform": "poly", "oracle": "chainlink", "side": "BUY", "outcome": "Up",
                 "ask": p["ua"], "depth_usd": p["ua_usd"],
                 "token": poly_market["up_token"] if poly_market else None},
                {"platform": "predict", "oracle": "binance", "side": "BUY", "outcome": "Down",
                 "ask": pr["no_ask_implied"], "depth_usd": pr["no_ask_usd"]},
            ],
        })
    if p and pr and pr["yes_ask"] > 0 and p["da"] > 0:
        cands.append({
            "direction": "B_POLY",
            "cost": p["da"] + pr["yes_ask"],
            "legs": [
                {"platform": "poly", "oracle": "chainlink", "side": "BUY", "outcome": "Down",
                 "ask": p["da"], "depth_usd": p["da_usd"],
                 "token": poly_market["down_token"] if poly_market else None},
                {"platform": "predict", "oracle": "binance", "side": "BUY", "outcome": "Up",
                 "ask": pr["yes_ask"], "depth_usd": pr["yes_ask_usd"]},
            ],
        })
    if lim and pr and lim["up_ask"] > 0 and pr["no_ask_implied"] > 0:
        cands.append({
            "direction": "A_LIM",
            "cost": lim["up_ask"] + pr["no_ask_implied"],
            "legs": [
                {"platform": "lim", "oracle": "chainlink", "side": "BUY", "outcome": "yes",
                 "ask": lim["up_ask"], "depth_usd": lim["up_ask_usd"],
                 "slug": lim["slug"]},
                {"platform": "predict", "oracle": "binance", "side": "BUY", "outcome": "Down",
                 "ask": pr["no_ask_implied"], "depth_usd": pr["no_ask_usd"]},
            ],
        })
    if lim and p and lim["up_ask"] > 0 and p["da"] > 0:
        cands.append({
            "direction": "LimUP_PolyDN",
            "cost": lim["up_ask"] + p["da"],
            "legs": [
                {"platform": "lim", "oracle": "chainlink", "side": "BUY", "outcome": "yes",
                 "ask": lim["up_ask"], "depth_usd": lim["up_ask_usd"],
                 "slug": lim["slug"]},
                {"platform": "poly", "oracle": "chainlink", "side": "BUY", "outcome": "Down",
                 "ask": p["da"], "depth_usd": p["da_usd"],
                 "token": poly_market["down_token"] if poly_market else None},
            ],
        })
    if lim and pr and lim.get("down_ask", 0) > 0 and pr["yes_ask"] > 0:
        cands.append({
            "direction": "B_LIM",
            "cost": lim["down_ask"] + pr["yes_ask"],
            "legs": [
                {"platform": "lim", "oracle": "chainlink", "side": "BUY", "outcome": "no",
                 "ask": lim["down_ask"], "depth_usd": lim["down_ask_usd"],
                 "slug": lim["slug"]},
                {"platform": "predict", "oracle": "binance", "side": "BUY", "outcome": "Up",
                 "ask": pr["yes_ask"], "depth_usd": pr["yes_ask_usd"]},
            ],
        })
    if p and lim and p["ua"] > 0 and lim.get("down_ask", 0) > 0:
        cands.append({
            "direction": "PolyUP_LimDN",
            "cost": p["ua"] + lim["down_ask"],
            "legs": [
                {"platform": "poly", "oracle": "chainlink", "side": "BUY", "outcome": "Up",
                 "ask": p["ua"], "depth_usd": p["ua_usd"],
                 "token": poly_market["up_token"] if poly_market else None},
                {"platform": "lim", "oracle": "chainlink", "side": "BUY", "outcome": "no",
                 "ask": lim["down_ask"], "depth_usd": lim["down_ask_usd"],
                 "slug": lim["slug"]},
            ],
        })
    # Auto-derive cross_oracle from leg oracles so a manual typo cannot
    # incorrectly bypass the late-window safety filter.
    for c in cands:
        c["cross_oracle"] = _derive_cross_oracle(c["legs"])
    return cands


def pick_best(cands, sec_to_close=None):
    viable = []
    for c in cands:
        if c["cost"] > COST_THRESHOLD:
            continue
        if any(l["ask"] > SINGLE_LEG_MAX_ASK for l in c["legs"]):
            continue
        if any(l["depth_usd"] < MIN_DEPTH_USD for l in c["legs"]):
            continue
        # In the final seconds of a window, oracles can diverge right at the strike.
        # Cross-oracle hedges are vulnerable to BOTH-LOSE in that scenario.
        # Same-oracle pairs are always safe since both legs settle identically.
        if c.get("cross_oracle") and sec_to_close is not None and sec_to_close < LAST_SECONDS_BLOCK_CROSS_ORACLE:
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


def check_freshness(cand, p, pr, lim, now_ms):
    """Verify each leg's underlying feed is fresh AND that the two legs are
    not asymmetrically stale relative to each other. Returns (ok, reason, ages).

    POLY note: the Polymarket recorder writes only second-precision
    `epoch_sec` to its CSV. We use the file mtime of the CSV as a ms-precision
    proxy for "last update time" — that's when the recorder last wrote a row,
    which is its last received WS message.
    """
    leg_ages_ms = []
    for leg in cand["legs"]:
        plat = leg["platform"]
        if plat == "poly":
            # BLOCKER 1 FIX: epoch_sec only gives second precision so it
            # appears 0-999ms stale randomly. Use the CSV file mtime instead.
            try:
                ts_ms_leg = int(os.path.getmtime(P_DATA) * 1000)
            except Exception:
                ts_ms_leg = 0
        elif plat == "predict":
            ts_ms_leg = pr.get("ts_ms", 0) if pr else 0
        elif plat == "lim":
            ts_ms_leg = lim.get("ts_ms", 0) if lim else 0
        else:
            ts_ms_leg = 0
        age = now_ms - ts_ms_leg if ts_ms_leg > 0 else 999_999
        leg_ages_ms.append(age)
        if age > MAX_QUOTE_AGE_MS:
            return False, f"stale_{plat}_{age}ms", leg_ages_ms
    if len(leg_ages_ms) == 2:
        skew = abs(leg_ages_ms[0] - leg_ages_ms[1])
        if skew > MAX_QUOTE_AGE_SKEW_MS:
            return False, f"asymmetric_skew_{skew}ms", leg_ages_ms
    return True, "fresh", leg_ages_ms


def place_poly(poly_client, OrderArgsV2, OrderType, leg, price, shares, dry_run=False):
    t0 = time.time()
    if dry_run:
        log_order("DRY_RUN_POLY_BUY", price=price, shares=shares, token=str(leg.get("token"))[:12])
        return {"platform": "poly", "ok": True, "size_filled": shares,
                "dry_run": True, "ms": (time.time() - t0) * 1000}
    try:
        args = OrderArgsV2(price=round(price, 4), size=round(shares, 4), side="BUY",
                           token_id=str(leg["token"]))
        resp = poly_client.create_and_post_order(args, order_type=OrderType.FAK)
        filled = resp and resp.get("status") == "matched"
        size_filled = float(resp.get("size_matched") or shares if filled else 0)
        return {"platform": "poly", "ok": filled, "size_filled": size_filled,
                "resp": resp, "ms": (time.time() - t0) * 1000}
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        # Detect geoblock or persistent platform failures so the caller can
        # stop hammering this platform for the rest of the window.
        geoblock = "403" in err_msg or "geoblock" in err_msg.lower() or "restricted" in err_msg.lower()
        return {"platform": "poly", "ok": False, "size_filled": 0,
                "error": err_msg, "geoblock": geoblock,
                "ms": (time.time() - t0) * 1000}


def place_predict(pt, predict_meta, current_predict_market, leg, price, shares, dry_run=False):
    t0 = time.time()
    if dry_run:
        log_order("DRY_RUN_PREDICT_BUY", price=price, shares=shares, outcome=leg.get("outcome"))
        return {"platform": "predict", "ok": True, "size_filled": shares,
                "dry_run": True, "ms": (time.time() - t0) * 1000}
    try:
        outcome_name = leg["outcome"]
        outcome = next(o for o in predict_meta["outcomes"] if o["name"] == outcome_name)
        # Change B: snapshot positions BEFORE submitting so we can reconcile
        # the actual fill if the API doesn't echo filledSize. Pre-snapshot is
        # mandatory — without it we can't distinguish our fill from pre-
        # existing inventory or another bot/window's concurrent fill.
        outcome_token_id_str = str(outcome["onChainId"])
        pre_shares = 0.0
        try:
            pre_positions = pt.get_positions()
            for p in pre_positions:
                pid = (p.get("marketId") or p.get("market_id")
                       or p.get("market") or "")
                oid = (p.get("outcomeId") or p.get("outcome_id")
                       or p.get("tokenId") or p.get("onChainId") or "")
                if str(pid) == str(current_predict_market) and str(oid) == outcome_token_id_str:
                    pre_shares += float(p.get("size") or p.get("shares") or 0)
        except Exception as e:
            log_order("PREDICT_PRE_SNAPSHOT_FAILED", err=f"{type(e).__name__}: {e}")
            pre_shares = None  # Cannot reconcile without baseline
        resp = pt.place_limit(
            market_id=current_predict_market,
            outcome_token_id=outcome["onChainId"],
            side=leg["side"], price=price, shares=shares,
            is_neg_risk=predict_meta["isNegRisk"],
            is_yield_bearing=predict_meta["isYieldBearing"],
            fee_rate_bps=predict_meta["feeRateBps"],
        )
        order_id = resp.get("orderId")
        code = resp.get("code")
        accepted = bool(order_id) and code == "OK"
        # Detect geoblock / persistent rejection: Predict returns code != "OK"
        # with status in the response or in the error text.
        resp_str = str(resp)
        geoblock = (not accepted) and (
            "403" in resp_str
            or "restricted" in resp_str.lower()
            or "geoblock" in resp_str.lower()
            or "forbidden" in resp_str.lower()
        )
        raw_block = resp.get("raw") if isinstance(resp.get("raw"), dict) else resp
        filled_raw = (raw_block.get("filledSize")
                      or raw_block.get("size_matched")
                      or raw_block.get("matched_size")
                      or raw_block.get("matchedSize")
                      or resp.get("filledSize")
                      or resp.get("size_matched"))
        try:
            filled = float(filled_raw) if filled_raw is not None else None
        except (TypeError, ValueError):
            filled = None
        if filled is None:
            # API accepted but didn't echo fill amount. Change B: reconcile
            # via positions API (best-effort with 2 retries to handle eventual
            # consistency on the BNB chain). If positions delta confirms a
            # fill, use the measured delta. Otherwise treat as unverified.
            size_filled = shares if accepted else 0.0
            fill_confidence = "assumed_full" if accepted else "rejected"
            if accepted and pre_shares is not None:
                measured_delta = None
                # BLOCKER FIX (AI3): a single fast check, NO time.sleep.
                # Sleeping 200ms 3x in the hot path delays each trade by up to
                # 600ms. If positions API has eventual consistency, accept the
                # uncertainty: better to under-report a fill than to block the
                # main loop for half a second per trade.
                try:
                    post_positions = pt.get_positions()
                    post_shares_for_outcome = 0.0
                    for p in post_positions:
                        pid = (p.get("marketId") or p.get("market_id")
                               or p.get("market") or "")
                        oid = (p.get("outcomeId") or p.get("outcome_id")
                               or p.get("tokenId") or p.get("onChainId") or "")
                        if str(pid) == str(current_predict_market) and str(oid) == outcome_token_id_str:
                            post_shares_for_outcome += float(p.get("size") or p.get("shares") or 0)
                    measured_delta = post_shares_for_outcome - pre_shares
                except Exception:
                    measured_delta = None
                if measured_delta is not None and measured_delta >= shares * 0.10:
                    size_filled = measured_delta
                    fill_confidence = "reconciled" if measured_delta >= shares * 0.95 else "reconciled_partial"
                    log_order("PREDICT_RECONCILED",
                              expected=shares, measured=round(measured_delta, 4),
                              confidence=fill_confidence)
                elif measured_delta is not None:
                    # Position did not change — order was likely not filled
                    size_filled = 0.0
                    accepted = False
                    fill_confidence = "reconciled_zero"
                    log_order("PREDICT_RECONCILED_ZERO_FILL",
                              expected=shares, measured=round(measured_delta, 4))
                else:
                    log_order("PREDICT_FILL_ASSUMED_FULL_RECONCILE_FAIL",
                              shares=shares, price=price,
                              note="positions_api_unreachable_falling_back_to_assumed_full")
            elif accepted:
                # BLOCKER FIX (AI1): without a baseline we cannot reconcile, so
                # we MUST NOT assume the order filled. Treat as rejected. Top-up
                # and emergency-sell logic will handle the missing leg.
                size_filled = 0.0
                accepted = False
                fill_confidence = "unverified_rejected"
                log_order("PREDICT_REJECTED_NO_BASELINE",
                          shares=shares, price=price,
                          note="pre_snapshot_failed_cannot_verify_fill")
        else:
            size_filled = filled
            fill_confidence = "verified"
            accepted = accepted and filled > 0
        return {"platform": "predict", "ok": accepted, "size_filled": size_filled,
                "fill_confidence": fill_confidence, "resp": resp,
                "geoblock": geoblock,
                "ms": (time.time() - t0) * 1000}
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        geoblock = "403" in err_msg or "restricted" in err_msg.lower() or "forbidden" in err_msg.lower()
        return {"platform": "predict", "ok": False, "size_filled": 0,
                "error": err_msg, "geoblock": geoblock,
                "ms": (time.time() - t0) * 1000}


def place_limitless(lt, leg, price, shares, dry_run=False):
    t0 = time.time()
    if dry_run:
        log_order("DRY_RUN_LIM_BUY", price=price, shares=shares, outcome=leg.get("outcome"))
        return {"platform": "lim", "ok": True, "size_filled": shares,
                "dry_run": True, "ms": (time.time() - t0) * 1000}
    try:
        size_usdc = round(shares * price, 4)
        slug = leg["slug"]
        market = lt.cache_market(slug)
        token_id = market.tokens.yes if leg["outcome"] == "yes" else market.tokens.no
        res = lt.place_fak_buy(market_slug=slug, token_id=str(token_id),
                               price=round(price, 4), size_usdc=size_usdc)
        filled_shares = float(res.get("filled_shares") or 0)
        ok = filled_shares > 0
        err = res.get("error", "")
        geoblock = "403" in str(err) or "restricted" in str(err).lower() or "forbidden" in str(err).lower()
        return {"platform": "lim", "ok": ok, "size_filled": filled_shares,
                "resp": res, "geoblock": geoblock,
                "ms": (time.time() - t0) * 1000}
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        geoblock = "403" in err_msg or "restricted" in err_msg.lower() or "forbidden" in err_msg.lower()
        return {"platform": "lim", "ok": False, "size_filled": 0,
                "error": err_msg, "geoblock": geoblock,
                "ms": (time.time() - t0) * 1000}


def fire_leg(ctx, leg, shares):
    """Dispatch to platform-specific BUY function."""
    dry = ctx.get("dry_run", False)
    if leg["platform"] == "poly":
        return place_poly(ctx["poly_client"], ctx["OrderArgsV2"], ctx["OrderType"],
                          leg, leg["ask"], shares, dry_run=dry)
    if leg["platform"] == "predict":
        return place_predict(ctx["pt"], ctx["predict_meta"], ctx["predict_market_id"],
                             leg, leg["ask"], shares, dry_run=dry)
    if leg["platform"] == "lim":
        return place_limitless(ctx["lt"], leg, leg["ask"], shares, dry_run=dry)
    raise ValueError(f"unknown platform: {leg['platform']}")


def emergency_sell(ctx, leg, excess_shares):
    """Aggressive FAK sell of excess shares on the over-filled platform.

    Returns {"ok": bool, "size_filled": float, "platform": str}. Caller MUST
    verify ok+size_filled before clearing window_has_unhedged.
    """
    log_order("EMERGENCY_SELL_TRY", platform=leg["platform"], excess=excess_shares)
    if ctx.get("dry_run"):
        log_order("EMERGENCY_SELL_DRY_RUN", platform=leg["platform"], excess=excess_shares)
        return {"ok": True, "size_filled": excess_shares, "platform": leg["platform"], "dry_run": True}
    try:
        if leg["platform"] == "poly":
            sell_args = ctx["OrderArgsV2"](
                price=round(max(leg["ask"] - 0.10, 0.01), 4),
                size=round(excess_shares, 4),
                side="SELL", token_id=str(leg["token"]),
            )
            resp = ctx["poly_client"].create_and_post_order(sell_args, order_type=ctx["OrderType"].FAK)
            status = (resp or {}).get("status")
            matched = float((resp or {}).get("size_matched") or 0)
            ok = status == "matched" and matched > 0
            log_order("EMERGENCY_SELL_POLY", status=status, matched=matched, ok=ok)
            return {"ok": ok, "size_filled": matched if ok else 0.0, "platform": "poly"}
        if leg["platform"] == "predict":
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
            code = resp.get("code")
            order_id = resp.get("orderId")
            raw = resp.get("raw") if isinstance(resp.get("raw"), dict) else resp
            filled_raw = (raw.get("filledSize") or raw.get("size_matched")
                          or raw.get("matched_size") or raw.get("matchedSize"))
            try:
                filled = float(filled_raw) if filled_raw is not None else None
            except (TypeError, ValueError):
                filled = None
            accepted = bool(order_id) and code == "OK"
            # CRITICAL: emergency sell is the last line of defense. If the API
            # accepts but does not echo fill size, we MUST NOT assume the sell
            # succeeded — that would clear window_has_unhedged on phantom data.
            # Treat unknown fill as zero so the caller keeps the window blocked.
            if filled is None:
                filled = 0.0
                log_order("EMERGENCY_SELL_PREDICT_UNKNOWN_FILL", code=code, order_id=order_id,
                          note="api_accepted_but_no_fill_size_returned-treating_as_zero")
            ok = accepted and filled > 0
            log_order("EMERGENCY_SELL_PREDICT", code=code, filled=filled, ok=ok)
            return {"ok": ok, "size_filled": filled if ok else 0.0, "platform": "predict"}
        if leg["platform"] == "lim":
            slug = leg["slug"]
            market = ctx["lt"].cache_market(slug)
            token_id = market.tokens.yes if leg["outcome"] == "yes" else market.tokens.no
            sell_price = max(round(leg["ask"] - 0.05, 4), 0.01)
            res = ctx["lt"].place_fak_sell(market_slug=slug, token_id=str(token_id),
                                            price=sell_price, size_shares=excess_shares)
            filled = float(res.get("filled_shares") or 0)
            ok = filled > 0
            log_order("EMERGENCY_SELL_LIM", filled=filled, ok=ok)
            return {"ok": ok, "size_filled": filled, "platform": "lim"}
    except Exception as e:
        log_order("EMERGENCY_SELL_ERROR", platform=leg["platform"],
                  err=f"{type(e).__name__}: {e}")
    return {"ok": False, "size_filled": 0.0, "platform": leg["platform"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-windows", type=int, default=9999)
    ap.add_argument("--max-trades-per-window", type=int, default=2)
    ap.add_argument("--invest", type=float, default=MAX_SIDE_USD)
    ap.add_argument("--stop-on-loss", action="store_true", default=True)
    ap.add_argument("--settle-wait-sec", type=int, default=SETTLE_WAIT_SEC)
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="run full strategy but log orders instead of submitting")
    args = ap.parse_args()

    print(f"=== arb_v6_3way_live (1h) starting at {now_iso()} ===", flush=True)
    print(f"invest_per_side=${args.invest}  max_trades_per_window={args.max_trades_per_window}", flush=True)

    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    lim_key = os.environ["LIMITLESS_API_KEY"]
    lim_sec = os.environ["LIMITLESS_API_SECRET"]

    pt = PredictTrader(api_key, pk, log_path="/root/arb_v6_3way_live_predict.log")
    lt = LimitlessTrader(lim_key, lim_sec, pk, log_path="/root/arb_v6_3way_live_lim.log")

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
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print("--- DRY RUN MODE: orders will be logged not submitted ---", flush=True)

    pool = ThreadPoolExecutor(max_workers=3)

    while windows_done < args.max_windows:
        trade_fired_this_iter = False
        try:
            p_row = tail_last_row(P_DATA)
            p = parse_poly(p_row, p_hdr)
            pr = parse_predict_latest()
            lim = parse_limitless_latest()

            now_e = int(time.time())
            window_epoch = (now_e // 900) * 900

            if window_epoch != current_epoch:
                if current_epoch is not None and window_open_wealth is not None:
                    print(f"\n>>> WINDOW {current_epoch} (1h) CLOSED. trades={trades_this_window}. waiting {args.settle_wait_sec}s...", flush=True)
                    time.sleep(args.settle_wait_sec)
                    close_snap = snapshot_wealth(pt, poly_client, lt)
                    if not close_snap.get("valid") or not window_open_wealth.get("valid"):
                        log_order("WEALTH_SNAPSHOT_INVALID",
                                  close_missing=close_snap.get("missing", []),
                                  open_missing=window_open_wealth.get("missing", []))
                        window_pnl = 0.0  # neutral, do not infer PnL from incomplete data
                    else:
                        wealth_delta = close_snap["total"] - window_open_wealth["total"]
                        window_pnl = wealth_delta if trades_this_window > 0 else 0.0
                    cumulative_pnl += window_pnl
                    log_order("WINDOW_CLOSE",
                              window=current_epoch, trades=trades_this_window,
                              wealth_before=round(window_open_wealth["total"], 4),
                              wealth_after=round(close_snap["total"], 4),
                              window_pnl=round(window_pnl, 4),
                              cum_pnl=round(cumulative_pnl, 4),
                              snap_valid=close_snap.get("valid"))
                    windows_done += 1
                    if args.stop_on_loss and close_snap.get("valid") and window_pnl < 0:
                        print(f"\n!!! STOP: window PnL ${window_pnl:+.4f} < 0. cumulative ${cumulative_pnl:+.4f}", flush=True)
                        break
                    if windows_done >= args.max_windows:
                        break

                current_epoch = window_epoch
                trades_this_window = 0
                window_has_unhedged = False

                # Change C: pre-cache market metadata in PARALLEL at window
                # open. v1 did three sequential HTTP fetches (~650ms total).
                # Parallel version runs them concurrently via the same pool
                # used for order fires (~300ms = slowest call). Each task
                # encapsulates its own retry+fallback so a failure on one
                # platform doesn't block the others.
                def _task_poly_market():
                    for attempt in range(3):
                        try:
                            m = fetch_poly_market(window_epoch)
                            if m:
                                return m
                        except Exception as e:
                            log_order("POLY_MARKET_FETCH_RETRY",
                                      attempt=attempt + 1,
                                      err=f"{type(e).__name__}: {e}")
                            time.sleep(2 ** attempt)
                    log_order("POLY_MARKET_FETCH_EXHAUSTED", epoch=window_epoch)
                    return None

                def _task_predict_market():
                    try:
                        pid = get_current_predict_market_id()
                        return pid, pt.get_market(pid)
                    except Exception as e:
                        log_order("PREDICT_MARKET_ERR", err=f"{type(e).__name__}: {e}")
                        return None, None

                def _task_lim_market():
                    try:
                        slug = get_current_limitless_market_slug()
                        lt.cache_market(slug)
                        return slug
                    except Exception as e:
                        log_order("LIM_MARKET_ERR", err=f"{type(e).__name__}: {e}")
                        return None

                # CRITICAL: LimitlessTrader is NOT thread-safe — its _run()
                # uses a single asyncio event loop shared across calls. Two
                # threads calling lt.cache_market() concurrently throw
                # "RuntimeError: this event loop is already running". So we
                # run ONLY Poly + Predict in parallel, then Limitless serially
                # on the main thread. The 150ms loss vs full-parallel is
                # acceptable vs the risk of breaking Limitless.
                t_precache = time.time()
                f_poly = pool.submit(_task_poly_market)
                f_pred = pool.submit(_task_predict_market)
                try:
                    poly_market = f_poly.result(timeout=15)
                except Exception as e:
                    poly_market = None
                    log_order("PRECACHE_POLY_TIMEOUT", err=f"{type(e).__name__}: {e}")
                try:
                    predict_market_id, predict_meta = f_pred.result(timeout=15)
                except Exception as e:
                    predict_market_id, predict_meta = None, None
                    log_order("PRECACHE_PREDICT_TIMEOUT", err=f"{type(e).__name__}: {e}")
                # Limitless: serial on main thread to avoid event-loop reentrancy
                lim_slug = _task_lim_market()
                log_order("MARKETS_PRECACHED",
                          ms=round((time.time() - t_precache) * 1000, 1),
                          poly=bool(poly_market), predict=bool(predict_meta),
                          lim=bool(lim_slug))
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

            # BLOCKER FIX (AI3): removed `or True` which was disabling fresh_lim
            if not (fresh_p and fresh_pr and fresh_lim and poly_market and predict_meta):
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
            sec_to_close = (current_epoch + WINDOW_SEC) - now_e if current_epoch else None
            best = pick_best(cands, sec_to_close=sec_to_close)

            # Phase 2 freshness gate: reject FAK if quotes are stale or
            # asymmetrically aged. Skip silently to avoid log spam — every
            # 200ms-stale poll cycle would log otherwise.
            if best:
                fresh_ok, fresh_reason, leg_ages = check_freshness(
                    best, p, pr, lim, int(time.time() * 1000)
                )
                if not fresh_ok:
                    # Log only when we'd otherwise have fired (cost passes
                    # threshold) so the log shows missed opportunities
                    log_order("STALE_QUOTE_REJECT",
                              dir=best["direction"], reason=fresh_reason,
                              ages_ms=leg_ages, cost=round(best["cost"], 4))
                    best = None
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
            # BLOCKER 2 FIX: do NOT set trade_fired_this_iter here. If both
            # legs fail (geoblock, timeout, network), we should sleep the full
            # POLL_SEC instead of immediately retrying and hammering the APIs.
            # The flag is set AFTER fills are verified, below.

            if parallel:
                log_order("FIRE_PARALLEL", dir=best["direction"], shares=shares)
                futures = [pool.submit(fire_leg, ctx, leg, shares) for leg in best["legs"]]
                results = []
                for f in futures:
                    try:
                        # Tight timeout: fail fast instead of hanging the bot
                        # on a single unresponsive platform.
                        results.append(f.result(timeout=ORDER_TIMEOUT_SEC))
                    except Exception as e:
                        results.append({"platform": "unknown", "ok": False,
                                        "size_filled": 0,
                                        "error": f"timeout/{type(e).__name__}: {e}"})
            else:
                thin_first = sorted(range(len(best["legs"])),
                                    key=lambda i: best["legs"][i]["depth_usd"])
                log_order("FIRE_SEQUENTIAL", dir=best["direction"], thin_first=thin_first[0],
                          shares=shares)
                first_idx = thin_first[0]
                second_idx = thin_first[1]
                first_res = fire_leg(ctx, best["legs"][first_idx], shares)
                # AI3 fix: clamp actual to requested shares to avoid second-leg
                # mirroring an overfill (rare but possible on aggressive markets).
                actual = min(first_res["size_filled"], shares) if first_res["ok"] else 0
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
            # BLOCKER 2 FIX: only skip POLL_SEC if at least one leg succeeded
            if any(r.get("ok") for r in results):
                trade_fired_this_iter = True

            # Change A: geoblock detection. If ANY platform returned 403 or a
            # "restricted in your region" error, the bot is at an IP that
            # the platform refuses to trade with. Stop wasting cycles in this
            # window. The watchdog/operator should consider moving to an
            # allowed region. We block-window but do NOT exit since same-oracle
            # pairs may still have a valid leg (and we may still want to log).
            blocked_platforms = [r.get("platform") for r in results if r and r.get("geoblock")]
            if blocked_platforms:
                log_order("GEOBLOCK_DETECTED", platforms=blocked_platforms,
                          note="window_blocked_consider_moving_to_allowed_region")
                window_has_unhedged = True

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
                under_idx = sizes.index(smaller)
                under_leg = best["legs"][under_idx]
                under_oracle = under_leg.get("oracle") or ORACLE_BY_PLATFORM.get(under_leg["platform"])
                # Find a third platform NOT used in the current pair whose oracle
                # matches the under-filled leg. Matching the oracle preserves the
                # hedge invariant: the original pair was {under_oracle outcome=X}
                # plus {other_oracle outcome=~X}. To complete the missing X,
                # the third leg must settle under the same oracle as under_leg.
                third_platform = None
                for plat in ("lim", "poly", "predict"):
                    if plat == under_leg["platform"]:
                        continue
                    if plat in [l["platform"] for l in best["legs"]]:
                        continue
                    if ORACLE_BY_PLATFORM.get(plat) != under_oracle:
                        continue
                    third_platform = plat
                    break
                if third_platform:
                    log_order("TOPUP_TRY", under=under_leg["platform"],
                              shortfall=round(shortfall, 4),
                              third=third_platform, oracle=under_oracle)
                    third_leg = None
                    if third_platform == "lim" and lim:
                        if under_leg["outcome"] in ("Up", "yes") and lim["up_ask"] > 0:
                            third_leg = {"platform": "lim", "oracle": "chainlink",
                                         "side": "BUY", "outcome": "yes",
                                         "ask": lim["up_ask"], "slug": lim["slug"]}
                        elif under_leg["outcome"] in ("Down", "no") and lim.get("down_ask", 0) > 0:
                            third_leg = {"platform": "lim", "oracle": "chainlink",
                                         "side": "BUY", "outcome": "no",
                                         "ask": lim["down_ask"], "slug": lim["slug"]}
                    elif third_platform == "poly" and poly_market:
                        if under_leg["outcome"] in ("Up", "yes") and p["ua"] > 0:
                            third_leg = {"platform": "poly", "oracle": "chainlink",
                                         "side": "BUY", "outcome": "Up",
                                         "ask": p["ua"], "token": poly_market["up_token"]}
                        elif under_leg["outcome"] in ("Down", "no") and p["da"] > 0:
                            third_leg = {"platform": "poly", "oracle": "chainlink",
                                         "side": "BUY", "outcome": "Down",
                                         "ask": p["da"], "token": poly_market["down_token"]}
                    elif third_platform == "predict" and predict_meta:
                        if under_leg["outcome"] in ("Up", "yes") and pr["yes_ask"] > 0:
                            third_leg = {"platform": "predict", "oracle": "binance",
                                         "side": "BUY", "outcome": "Up",
                                         "ask": pr["yes_ask"]}
                        elif under_leg["outcome"] in ("Down", "no") and pr["no_ask_implied"] > 0:
                            third_leg = {"platform": "predict", "oracle": "binance",
                                         "side": "BUY", "outcome": "Down",
                                         "ask": pr["no_ask_implied"]}
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
                    else:
                        log_order("TOPUP_SKIPPED", reason="no_valid_third_leg",
                                  oracle_needed=under_oracle)
                else:
                    log_order("TOPUP_SKIPPED", reason="no_third_platform_same_oracle",
                              oracle_needed=under_oracle)

            # Emergency-sell only if remaining excess > EXCESS_SELL_PCT of larger side
            if larger > 0 and (shortfall / larger) > EXCESS_SELL_PCT:
                over_idx = sizes.index(larger)
                over_leg = best["legs"][over_idx]
                log_order("EXCESS_OVER_THRESHOLD",
                          over=over_leg["platform"], larger=round(larger, 4),
                          smaller=round(smaller, 4), shortfall=round(shortfall, 4),
                          threshold=EXCESS_SELL_PCT)
                sell_res = emergency_sell(ctx, over_leg, shortfall)
                # BLOCKER FIX (AI1): do NOT clear window_has_unhedged based on
                # inferred fill from the sell response. Without authoritative
                # cross-platform position reconciliation, we cannot prove the
                # net exposure is actually zero. Keep the window blocked until
                # the next window opens fresh.
                if sell_res["ok"] and sell_res["size_filled"] >= shortfall * 0.95:
                    log_order("EMERGENCY_SELL_REPORTED_OK",
                              filled=round(sell_res["size_filled"], 4),
                              shortfall=round(shortfall, 4),
                              note="window_still_blocked_until_next_for_safety")
                else:
                    log_order("EMERGENCY_SELL_FAILED_KEEP_UNHEDGED",
                              filled=round(sell_res["size_filled"], 4),
                              remaining=round(shortfall - sell_res["size_filled"], 4))
                window_has_unhedged = True
                log_order("WINDOW_BLOCKED", reason="emergency_sell_attempted",
                          window=current_epoch)
            elif shortfall > 0:
                # Accept the small imbalance financially (cost of unwind > risk)
                # but BLOCK the rest of the window: residual exposure can compound
                # across trades in the same window if max-trades > 1.
                log_order("ACCEPT_SMALL_IMBALANCE_BLOCK_WINDOW",
                          larger=round(larger, 4), smaller=round(smaller, 4),
                          shortfall=round(shortfall, 4),
                          pct=round(shortfall/larger*100, 2))
                window_has_unhedged = True

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
            continue

        # Change E: only sleep the full POLL_SEC if no trade was fired this
        # iteration. After a fire, the next poll runs almost immediately to
        # catch follow-up opportunities, but a tiny floor sleep prevents
        # pathological busy-spin on a malformed feed or repeated failures.
        if trade_fired_this_iter:
            time.sleep(POST_TRADE_MIN_SLEEP)
        else:
            time.sleep(POLL_SEC)

    # BLOCKER FIX (AI1): ensure threads don't leak on exit
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    print(f"\n=== DONE. windows={windows_done} ===", flush=True)


if __name__ == "__main__":
    main()
