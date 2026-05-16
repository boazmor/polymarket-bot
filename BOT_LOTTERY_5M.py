#!/usr/bin/env python3
"""BOT_LOTTERY_5M — simple end-of-window lottery on Polymarket BTC 5min.

Strategy (per data analysis on 2.9 days, +$74-$82/day at $1.50/trade):
  - Window: last 60 seconds of each 5-min market (sec 240-299).
  - Buy a side at ask <= 0.05.
  - Only when |BTC - target_strike| is between $15 and $30.
  - One trade per (market, side). Hold to expiry.
  - No depth check: at $1.50 trade size we accept whatever fills.

Data source: tails the MULTI_COIN_RECORDER per-second CSV. No WebSocket
dependency — recorder already has Binance BTC + Polymarket up/down asks
+ Chainlink target in one synchronized row.

Outcome detection: same CSV; whichever side's bid hits >= 0.95 in the
last 60 sec is the winner.

Usage:
  python3 BOT_LOTTERY_5M.py --invest 1.5 [--dry-run]
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib3
from datetime import datetime, timezone

HTTP_POOL = urllib3.PoolManager(
    maxsize=4, block=False,
    retries=urllib3.Retry(connect=1, read=0, backoff_factor=0.05),
    headers={"User-Agent": "Mozilla/5.0"},
)

sys.path.insert(0, "/root")
from dotenv import load_dotenv
load_dotenv("/root/live/btc_5m/.env", override=True)

POLY_SAFE = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

# Strategy constants (from recorder analysis)
RECORDER_CSV = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
TRIGGER_SEC_START = 240
TRIGGER_SEC_END   = 299
MAX_PRICE         = 0.05
MIN_DISTANCE      = 15.0
MAX_DISTANCE      = 30.0
WIN_BID_THRESH    = 0.95
WINDOW_SEC        = 300
POLL_SEC          = 0.5
LIMIT_PRICE_PAD   = 0.01  # send buy at MAX_PRICE + pad so we accept slight slippage
OUTCOME_SETTLE_WAIT_SEC = 60


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


def tail_last_row():
    """Read the last data row of the recorder CSV. Returns dict or None."""
    try:
        with open(RECORDER_CSV, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 8192))
            chunk = f.read().decode(errors="ignore")
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        # Re-read full header from start of file (just once would be nicer but
        # this is fine at recorder scale).
        with open(RECORDER_CSV) as f:
            header = f.readline().strip().split(",")
        last = lines[-1].split(",")
        if len(last) < len(header):
            return None
        return dict(zip(header, last))
    except FileNotFoundError:
        return None


def fnum(s, default=0.0):
    try:
        return float(s) if s not in (None, "") else default
    except (TypeError, ValueError):
        return default


def fetch_poly_tokens(epoch):
    """Fetch UP/DOWN token IDs from Polymarket gamma API."""
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
        toks = m.get("clobTokenIds")
        if isinstance(toks, str):
            toks = json.loads(toks)
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if not toks or len(toks) != 2:
            return None
        up_token = toks[0] if outcomes[0].lower() == "up" else toks[1]
        down_token = toks[1] if outcomes[0].lower() == "up" else toks[0]
        return {"slug": slug, "up_token": up_token, "down_token": down_token}
    except Exception:
        return None


def lookup_outcome(market_epoch, settled_by_ts):
    """Scan recorder for winner of the given market_epoch.

    Returns 'UP', 'DOWN', or None if not yet determinable. Walks the
    end of the file backwards and checks bid columns.
    """
    try:
        with open(RECORDER_CSV) as f:
            header = f.readline().strip().split(",")
            cols = {name: i for i, name in enumerate(header)}
            ep_col = cols.get("market_epoch")
            sec_col = cols.get("sec_from_start")
            up_bid_col = cols.get("up_bid")
            down_bid_col = cols.get("down_bid")
            winner = None
            # Walk file; for size at this point a full scan is OK
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) <= max(ep_col, sec_col, up_bid_col, down_bid_col):
                    continue
                try:
                    ep = int(parts[ep_col] or 0)
                except ValueError:
                    continue
                if ep != market_epoch:
                    continue
                try:
                    sec = int(parts[sec_col] or 0)
                except ValueError:
                    continue
                if sec < 240:
                    continue
                ub = fnum(parts[up_bid_col])
                db = fnum(parts[down_bid_col])
                if ub >= WIN_BID_THRESH:
                    winner = "UP"
                elif db >= WIN_BID_THRESH:
                    winner = "DOWN"
            return winner
    except FileNotFoundError:
        return None


def place_poly_buy(poly_client, token_id, price, invest_usd, dry_run, log):
    """FAK buy on Polymarket. Returns (ok, filled_shares, fill_price)."""
    shares = round(invest_usd / price, 4)
    if dry_run:
        log("DRY_RUN_BUY", token=token_id[:14] + "..", price=price, shares=shares, invest=invest_usd)
        return True, shares, price
    try:
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
        args = OrderArgsV2(price=price, size=shares, side="BUY", token_id=str(token_id))
        resp = poly_client.create_and_post_order(args, order_type=OrderType.FAK)
        matched = float(resp.get("size_matched") or 0)
        avg_px = fnum(resp.get("avg_price"), price)
        return matched > 0, matched, avg_px
    except Exception as e:
        log("BUY_ERROR", token=token_id[:14] + "..", err=f"{type(e).__name__}: {e}")
        return False, 0, price


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--invest", type=float, default=1.5)
    ap.add_argument("--dry-run", action="store_true", default=False)
    args = ap.parse_args()

    tag = f"5m_${args.invest}"
    orders_path = f"/root/bot_lottery_{tag}_orders.csv"
    log = make_log_fn(orders_path)

    print(f"=== BOT_LOTTERY_5M starting at {now_iso()} ===", flush=True)
    print(f"invest=${args.invest}  dry_run={args.dry_run}  "
          f"trigger_sec={TRIGGER_SEC_START}-{TRIGGER_SEC_END}  "
          f"max_price<={MAX_PRICE}  distance=[${MIN_DISTANCE},${MAX_DISTANCE}]",
          flush=True)

    # Init poly client
    pk = os.environ["MY_PRIVATE_KEY"]
    if args.dry_run:
        poly_client = None
        print("--- DRY RUN: no client ---", flush=True)
    else:
        from py_clob_client_v2.client import ClobClient
        poly_client = ClobClient(host="https://clob.polymarket.com", key=pk,
                                  chain_id=137, signature_type=2, funder=POLY_SAFE)
        try:
            poly_client.set_api_creds(poly_client.create_or_derive_api_key())
        except Exception as e:
            print(f"poly api_key warning: {e}", flush=True)
        print("--- poly client connected ---", flush=True)

    # Per-market state
    current_epoch = None
    tokens_by_epoch = {}    # epoch -> {up_token, down_token, slug}
    bought = {}             # (epoch, side) -> {price, shares, ts}
    settled = set()         # epochs we've resolved

    log("START", invest=args.invest, dry_run=args.dry_run)

    while True:
        try:
            row = tail_last_row()
            if not row:
                time.sleep(POLL_SEC)
                continue
            try:
                ep = int(row.get("market_epoch") or 0)
                sec = int(row.get("sec_from_start") or -1)
            except (ValueError, TypeError):
                time.sleep(POLL_SEC)
                continue
            if not ep or sec < 0:
                time.sleep(POLL_SEC)
                continue

            # New window?
            if ep != current_epoch:
                log("NEW_WINDOW", epoch=ep, sec=sec, slug=f"btc-updown-5m-{ep}")
                # Resolve any previous epoch we held positions on
                for (held_ep, held_side), pos in list(bought.items()):
                    if held_ep < ep and held_ep not in settled:
                        # market should have settled by now
                        winner = lookup_outcome(held_ep, time.time())
                        if winner is None:
                            log("OUTCOME_PENDING", epoch=held_ep, side=held_side)
                            continue
                        outcome = "WIN" if winner == held_side else "LOSS"
                        pnl = (pos["shares"] * (1 - pos["price"])) if outcome == "WIN" else -(pos["shares"] * pos["price"])
                        log("OUTCOME", epoch=held_ep, side=held_side, winner=winner,
                            outcome=outcome, shares=round(pos["shares"], 4),
                            buy_price=round(pos["price"], 4), pnl=round(pnl, 3))
                        settled.add(held_ep)
                current_epoch = ep

            # Are we in trigger zone?
            if sec < TRIGGER_SEC_START or sec > TRIGGER_SEC_END:
                time.sleep(POLL_SEC)
                continue

            # Read live state
            btc = fnum(row.get("binance_price"))
            target = fnum(row.get("target_chainlink_at_open"))
            if btc <= 0 or target <= 0:
                time.sleep(POLL_SEC)
                continue
            dist = abs(btc - target)
            if dist < MIN_DISTANCE or dist > MAX_DISTANCE:
                time.sleep(POLL_SEC)
                continue
            up_ask = fnum(row.get("up_ask"))
            down_ask = fnum(row.get("down_ask"))

            # Cache tokens for this epoch (fetched once)
            if ep not in tokens_by_epoch:
                toks = fetch_poly_tokens(ep)
                if toks is None:
                    log("TOKENS_FETCH_FAIL", epoch=ep)
                    time.sleep(POLL_SEC)
                    continue
                tokens_by_epoch[ep] = toks
                log("TOKENS_READY", epoch=ep)

            toks = tokens_by_epoch[ep]
            buy_price = round(MAX_PRICE + LIMIT_PRICE_PAD, 4)

            for side, ask, token in [("UP", up_ask, toks["up_token"]),
                                       ("DOWN", down_ask, toks["down_token"])]:
                if (ep, side) in bought:
                    continue
                if not (0 < ask <= MAX_PRICE):
                    continue
                log("FIRE", epoch=ep, sec=sec, side=side, ask=ask, btc=btc,
                    target=target, dist=round(dist, 2), buy_price=buy_price)
                ok, filled, fill_px = place_poly_buy(
                    poly_client, token, buy_price, args.invest, args.dry_run, log)
                log("BUY_RESULT", epoch=ep, side=side, ok=ok,
                    filled_shares=round(filled, 4), fill_price=round(fill_px, 4))
                if ok and filled > 0:
                    bought[(ep, side)] = {"price": fill_px, "shares": filled, "ts": time.time()}

            time.sleep(POLL_SEC)
        except KeyboardInterrupt:
            log("STOPPED_KEYBOARD")
            break
        except Exception as e:
            log("LOOP_ERROR", err=f"{type(e).__name__}: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
