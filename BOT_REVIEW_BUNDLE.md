# Bot Code Bundle for External Code Review

This file concatenates the full source of the 3-platform arbitrage bot so it
can be shared as a single attachment to ChatGPT / Gemini / Claude / Perplexity
for a line-by-line code review.

Total: ~3500 lines across 8 files. Sizes per file:

- `arb_v5_3way_live.py` — 1311 lines — entry point for the 15-min bot
- `arb_v6_3way_live.py` — 1312 lines — entry point for the 1-hour bot (95% identical to V5; difference is WINDOW_SEC and market slug builder)
- `ws_feeds/state.py` — 127 lines — thread-safe SharedState for orderbook + freshness fields
- `ws_feeds/poly_ws.py` — 197 lines — Polymarket WebSocket client
- `ws_feeds/predict_ws.py` — 179 lines — Predict.fun WebSocket client
- `ws_feeds/limitless_ws.py` — 173 lines — Limitless WebSocket client
- `ws_feeds/runner.py` — 41 lines — starts the 3 WS threads
- `check_live_bots.sh` — 91 lines — supervisor / watchdog

Architecture summary:

```
main thread (bot)
   |
   +-- starts 3 daemon WS threads from ws_feeds.runner
   |       poly_ws  ─► STATE.update("poly",  ...)
   |       predict_ws ─► STATE.update("predict", ...)
   |       limitless_ws ─► STATE.update("lim", ...)
   |
   +-- per-window loop:
         1. fetch current market metadata for all 3 platforms in parallel
         2. inside POLL_SEC tight loop:
            - read snapshots from STATE
            - check_freshness on candidate legs
            - build cross-platform arb candidates
            - if shareable price + sufficient depth: fire orders
              - parallel fire if BOTH legs have ≥4× depth
              - else sequential thin-side-first
              - top-up from 3rd platform on shortfall
              - emergency sell excess if >10%
         3. window close: snapshot wealth, log PnL, stop-on-loss if negative
```

---

## File 1: arb_v5_3way_live.py (15-min market entry point)

```python
#!/usr/bin/env python3
"""arb_v5_3way_live_v3.py - Phase 3: direct WebSocket subscriptions.

v3 == v2 + the bot subscribes directly to each platform's WebSocket via
ws_feeds/ modules. The bot no longer reads latest.json files. Detect-to-fire
latency is expected to drop from 300-500ms (file polling) to 50-100ms
(direct WS).

Key differences from v2:
  - parse_predict_latest / parse_limitless_latest replaced by STATE.get()
  - parse_poly still reads CSV for target_chainlink_at_open (not in WS feed)
    but quote data comes from STATE.get("poly") not the CSV
  - check_freshness reads from STATE which has per-platform last_update_ms
  - 3 WS threads started at boot via ws_feeds.runner.start_all_feeds()
  - File-based recorders (LIMITLESS_RECORDER_WS, PREDICT_RECORDER, etc.) can
    still run in parallel for historical archival — they don't block v3.

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
from ws_feeds.state import STATE  # noqa: shared in-memory orderbook state
from ws_feeds.runner import start_all_feeds  # noqa

from dotenv import load_dotenv

ENV_PATH = "/root/live/btc_5m/.env"
load_dotenv(ENV_PATH, override=True)

P_DATA = "/root/data_btc_15m_research/combined_per_second.csv"
PR_LATEST_JSON = "/root/data_predict_btc_15m/latest.json"
PR_MARKETS = "/root/data_predict_btc_15m/markets.csv"
LIM_LATEST_JSON = "/root/data_limitless_btc_15m/latest.json"
LIM_MARKETS = "/root/data_limitless_btc_15m/markets.csv"

LIVE_TRADES = "/root/arb_v5_3way_live_trades.csv"
LIVE_ORDERS = "/root/arb_v5_3way_live_orders.csv"

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
LIM_MAX_AGE_SEC = 60
SETTLE_WAIT_SEC = 60
WINDOW_SEC = 900
# Phase 2 tight timeouts: fail-fast on unresponsive platforms so the bot
# doesn't hang for 30s waiting on a single bad API call. Polymarket order
# placement is normally 100-300ms; 1.5s allows 5x slowdown headroom.
ORDER_TIMEOUT_SEC = 0.8
POST_TRADE_MIN_SLEEP = 0.05
# Freshness model (rewritten 13/05 after 24h diagnostic):
# We trust silence. A quiet market = orderbook unchanged, not stale data.
# A leg is fresh iff:
#   1. Heartbeat: any message in the last HEARTBEAT_MAX_MS milliseconds.
#   2. Transit: last message's server->local delta < TRANSIT_MAX_MS.
# Per-platform empirical transit p99 (13/05): poly ~80, predict ~80, lim ~35.
HEARTBEAT_MAX_MS = 60000
TRANSIT_MAX_MS = {
    'poly': 300,
    'lim': 300,
    'predict': 300,
}

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
    """v3: orderbook from WebSocket-driven SharedState, target from CSV.

    target_chainlink_at_open is not in the WS feed — the recorder fetches it
    from Polymarket gamma API and writes it to CSV. We still need it for the
    bot's strike comparison logic, so we keep the CSV read but use it ONLY
    for the target field. Orderbook prices come from STATE.
    """
    target = 0.0
    slug = ""
    if row:
        d = to_dict(row, hdr)
        target = fnum(d.get("target_chainlink_at_open", d.get("target_price", 0)))
        slug = d.get("market_slug", "")
    book = STATE.get("poly")
    if not book.connected or book.last_update_ms == 0:
        return None
    return {
        "epoch": book.last_update_ms // 1000,
        "ts_ms": book.last_update_ms,
        "last_update_ms": book.last_update_ms,
        "server_ts_ms": book.server_ts_ms,
        "last_transit_ms": book.last_transit_ms,
        "connected": book.connected,
        "slug": slug,
        "tgt": target,
        "ua": book.best_ask,
        "da": book.no_best_ask,
        "ua_usd": book.ask_depth_usd,
        "da_usd": book.no_ask_depth_usd,
    }


def parse_predict_latest():
    """v3: read directly from SharedState updated by predict_ws thread.

    Returns None if WS is not connected or has no data yet — caller treats
    this the same as a missing file in v2 (skip iteration).
    """
    book = STATE.get("predict")
    if not book.connected or book.last_update_ms == 0:
        return None
    return {
        "epoch": book.last_update_ms // 1000,
        "ts_ms": book.last_update_ms,
        "last_update_ms": book.last_update_ms,
        "server_ts_ms": book.server_ts_ms,
        "last_transit_ms": book.last_transit_ms,
        "connected": book.connected,
        "market_id": book.market_id,
        "yes_ask": book.best_ask,
        "yes_bid": book.best_bid,
        "no_ask_implied": book.no_best_ask,
        "yes_ask_usd": book.ask_depth_usd,
        "no_ask_usd": book.no_ask_depth_usd,
    }


def parse_limitless_latest():
    """v3: read directly from SharedState updated by limitless_ws thread."""
    book = STATE.get("lim")
    if not book.connected or book.last_update_ms == 0:
        return None
    return {
        "epoch": book.last_update_ms // 1000,
        "ts_ms": book.last_update_ms,
        "last_update_ms": book.last_update_ms,
        "server_ts_ms": book.server_ts_ms,
        "last_transit_ms": book.last_transit_ms,
        "connected": book.connected,
        "market_id": book.market_id,
        "slug": book.slug,
        "up_ask": book.best_ask,
        "up_bid": book.best_bid,
        "up_ask_usd": book.ask_depth_usd,
        "up_bid_usd": book.bid_depth_usd,
        "down_ask": book.no_best_ask,
        "down_ask_usd": book.no_ask_depth_usd,
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
    """Per-leg health check: WS connection alive AND last message transit OK.

    A leg is fresh iff:
      - heartbeat_age = now - last_local_recv < HEARTBEAT_MAX_MS
      - last_transit_ms <= TRANSIT_MAX_MS[platform]

    Silence is trusted. If no message has arrived for 30s but the WS layer
    has not raised a disconnect, the orderbook is unchanged. The skew check
    has been removed; with the new semantics, both legs are either healthy
    or not, regardless of which one updates more frequently.

    Returns (ok, reason, [heartbeat_age_per_leg]).
    """
    snaps = {"poly": p, "predict": pr, "lim": lim}
    heartbeat_ages = []
    for leg in cand["legs"]:
        plat = leg["platform"]
        snap = snaps.get(plat)
        if not snap:
            return False, f"no_snap_{plat}", heartbeat_ages
        if not snap.get("connected", False):
            return False, f"disconnected_{plat}", heartbeat_ages
        last_local = snap.get("last_update_ms", 0)
        if last_local <= 0:
            return False, f"no_data_{plat}", heartbeat_ages
        heartbeat_age = now_ms - last_local
        heartbeat_ages.append(heartbeat_age)
        if heartbeat_age > HEARTBEAT_MAX_MS:
            return False, f"heartbeat_stale_{plat}_{heartbeat_age}ms", heartbeat_ages
        transit = snap.get("last_transit_ms", 0)
        plat_limit = TRANSIT_MAX_MS.get(plat, 300)
        if transit > plat_limit:
            return False, f"transit_slow_{plat}_{transit}ms", heartbeat_ages
    return True, "fresh", heartbeat_ages


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
    ap.add_argument("--max-trades-per-window", type=int, default=1)
    ap.add_argument("--invest", type=float, default=MAX_SIDE_USD)
    ap.add_argument("--stop-on-loss", action="store_true", default=True)
    ap.add_argument("--settle-wait-sec", type=int, default=SETTLE_WAIT_SEC)
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="run full strategy but log orders instead of submitting")
    args = ap.parse_args()

    print(f"=== arb_v5_3way_live (15min) starting at {now_iso()} ===", flush=True)
    print(f"invest_per_side=${args.invest}  max_trades_per_window={args.max_trades_per_window}", flush=True)

    api_key = os.environ["PREDICT_API_KEY"]
    pk = os.environ["MY_PRIVATE_KEY"]
    lim_key = os.environ["LIMITLESS_API_KEY"]
    lim_sec = os.environ["LIMITLESS_API_SECRET"]

    pt = PredictTrader(api_key, pk, log_path="/root/arb_v5_3way_live_predict.log")
    lt = LimitlessTrader(lim_key, lim_sec, pk, log_path="/root/arb_v5_3way_live_lim.log")

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

    # v3: start WebSocket feed threads. They run continuously and update
    # the shared STATE container. The main loop reads from STATE.
    # The market_holders dict is mutated by the main loop at every window
    # rollover; the WS providers re-read its values to know which market
    # to subscribe to.
    market_holders = {
        "poly_up_tokens": [],
        "poly_down_tokens": [],
        "predict_market_id": None,
        "lim_slug": None,
    }
    ws_threads = start_all_feeds(
        poly_tokens_provider=lambda: (
            market_holders["poly_up_tokens"],
            market_holders["poly_down_tokens"],
        ),
        predict_market_id_provider=lambda: market_holders["predict_market_id"],
        limitless_slug_provider=lambda: market_holders["lim_slug"],
        state=STATE,
    )
    print(f"--- {len(ws_threads)} WebSocket feeds started ---", flush=True)

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
                    print(f"\n>>> WINDOW {current_epoch} CLOSED. trades={trades_this_window}. waiting {args.settle_wait_sec}s...", flush=True)
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
                        try:
                            stop_path = f"/root/{os.path.basename(__file__).replace('.py','')}.stopped"
                            with open(stop_path, "w") as sf:
                                sf.write(f"{now_iso()} stop_on_loss window_pnl=${window_pnl:+.4f} cum_pnl=${cumulative_pnl:+.4f}\n")
                            log_order("STOP_FILE_WRITTEN", path=stop_path)
                        except Exception as e:
                            print(f"failed to write stop file: {e}", flush=True)
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

                # v3: push new market identifiers to the WS thread providers so
                # they re-subscribe to the rolled-over markets on the next
                # message cycle. Polymarket needs token IDs, Predict needs the
                # numeric market_id, Limitless needs the slug.
                if poly_market:
                    market_holders["poly_up_tokens"] = [poly_market["up_token"]]
                    market_holders["poly_down_tokens"] = [poly_market["down_token"]]
                if predict_market_id:
                    market_holders["predict_market_id"] = predict_market_id
                if lim_slug:
                    market_holders["lim_slug"] = lim_slug
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

```

---

## File 2: ws_feeds/state.py

```python
"""Shared in-memory orderbook state for the 3 platforms.

Replaces the file-polling architecture (latest.json -> tail_last_row) with a
thread-safe dict updated directly by each platform's WebSocket client.

The main bot reads via SharedState.get(platform) which returns a copy to
prevent torn reads. is_fresh(platform) gates trading on connection health
and quote age.
"""

import copy
import threading
import time
from dataclasses import dataclass


@dataclass
class PlatformBook:
    """Top-of-book snapshot for one platform's BTC market."""
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    no_best_ask: float = 0.0
    no_ask_depth_usd: float = 0.0
    market_id: str = ""
    slug: str = ""
    ts_ms: int = 0                 # alias for last_update_ms, kept for compat
    last_update_ms: int = 0        # local receive time of last message
    server_ts_ms: int = 0          # platform-stamped emit time of last message
    last_transit_ms: int = 0       # last_update_ms - server_ts_ms
    connected: bool = False
    error_count: int = 0


class SharedState:
    """Thread-safe container for all platforms' orderbook state."""

    def __init__(self):
        self._data = {
            "poly": PlatformBook(),
            "predict": PlatformBook(),
            "lim": PlatformBook(),
        }
        self._lock = threading.RLock()

    def update(self, platform: str, **kwargs):
        """Bulk-update fields. Called by WS client threads."""
        with self._lock:
            book = self._data[platform]
            for k, v in kwargs.items():
                if hasattr(book, k):
                    setattr(book, k, v)
            now_ms = int(time.time() * 1000)
            book.last_update_ms = now_ms
            book.ts_ms = now_ms
            # Compute transit only when server_ts is close enough to now to
            # represent network latency (not a stale book-state timestamp like
            # Predict.fun's updateTimestampMs after a reconnect snapshot).
            if 0 < book.server_ts_ms and (now_ms - book.server_ts_ms) < 10000:
                book.last_transit_ms = now_ms - book.server_ts_ms

    def get(self, platform: str) -> PlatformBook:
        """Return a COPY so the caller's reads aren't disturbed by a
        concurrent WS update mid-iteration."""
        with self._lock:
            return copy.copy(self._data[platform])

    def mark_disconnected(self, platform: str):
        """Mark stale during reconnect so the bot stops trading on that
        platform until data flows again. Zero out prices to avoid `ghost`
        liquidity reads."""
        with self._lock:
            self._data[platform].connected = False
            self._data[platform].best_bid = 0.0
            self._data[platform].best_ask = 0.0
            self._data[platform].bid_depth_usd = 0.0
            self._data[platform].ask_depth_usd = 0.0
            self._data[platform].error_count += 1

    def is_fresh(self, platform: str,
                 heartbeat_max_ms: int = 60000,
                 transit_max_ms: int = 300) -> bool:
        """Returns True iff: WS connected AND a recent heartbeat exists AND
        the most recent message's transit time (server-stamped to local) was
        within `transit_max_ms`.

        Quiet markets are TRUSTED: a silent period just means the orderbook
        did not change, NOT that data is stale. The heartbeat window only
        catches outright disconnects.
        """
        with self._lock:
            book = self._data[platform]
            if not book.connected:
                return False
            now_ms = int(time.time() * 1000)
            if now_ms - book.last_update_ms > heartbeat_max_ms:
                return False
            if book.last_transit_ms > transit_max_ms:
                return False
            return True

    def all_connected(self) -> bool:
        """True iff all three platforms have live WS connections."""
        with self._lock:
            return all(b.connected for b in self._data.values())

    def snapshot(self) -> dict:
        """Returns a flat dict of all platform states, for logging/debugging."""
        with self._lock:
            now_ms = int(time.time() * 1000)
            return {
                plat: {
                    "best_bid": b.best_bid,
                    "best_ask": b.best_ask,
                    "bid_depth_usd": b.bid_depth_usd,
                    "ask_depth_usd": b.ask_depth_usd,
                    "age_ms": (now_ms - b.last_update_ms) if b.last_update_ms else -1,
                    "connected": b.connected,
                    "error_count": b.error_count,
                }
                for plat, b in self._data.items()
            }


# Module-level singleton for the running bot
STATE = SharedState()

```

---

## File 3: ws_feeds/poly_ws.py

```python
"""Polymarket CLOB WebSocket client.

Subscribes to the `market` channel for specified token IDs and maintains
top-of-book state via the SharedState container.

Wire protocol (verified from py-clob-client + Polymarket docs):
  - URL: wss://ws-subscriptions-clob.polymarket.com/ws/market
  - Subscribe message:
      {"type": "MARKET", "markets": [token_id1, token_id2, ...]}
    Older docs use "assets_ids" — the v2 endpoint accepts "markets" too.
  - Event types received:
      "book"          full snapshot (on subscribe + on rollover)
      "price_change"  individual level update
      "tick_size_change" rare — recalibrate
  - No authentication required for public orderbook channel.

KNOWN BUG (silent freeze): the server occasionally accepts the connection
and subscription but never sends a book event. We detect this by timing
out on the first message after subscribe (5s) and forcing a reconnect.
"""

import asyncio
import json
import time
import websockets

POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


async def _handle_message(msg_str, token_to_side, state, platform="poly"):
    """Parse a single WS message and update SharedState.

    token_to_side maps each subscribed token_id to "up" or "down" so we know
    which side of the market the update affects.
    """
    try:
        data = json.loads(msg_str)
    except Exception:
        return

    # Polymarket sends list-wrapped messages in some channels
    if isinstance(data, list):
        for entry in data:
            await _handle_one(entry, token_to_side, state, platform)
    else:
        await _handle_one(data, token_to_side, state, platform)


async def _handle_one(entry, token_to_side, state, platform):
    event_type = entry.get("event_type")
    asset_id = entry.get("asset_id") or entry.get("market") or entry.get("token_id")
    side = token_to_side.get(str(asset_id))
    if not side:
        return  # event for a token we don't track

    server_ts = entry.get("timestamp")
    try:
        server_ts_ms = int(server_ts) if server_ts is not None else 0
    except (TypeError, ValueError):
        server_ts_ms = 0

    if event_type == "book":
        bids = entry.get("bids") or []
        asks = entry.get("asks") or []
        if side == "up":
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            bid_depth = float(bids[0]["size"]) * best_bid if bids else 0.0
            ask_depth = float(asks[0]["size"]) * best_ask if asks else 0.0
            state.update(platform,
                         best_bid=best_bid, best_ask=best_ask,
                         bid_depth_usd=bid_depth, ask_depth_usd=ask_depth,
                         server_ts_ms=server_ts_ms, connected=True)

    elif event_type == "price_change":
        changes = entry.get("changes") or [entry]
        for ch in changes:
            try:
                price = float(ch.get("price", 0))
                size = float(ch.get("size", 0))
                ch_side = (ch.get("side") or "").upper()
            except (TypeError, ValueError):
                continue
            book = state.get(platform)
            if side == "up":
                if ch_side == "SELL" and abs(price - book.best_ask) < 1e-9:
                    state.update(platform,
                                 ask_depth_usd=size * price,
                                 server_ts_ms=server_ts_ms, connected=True)
                elif ch_side == "BUY" and abs(price - book.best_bid) < 1e-9:
                    state.update(platform,
                                 bid_depth_usd=size * price,
                                 server_ts_ms=server_ts_ms, connected=True)


async def poly_ws_main(tokens_provider, state):
    """Main WS loop with reconnect and silent-freeze detection.

    tokens_provider: callable returning (up_tokens, down_tokens) tuples.
    Called at every connect AND between message receives, so when the
    15-min market rolls over we automatically re-subscribe.

    Backwards-compat: if tokens_provider is a list-pair tuple, treat it
    as static.
    """
    backoff = 1.0
    max_backoff = 30.0

    def get_tokens():
        if callable(tokens_provider):
            up, down = tokens_provider()
            return list(up or []), list(down or [])
        # Static legacy form: (up_list, down_list)
        return list(tokens_provider[0]), list(tokens_provider[1])

    while True:
        try:
            up_tokens, down_tokens = get_tokens()
            if not up_tokens or not down_tokens:
                # Markets not loaded yet — wait
                await asyncio.sleep(1)
                continue
            all_tokens = up_tokens + down_tokens
            token_to_side = {}
            for t in up_tokens:
                token_to_side[str(t)] = "up"
            for t in down_tokens:
                token_to_side[str(t)] = "down"

            async with websockets.connect(POLY_WS_URL,
                                          ping_interval=20,
                                          ping_timeout=10) as ws:
                # Official Polymarket CLOB market-channel subscribe format
                sub_msg = {
                    "assets_ids": [str(t) for t in all_tokens],
                    "type": "market",
                    "custom_feature_enabled": True,
                }
                await ws.send(json.dumps(sub_msg))
                current_up = up_tokens[0]

                # Silent-freeze guard: expect a book event within 5s.
                # If not, force reconnect.
                try:
                    first = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    await _handle_message(first, token_to_side, state)
                except asyncio.TimeoutError:
                    print("[poly_ws] silent freeze on first message, reconnecting")
                    raise ConnectionError("silent_freeze_first_msg")

                backoff = 1.0  # successful connect
                async for msg in ws:
                    await _handle_message(msg, token_to_side, state)
                    # Check for market rollover — re-subscribe if token set
                    # changed. We compare the first up_token only since the
                    # bot uses one market per window.
                    if callable(tokens_provider):
                        new_up, new_down = tokens_provider()
                        if new_up and new_up[0] != current_up:
                            print(f"[poly_ws] market rollover detected; reconnecting")
                            break  # exit inner loop, reconnect with new tokens
        except Exception as e:
            print(f"[poly_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("poly")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def run_in_thread(tokens_provider, state):
    """Helper for runner.py to start this client in a dedicated thread.

    tokens_provider: callable returning (up_tokens_list, down_tokens_list).
    """
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(poly_ws_main(tokens_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="poly_ws")
    t.start()
    return t


if __name__ == "__main__":
    # Standalone test: pass two token IDs as CLI args
    import sys
    from state import STATE
    if len(sys.argv) < 3:
        print("usage: poly_ws.py <up_token> <down_token>")
        sys.exit(1)
    up_t, down_t = sys.argv[1], sys.argv[2]
    asyncio.run(poly_ws_main(lambda: ([up_t], [down_t]), STATE))

```

---

## File 4: ws_feeds/predict_ws.py

```python
"""Predict.fun WebSocket client.

Subscribes to predictOrderbook/{market_id} topic on the public WS endpoint
and maintains top-of-book state via the SharedState container.

Wire protocol (verified from existing PREDICT_RECORDER_15M_V2.py which has
been running stable for weeks):
  - URL: wss://ws.predict.fun/ws
  - Subscribe:
      {"method": "subscribe", "requestId": <int>, "params": ["predictOrderbook/{market_id}"]}
  - Event format:
      {"type": "M",
       "topic": "predictOrderbook/{market_id}",
       "data": {"marketId": ..., "bids": [[price, size], ...], "asks": [[price, size], ...]}}
  - Heartbeat: handled by websockets library's TCP ping (ping_interval=20).
    The existing recorder has run for weeks without explicit message-level
    heartbeats, so this is sufficient in practice.
  - No authentication required for public orderbook channel.
"""

import asyncio
import json
import time
import websockets

PREDICT_WS_URL = "wss://ws.predict.fun/ws"


async def predict_ws_main(market_id_provider, state):
    """Main WS loop with reconnect.

    market_id_provider: a callable that returns the current Predict.fun
    market ID at any time. This lets the bot rotate markets every 15 min
    without restarting the WS thread; we re-subscribe whenever the active
    market ID changes.
    """
    backoff = 1.0
    max_backoff = 30.0
    req_id_counter = 1

    while True:
        current_market_id = None
        try:
            async with websockets.connect(PREDICT_WS_URL,
                                          open_timeout=10,
                                          ping_interval=20,
                                          ping_timeout=10) as ws:
                # Subscribe to the current market
                current_market_id = market_id_provider()
                if not current_market_id:
                    await asyncio.sleep(1)
                    continue
                topic = f"predictOrderbook/{current_market_id}"
                req_id_counter += 1
                sub_msg = {"method": "subscribe", "requestId": req_id_counter,
                           "params": [topic]}
                await ws.send(json.dumps(sub_msg))

                # Silent-freeze guard: expect a message (sub-ack or data)
                # within 5 seconds.
                try:
                    first = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    _process_msg(first, current_market_id, state)
                except asyncio.TimeoutError:
                    print("[predict_ws] silent freeze, reconnecting")
                    raise ConnectionError("silent_freeze_first_msg")

                backoff = 1.0

                while True:
                    # Periodically check if the market has rolled over.
                    # If so, re-subscribe to the new market_id.
                    new_market_id = market_id_provider()
                    if new_market_id and new_market_id != current_market_id:
                        # Unsubscribe old + subscribe new
                        try:
                            old_topic = f"predictOrderbook/{current_market_id}"
                            req_id_counter += 1
                            await ws.send(json.dumps({
                                "method": "unsubscribe",
                                "requestId": req_id_counter,
                                "params": [old_topic]
                            }))
                        except Exception:
                            pass
                        current_market_id = new_market_id
                        new_topic = f"predictOrderbook/{current_market_id}"
                        req_id_counter += 1
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "requestId": req_id_counter,
                            "params": [new_topic]
                        }))
                        state.update("predict", market_id=str(current_market_id))

                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue
                    _process_msg(msg, current_market_id, state)

        except Exception as e:
            print(f"[predict_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("predict")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def _process_msg(msg_str, expected_market_id, state):
    try:
        d = json.loads(msg_str)
    except Exception:
        return

    # Subscribe acks have no "type" field — ignore
    if d.get("type") != "M":
        return
    if not d.get("topic", "").startswith("predictOrderbook/"):
        return

    data = d.get("data") or {}
    market_id = data.get("marketId")
    if expected_market_id is not None and str(market_id) != str(expected_market_id):
        return  # event for a market we no longer track

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    # Predict.fun format: [[price, size], ...]
    yes_bid = float(bids[0][0]) if bids else 0.0
    yes_bid_size = float(bids[0][1]) if bids else 0.0
    yes_ask = float(asks[0][0]) if asks else 0.0
    yes_ask_size = float(asks[0][1]) if asks else 0.0

    yes_ask_usd = sum(float(a[0]) * float(a[1]) for a in asks)
    yes_bid_usd = sum(float(b[0]) * float(b[1]) for b in bids)

    raw_server_ts = data.get("updateTimestampMs")
    try:
        server_ts_ms = int(raw_server_ts) if raw_server_ts is not None else 0
    except (TypeError, ValueError):
        server_ts_ms = 0

    state.update("predict",
                 best_bid=yes_bid,
                 best_ask=yes_ask,
                 bid_depth_usd=round(yes_bid_usd, 4),
                 ask_depth_usd=round(yes_ask_usd, 4),
                 no_best_ask=round(1.0 - yes_bid, 4) if yes_bid > 0 else 0,
                 no_ask_depth_usd=round(yes_bid_usd, 4),
                 server_ts_ms=server_ts_ms,
                 market_id=str(market_id),
                 connected=True)


def run_in_thread(market_id_provider, state):
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(predict_ws_main(market_id_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="predict_ws")
    t.start()
    return t


if __name__ == "__main__":
    import sys
    from state import STATE
    if len(sys.argv) < 2:
        print("usage: predict_ws.py <market_id>")
        sys.exit(1)
    mid = int(sys.argv[1])
    asyncio.run(predict_ws_main(lambda: mid, STATE))

```

---

## File 5: ws_feeds/limitless_ws.py

```python
"""Limitless Exchange WebSocket client.

Uses limitless-sdk's WebSocketClient (Socket.IO under the hood) to subscribe
to the orderbook channel for one market slug. Updates SharedState on every
orderbookUpdate event.

Why the SDK and not raw websockets:
  - Limitless uses Socket.IO protocol, not raw WS
  - The SDK handles the handshake, reconnect, and event-name mapping
  - Our limitless_trader.py already depends on the SDK, so no new dependency

Threading:
  Limitless trader uses its own asyncio loop for order placement. The WS
  client here uses a SEPARATE loop in its own thread. They never share
  the loop — order placement runs on the main thread, WS on its own.
"""

import asyncio
import time

from limitless_sdk.websocket import WebSocketClient, WebSocketConfig


async def limitless_ws_main(slug_provider, state):
    """Main WS loop with reconnect and resubscribe on market rollover.

    slug_provider: callable returning the current market slug (so we can
    re-subscribe when the 15-min/1h market rolls over).
    """
    current_slug = None
    backoff = 1.0
    max_backoff = 30.0

    while True:
        try:
            ws = WebSocketClient(WebSocketConfig(auto_reconnect=True))

            @ws.on("orderbookUpdate")
            async def on_ob(data):
                try:
                    msg_slug = data.get("marketSlug", "") if isinstance(data, dict) else ""
                    if current_slug and msg_slug and msg_slug != current_slug:
                        return  # event for a different market
                    ob = data.get("orderbook") or {} if isinstance(data, dict) else {}
                    bids = ob.get("bids") or []
                    asks = ob.get("asks") or []

                    # parse_size_to_usd-style normalization: API may return
                    # raw size_units (shares * 1e6) or already-decimal shares.
                    def to_shares(s):
                        try:
                            v = float(s)
                        except (TypeError, ValueError):
                            return 0.0
                        return v / 1e6 if v > 1e4 else v

                    if bids:
                        best_bid = float(bids[0].get("price") or 0)
                        best_bid_shares = to_shares(bids[0].get("size"))
                        best_bid_usd = best_bid * best_bid_shares
                        total_bid_usd = sum(
                            float(b.get("price") or 0) * to_shares(b.get("size"))
                            for b in bids
                        )
                    else:
                        best_bid = 0.0
                        best_bid_usd = 0.0
                        total_bid_usd = 0.0
                    if asks:
                        best_ask = float(asks[0].get("price") or 0)
                        best_ask_shares = to_shares(asks[0].get("size"))
                        best_ask_usd = best_ask * best_ask_shares
                        total_ask_usd = sum(
                            float(a.get("price") or 0) * to_shares(a.get("size"))
                            for a in asks
                        )
                    else:
                        best_ask = 0.0
                        best_ask_usd = 0.0
                        total_ask_usd = 0.0

                    no_best_ask = round(1.0 - best_bid, 4) if best_bid > 0 else 0
                    no_ask_depth = best_bid_usd  # same liquidity, complement price

                    server_ts_ms = 0
                    iso_ts = data.get("timestamp") if isinstance(data, dict) else None
                    if iso_ts:
                        try:
                            from datetime import datetime as _dt
                            server_ts_ms = int(_dt.fromisoformat(
                                iso_ts.replace("Z", "+00:00")).timestamp() * 1000)
                        except Exception:
                            server_ts_ms = 0

                    state.update("lim",
                                 best_bid=best_bid, best_ask=best_ask,
                                 bid_depth_usd=round(best_bid_usd, 4),
                                 ask_depth_usd=round(best_ask_usd, 4),
                                 no_best_ask=no_best_ask,
                                 no_ask_depth_usd=round(no_ask_depth, 4),
                                 server_ts_ms=server_ts_ms,
                                 slug=msg_slug or current_slug or "",
                                 connected=True)
                except Exception as e:
                    print(f"[lim_ws] event handler error: {type(e).__name__}: {e}")

            await ws.connect()
            print("[lim_ws] connected")
            current_slug = slug_provider()
            if current_slug:
                await ws.subscribe("subscribe_market_prices",
                                   {"marketSlugs": [current_slug]})

            backoff = 1.0

            while True:
                await asyncio.sleep(5)
                # Check for market rollover
                new_slug = slug_provider()
                if new_slug and new_slug != current_slug:
                    if current_slug:
                        try:
                            await ws.unsubscribe("subscribe_market_prices",
                                                 {"marketSlugs": [current_slug]})
                        except Exception:
                            pass
                    current_slug = new_slug
                    await ws.subscribe("subscribe_market_prices",
                                       {"marketSlugs": [current_slug]})
                    state.update("lim", slug=current_slug)

                # Zombie detection: if last update > 30s ago, force reconnect.
                snap = state.get("lim")
                now_ms = int(time.time() * 1000)
                if snap.last_update_ms and (now_ms - snap.last_update_ms > 30000):
                    print("[lim_ws] zombie detected (no update >30s), reconnecting")
                    raise ConnectionError("zombie_no_data")

        except Exception as e:
            print(f"[lim_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("lim")
            try:
                await ws.disconnect()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def run_in_thread(slug_provider, state):
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(limitless_ws_main(slug_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="lim_ws")
    t.start()
    return t


if __name__ == "__main__":
    import sys
    from state import STATE
    if len(sys.argv) < 2:
        print("usage: limitless_ws.py <market_slug>")
        sys.exit(1)
    slug = sys.argv[1]
    asyncio.run(limitless_ws_main(lambda: slug, STATE))

```

---

## File 6: ws_feeds/runner.py

```python
"""Orchestrates the 3 WebSocket threads alongside the main sync bot.

Usage from arb_v5_3way_live_v3.py:

    from ws_feeds.state import STATE
    from ws_feeds.runner import start_all_feeds

    feeds = start_all_feeds(
        poly_up_tokens=[up_token],
        poly_down_tokens=[down_token],
        predict_market_id_provider=lambda: current_predict_market_id,
        limitless_slug_provider=lambda: current_lim_slug,
        state=STATE,
    )
    # main bot loop reads STATE.get(platform) / STATE.is_fresh(platform)
    # feeds is a list of Thread objects for diagnostics
"""

from ws_feeds.poly_ws import run_in_thread as poly_run
from ws_feeds.predict_ws import run_in_thread as predict_run
from ws_feeds.limitless_ws import run_in_thread as lim_run


def start_all_feeds(poly_tokens_provider,
                    predict_market_id_provider,
                    limitless_slug_provider,
                    state):
    """Start the 3 WS client threads. Each runs in its own asyncio loop
    and updates the shared `state` container.

    All three providers are callables. The main bot updates the underlying
    container as markets roll over; the WS clients re-read at each iteration
    and re-subscribe automatically.

    Returns a list of Thread objects (already started, daemon=True).
    """
    threads = []
    threads.append(poly_run(poly_tokens_provider, state))
    threads.append(predict_run(predict_market_id_provider, state))
    threads.append(lim_run(limitless_slug_provider, state))
    return threads

```

---

## File 7: check_live_bots.sh

```bash
#!/bin/bash
# Watchdog for live trading bots.
#   Helsinki: arb_v5_live (15-min markets), arb_v7_live (5-min markets)
#   Hetzner:  arb_v6_live (1-hour markets)
#
# Each bot is checked for:
#   1. Process alive (pgrep -f "python3 -u $script")
#   2. Log file updated within $max_stale_sec
# If dead or stale, kill remnants, archive old log, restart under screen.
#
# Append OK / RESTART events to /root/bot_watchdog.log.
# Pass the server flag (helsinki | hetzner) to control which bots to check.
#
# Usage:  bash /root/check_live_bots.sh helsinki
#         bash /root/check_live_bots.sh hetzner

NOW=$(date +%s)
LOG=/root/bot_watchdog.log
HOST=${1:-helsinki}

check_bot() {
  local name=$1
  local script=$2
  local args=$3
  local logfile=$4
  local max_stale_sec=$5

  # Stop file convention: when the bot writes /root/<script-without-.py>.stopped
  # (e.g. arb_v5_3way_live.stopped) the watchdog must leave it alone. The bot
  # writes this when stop-on-loss triggers; resume by deleting the file.
  local stop_file="/root/${script%.py}.stopped"
  if [ -f "$stop_file" ]; then
    echo "$(date -u +%FT%TZ) SKIP_STOPPED $name stop_file=$stop_file" >> "$LOG"
    return
  fi

  local pids=$(pgrep -f "python3.* $script" | tr '\n' ',')
  local restart_reason=""

  if [ -z "$pids" ]; then
    restart_reason="DEAD_PROCESS"
  elif [ -f "/root/$logfile" ]; then
    local mtime
    mtime=$(stat -c %Y "/root/$logfile")
    local age=$((NOW - mtime))
    if [ "$age" -gt "$max_stale_sec" ]; then
      restart_reason="STALE_LOG_${age}s_over_${max_stale_sec}s"
    fi
  else
    restart_reason="NO_LOG_FILE"
  fi

  if [ -n "$restart_reason" ]; then
    echo "$(date -u +%FT%TZ) RESTART $name reason=$restart_reason old_pids=$pids" >> "$LOG"
    pkill -f "python3.* $script" 2>/dev/null
    sleep 2
    screen -wipe > /dev/null 2>&1
    if [ -f "/root/$logfile" ]; then
      mv "/root/$logfile" "/root/${logfile}.bak.${NOW}"
    fi
    screen -dmS "$name" bash -c "cd /root && python3 -u $script $args > $logfile 2>&1"
    sleep 3
    local new_pid
    new_pid=$(pgrep -f "python3.* $script" | head -1)
    echo "$(date -u +%FT%TZ) RESTART $name DONE new_pid=$new_pid" >> "$LOG"
  else
    echo "$(date -u +%FT%TZ) OK $name pid=$pids" >> "$LOG"
  fi
}

case "$HOST" in
  usa)
    # Reverted 13/05 - Polymarket geoblocks US. Live bots now on Europe.
    # US server kept for Limitless recorder + future use only.
    ;;
  helsinki)
    check_bot arb_v5_3way_live arb_v5_3way_live.py "--max-trades-per-window 1 --invest 7.0" arb_v5_3way_live_v1.log 600
    # V7 paused 13/05 pending freshness model rollout. Has 2 unhedged Predict
    # fills from 11/05 that need manual reconciliation before resuming.
    # check_bot arb_v7_live arb_v7_live.py "--max-trades-per-window 1 --invest 7.0" arb_v7_live_v6.log 600
    ;;
  hetzner)
    check_bot arb_v6_3way_live arb_v6_3way_live.py "--max-trades-per-window 2 --invest 7.0" arb_v6_3way_live_v1.log 1800
    check_bot arb_v5_3way arb_v5_3way.py "" arb_v5_3way_run.log 900
    check_bot arb_v6_3way arb_v6_3way.py "" arb_v6_3way_run.log 1800
    ;;
  *)
    echo "Usage: $0 usa|helsinki|hetzner" >&2
    exit 1
    ;;
esac

```

---

Note: arb_v6_3way_live.py is omitted from this bundle because it is 95% identical to V5. The only differences are: WINDOW_SEC=3600 (vs 900), and the Polymarket market slug is computed by date string ("bitcoin-up-or-down-may-13-2026-3pm-et") rather than epoch.
